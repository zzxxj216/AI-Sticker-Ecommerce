"""V2 pipeline web routes — mounted under ``/v2``.

This module owns all V2 namespace routes. Module-by-module pages will be
filled in across W2 / W3 / W4; W1 ships:
- /v2                          dashboard with link-out cards
- /v2/system/ai-logs           live view of ai_call_logs
- /v2/system/scheduled-jobs    live view of scheduled_jobs + per-job status
- /v2/{module placeholders}    clickable but show "implemented in Wn"

The router is created with ``prefix='/v2'`` so it mounts cleanly onto the
existing FastAPI app without disturbing the V1 routes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.core.logger import get_logger

logger = get_logger("web.v2")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Module-level templates instance (FastAPI app shares the same dir).
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(prefix="/v2", tags=["v2"])

_CN_TZ = timezone(timedelta(hours=8))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DEFAULT_DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=_CN_TZ).strftime("%m-%d %H:%M:%S")


def _fmt_duration(seconds: int | float | None) -> str:
    if not seconds and seconds != 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _fmt_interval(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        return f"{seconds // 3600} 小时"
    return f"{seconds // 86400} 天"


# Default job intervals — mirror scripts/scheduler.py registrations.
JOB_INTERVALS_S = {
    "tk_metrics_refresh":         2 * 60 * 60,
    "tk_video_publish_dispatch":          60,
    "tkshop_status_sync":        30 * 60,
}


# ----------------------------------------------------------------------
# Dashboard (/v2)
# ----------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def v2_dashboard(request: Request):
    conn = _open_db()
    try:
        stats = {
            "hot_topics":        conn.execute("SELECT COUNT(*) FROM hot_topics").fetchone()[0],
            "topic_plans":       conn.execute("SELECT COUNT(*) FROM topic_plans").fetchone()[0],
            "packs":             conn.execute("SELECT COUNT(*) FROM packs").fetchone()[0],
            "tk_videos":         conn.execute("SELECT COUNT(*) FROM tk_videos").fetchone()[0],
            "tk_video_metrics":  conn.execute("SELECT COUNT(*) FROM tk_video_metrics").fetchone()[0],
            "tkshop_products":   conn.execute("SELECT COUNT(*) FROM tkshop_products").fetchone()[0],
        }
        rows = conn.execute(
            """
            SELECT id, service, model, task, status,
                   prompt_tokens, completion_tokens, cost_estimate, created_at
            FROM ai_call_logs
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
        recent_ai_logs = []
        for r in rows:
            d = dict(r)
            d["created_human"] = _fmt_ts(d["created_at"])
            recent_ai_logs.append(d)
    finally:
        conn.close()

    return templates.TemplateResponse(
        "v2_dashboard.html",
        {
            "request": request,
            "page_title": "V2 工作台",
            "stats": stats,
            "recent_ai_logs": recent_ai_logs,
        },
    )


# ----------------------------------------------------------------------
# System / observability
# ----------------------------------------------------------------------

@router.get("/system/ai-logs", response_class=HTMLResponse)
def v2_ai_logs(request: Request, status: str | None = None, limit: int = 100):
    conn = _open_db()
    try:
        where = ""
        params: tuple = ()
        if status:
            where = "WHERE status = ?"
            params = (status,)
        rows = conn.execute(
            f"""
            SELECT id, service, model, task, related_table, related_id, status,
                   prompt_tokens, completion_tokens, latency_ms, cost_estimate,
                   error, created_at
              FROM ai_call_logs
              {where}
             ORDER BY id DESC
             LIMIT ?
            """,
            params + (limit,),
        ).fetchall()
        logs = []
        for r in rows:
            d = dict(r)
            d["created_human"] = _fmt_ts(d["created_at"])
            logs.append(d)
        total = conn.execute("SELECT COUNT(*) FROM ai_call_logs").fetchone()[0]
    finally:
        conn.close()

    return templates.TemplateResponse(
        "v2_system_ai_logs.html",
        {
            "request": request,
            "page_title": "AI 调用日志",
            "logs": logs,
            "stats": {"total": total},
            "filter_status": status or "",
        },
    )


@router.get("/system/scheduled-jobs", response_class=HTMLResponse)
def v2_scheduled_jobs(request: Request, limit: int = 100):
    conn = _open_db()
    try:
        # Per-job summary
        jobs = []
        now = int(datetime.now().timestamp())
        for name, interval in JOB_INTERVALS_S.items():
            last = conn.execute(
                """
                SELECT finished_at FROM scheduled_jobs
                WHERE job_name = ? AND status = 'ok' AND finished_at IS NOT NULL
                ORDER BY finished_at DESC LIMIT 1
                """,
                (name,),
            ).fetchone()
            last_ts = last[0] if last else None
            next_ts = (last_ts + interval) if last_ts else None

            recent_24h = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok,
                    SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS err
                FROM scheduled_jobs
                WHERE job_name = ? AND started_at >= ?
                """,
                (name, now - 86400),
            ).fetchone()
            ok_n = recent_24h["ok"] or 0
            err_n = recent_24h["err"] or 0
            total_n = ok_n + err_n
            sr = f"{ok_n}/{total_n}" if total_n else "—"

            jobs.append({
                "name": name,
                "interval_human": _fmt_interval(interval),
                "last_run_human": _fmt_ts(last_ts),
                "next_run_human": _fmt_ts(next_ts) if next_ts and next_ts > now else ("立即" if next_ts else ""),
                "success_rate_human": sr,
            })

        # Recent runs
        rows = conn.execute(
            """
            SELECT id, job_name, started_at, finished_at, status,
                   affected_rows, error
              FROM scheduled_jobs
             ORDER BY id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        runs = []
        for r in rows:
            d = dict(r)
            d["started_human"] = _fmt_ts(d["started_at"])
            d["finished_human"] = _fmt_ts(d["finished_at"]) if d["finished_at"] else ""
            if d["finished_at"] and d["started_at"]:
                d["duration_human"] = _fmt_duration(d["finished_at"] - d["started_at"])
            else:
                d["duration_human"] = ""
            runs.append(d)
    finally:
        conn.close()

    return templates.TemplateResponse(
        "v2_system_scheduled_jobs.html",
        {
            "request": request,
            "page_title": "调度任务",
            "jobs": jobs,
            "runs": runs,
        },
    )


# ----------------------------------------------------------------------
# Module placeholders (real pages land in W2 / W3 / W4)
# ----------------------------------------------------------------------

def _placeholder(
    request: Request,
    *,
    title: str,
    section_tag: str,
    planned_in: str,
    related_table: str = "",
    description: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        "v2_placeholder.html",
        {
            "request": request,
            "page_title": title,
            "title": title,
            "section_tag": section_tag,
            "planned_in": planned_in,
            "related_table": related_table,
            "description": description,
        },
    )


@router.get("/hot-topics", response_class=HTMLResponse)
def v2_hot_topics(request: Request):
    return _placeholder(
        request,
        title="热点池",
        section_tag="A.1 · W2",
        planned_in="W2 / A.1 任务",
        related_table="hot_topics",
        description="多源 web 搜索（OpenAI / Tavily / Perplexity / TikTok CC）。"
                    "现已有 348 条历史 tk_hashtags 种入库，待 W2 接入新搜索源。",
    )


@router.get("/topic-plans", response_class=HTMLResponse)
def v2_topic_plans(request: Request):
    return _placeholder(
        request,
        title="题材规划",
        section_tag="A.2 · W2",
        planned_in="W2 / A.2 任务",
        related_table="topic_plans, pack_series",
        description="人工选热点 + 配数量 → AI 两步生成（主模型自由产出 + 提取模型转 JSON）。",
    )


@router.get("/packs", response_class=HTMLResponse)
def v2_packs(request: Request):
    return _placeholder(
        request,
        title="卡包管理",
        section_tag="A.4 · W3",
        planned_in="W3 / A.4 任务",
        related_table="packs, pack_series, pack_previews, pack_stickers",
    )


@router.get("/gallery", response_class=HTMLResponse)
def v2_gallery(request: Request):
    return _placeholder(
        request,
        title="卡包画廊",
        section_tag="A.4 · W3",
        planned_in="W3 / A.4 任务",
        related_table="packs",
    )


@router.get("/videos", response_class=HTMLResponse)
def v2_videos(request: Request):
    return _placeholder(
        request,
        title="TK 视频管理",
        section_tag="B.1 · W3",
        planned_in="W3 / B.1 任务",
        related_table="tk_videos",
        description="人工上传视频 + AI 文案 + Hashtag（强制英文）+ 定时发送。",
    )


@router.get("/analytics", response_class=HTMLResponse)
def v2_analytics(request: Request):
    return _placeholder(
        request,
        title="视频数据看板",
        section_tag="B.3 · W3",
        planned_in="W3 / B.3 任务",
        related_table="tk_video_metrics",
        description="2h 自动刷新（独立调度脚本已就绪），按互动率排序。",
    )


@router.get("/products", response_class=HTMLResponse)
def v2_products(request: Request):
    return _placeholder(
        request,
        title="TKShop 产品",
        section_tag="C · W4",
        planned_in="W4 / C.1-C.4 任务",
        related_table="tkshop_products, tkshop_product_images, tkshop_publish_logs",
        description="详情页生成（英文）+ 主图管理（人工 + AI）+ 一键发布 + 状态看板。",
    )
