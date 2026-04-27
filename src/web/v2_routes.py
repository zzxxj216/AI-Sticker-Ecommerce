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

import json as _json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.core.logger import get_logger
from src.services.hot_topics import KNOWN_PROVIDERS as HOT_TOPIC_PROVIDERS, get_hot_topic_service
from src.services.preview_gen import get_preview_gen_service
from src.services.topic_plans import get_topic_plan_service
from src.services.topic_plans.service import (
    DEFAULT_PREVIEWS_PER_SERIES,
    DEFAULT_SERIES_COUNT,
    DEFAULT_STICKERS_PER_PREVIEW,
)

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


def _path_to_v2_url(disk_path: str) -> str:
    """Map an ``output/packs/...`` disk path to its ``/v2-outputs/...`` URL.

    Returns empty string if ``disk_path`` doesn't live under output/packs.
    """
    if not disk_path:
        return ""
    p = disk_path.replace("\\", "/")
    marker = "output/packs/"
    idx = p.find(marker)
    if idx < 0:
        return ""
    return "/v2-outputs/" + p[idx + len(marker):]


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
def v2_hot_topics_list(
    request: Request,
    source: str = "all",
    status: str = "all",
    q: str = "",
    limit: int = 100,
):
    svc = get_hot_topic_service()
    topics, total = svc.list_topics(
        source=source if source != "all" else None,
        status=status if status != "all" else None,
        query_substring=q or None,
        limit=limit,
    )
    for t in topics:
        t["fetched_human"] = _fmt_ts(t.get("fetched_at"))

    source_stats = svc.source_stats()
    stats_totals = {"total": sum(s["total"] for s in source_stats.values())}

    # Pull last_search_summary from session-style query string (search redirects here)
    last_search_summary = None
    if request.query_params.get("inserted") is not None:
        last_search_summary = {
            "inserted_total": int(request.query_params.get("inserted", "0")),
            "by_provider": _json.loads(request.query_params.get("by_provider", "{}") or "{}"),
            "errors":      _json.loads(request.query_params.get("errors", "{}") or "{}"),
        }

    return templates.TemplateResponse(
        "v2_hot_topics.html",
        {
            "request": request,
            "page_title": "热点池",
            "topics": topics,
            "total": total,
            "limit": limit,
            "known_providers": HOT_TOPIC_PROVIDERS,
            "source_stats": source_stats,
            "stats_totals": stats_totals,
            "source_filter": source,
            "status_filter": status,
            "default_query": request.query_params.get("default_query", ""),
            "last_search_summary": last_search_summary,
        },
    )


@router.post("/hot-topics/search")
async def v2_hot_topics_search(request: Request):
    """Run a multi-source search synchronously and redirect back to the list."""
    form = await request.form()
    query = (form.get("query") or "").strip()
    region = (form.get("region") or "US").strip()
    try:
        max_results = int(form.get("max_results") or 10)
    except ValueError:
        max_results = 10
    providers = form.getlist("providers")

    if not query:
        raise HTTPException(status_code=400, detail="query required")

    svc = get_hot_topic_service()
    summary = svc.search_and_persist(
        query=query, providers=providers, region=region, max_results=max_results,
    )
    logger.info("hot_topics search '%s' inserted %d rows across %s",
                query, summary["inserted_total"], list(summary["by_provider"].keys()))

    qs = (
        f"?inserted={summary['inserted_total']}"
        f"&by_provider={_json.dumps(summary['by_provider'])}"
        f"&errors={_json.dumps(summary['errors'])}"
        f"&default_query={query}"
    )
    return RedirectResponse(url=f"/v2/hot-topics{qs}", status_code=303)


@router.get("/hot-topics/{topic_id:int}", response_class=HTMLResponse)
def v2_hot_topic_detail(request: Request, topic_id: int):
    svc = get_hot_topic_service()
    topic = svc.get_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="topic not found")
    topic["fetched_human"] = _fmt_ts(topic.get("fetched_at"))
    raw_payload_json = _json.dumps(topic.get("raw_payload") or {}, ensure_ascii=False, indent=2)
    return templates.TemplateResponse(
        "v2_hot_topic_detail.html",
        {
            "request": request,
            "page_title": f"#{topic_id} {topic['topic_name'][:40]}",
            "topic": topic,
            "raw_payload_json": raw_payload_json,
        },
    )


@router.post("/hot-topics/{topic_id:int}/status")
async def v2_hot_topic_set_status(request: Request, topic_id: int):
    form = await request.form()
    new_status = (form.get("status") or "").strip()
    svc = get_hot_topic_service()
    try:
        ok = svc.update_status(topic_id, new_status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="topic not found")
    return RedirectResponse(url=f"/v2/hot-topics/{topic_id}", status_code=303)


@router.get("/topic-plans", response_class=HTMLResponse)
def v2_topic_plans_list(
    request: Request,
    status: str = "all",
    topic_id: int | None = None,
    limit: int = 50,
):
    svc = get_topic_plan_service()
    plans, total = svc.list_plans(
        topic_id=topic_id,
        status=status if status != "all" else None,
        limit=limit,
    )
    for p in plans:
        p["updated_human"] = _fmt_ts(p.get("updated_at"))
    return templates.TemplateResponse(
        "v2_topic_plans.html",
        {
            "request": request,
            "page_title": "题材规划",
            "plans": plans,
            "total": total,
            "status_filter": status,
            "topic_id_filter": topic_id,
        },
    )


@router.get("/topic-plans/new", response_class=HTMLResponse)
def v2_topic_plan_new(request: Request, topic_id: int | None = None):
    """Show the generate-plan form. Requires a ?topic_id= query param."""
    topic = None
    if topic_id is not None:
        topic = get_hot_topic_service().get_topic(topic_id)
        if not topic:
            raise HTTPException(status_code=404, detail=f"hot_topic #{topic_id} not found")
    return templates.TemplateResponse(
        "v2_topic_plan_new.html",
        {
            "request": request,
            "page_title": "新建题材方案",
            "topic": topic,
            "defaults": {
                "series_count":         DEFAULT_SERIES_COUNT,
                "previews_per_series":  DEFAULT_PREVIEWS_PER_SERIES,
                "stickers_per_preview": DEFAULT_STICKERS_PER_PREVIEW,
            },
        },
    )


@router.post("/topic-plans/generate")
async def v2_topic_plan_generate(request: Request):
    """Run two-step generation synchronously, then redirect to detail."""
    form = await request.form()
    try:
        topic_id = int(form.get("topic_id") or 0)
        series_count = int(form.get("series_count") or DEFAULT_SERIES_COUNT)
        previews_per_series = int(form.get("previews_per_series") or DEFAULT_PREVIEWS_PER_SERIES)
        stickers_per_preview = int(form.get("stickers_per_preview") or DEFAULT_STICKERS_PER_PREVIEW)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad number: {e}")
    extra_brief = (form.get("extra_brief") or "").strip()

    if topic_id <= 0:
        raise HTTPException(status_code=400, detail="topic_id required")

    svc = get_topic_plan_service()
    try:
        result = svc.generate_plan(
            topic_id,
            series_count=series_count,
            previews_per_series=previews_per_series,
            stickers_per_preview=stickers_per_preview,
            extra_brief=extra_brief,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("topic plan generation failed")
        raise HTTPException(status_code=500, detail=f"generation failed: {e}")

    logger.info(
        "topic_plan #%d generated for topic #%d (status=%s, series=%d, main_chars=%d)",
        result["plan_id"], topic_id, result["status"],
        result["series_count"], result["main_chars"],
    )
    return RedirectResponse(url=f"/v2/topic-plans/{result['plan_id']}", status_code=303)


@router.get("/topic-plans/{plan_id:int}", response_class=HTMLResponse)
def v2_topic_plan_detail(request: Request, plan_id: int):
    svc = get_topic_plan_service()
    plan = svc.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="plan not found")
    plan["updated_human"] = _fmt_ts(plan.get("updated_at"))
    series_payload_json = _json.dumps(plan.get("series_payload") or {}, ensure_ascii=False, indent=2)
    return templates.TemplateResponse(
        "v2_topic_plan_detail.html",
        {
            "request": request,
            "page_title": f"方案 #{plan_id}",
            "plan": plan,
            "series_payload_json": series_payload_json,
        },
    )


@router.post("/topic-plans/{plan_id:int}/series/{series_id:int}/toggle")
async def v2_topic_plan_series_toggle(
    request: Request, plan_id: int, series_id: int,
):
    form = await request.form()
    is_selected = (form.get("is_selected") or "0").strip() == "1"
    svc = get_topic_plan_service()
    ok = svc.toggle_series_selection(series_id, is_selected)
    if not ok:
        raise HTTPException(status_code=404, detail="series not found")
    return RedirectResponse(url=f"/v2/topic-plans/{plan_id}", status_code=303)


# ----------------------------------------------------------------------
# A.3 — preview generation (series + preview pages, generate route)
# ----------------------------------------------------------------------

@router.get("/series/{series_id:int}", response_class=HTMLResponse)
def v2_series_detail(request: Request, series_id: int):
    svc = get_preview_gen_service()
    series = svc.get_series_with_previews(series_id)
    if not series:
        raise HTTPException(status_code=404, detail="series not found")
    briefs_by_idx = {int(b.get("preview_idx") or 0): b
                     for b in (series.get("metadata") or {}).get("preview_briefs", [])}
    enriched = []
    ok_n = err_n = pending_n = generating_n = 0
    for p in series["previews"]:
        d = dict(p)
        d["image_url"] = _path_to_v2_url(d.get("image_path") or "")
        d["generated_human"] = _fmt_ts(d.get("generated_at"))
        d["brief"] = briefs_by_idx.get(int(d["preview_idx"]), {})
        enriched.append(d)
        st = d["generation_status"]
        if   st == "ok":         ok_n += 1
        elif st == "error":      err_n += 1
        elif st == "generating": generating_n += 1
        else:                    pending_n += 1
    series["previews"] = enriched
    series["status_summary"] = {
        "ok": ok_n, "error": err_n,
        "generating": generating_n, "pending": pending_n,
        "total": len(enriched),
    }
    series["expected_count"] = len(briefs_by_idx)
    return templates.TemplateResponse(
        "v2_series_detail.html",
        {
            "request": request,
            "page_title": f"系列 #{series_id} {series['series_name']}",
            "series": series,
        },
    )


@router.post("/series/{series_id:int}/prepare-previews")
async def v2_series_prepare_previews(request: Request, series_id: int):
    """Mint pack_uid + create pending pack_previews rows from briefs."""
    svc = get_preview_gen_service()
    try:
        result = svc.prepare_previews(series_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info("series #%d prepare: %s", series_id, result)
    return RedirectResponse(url=f"/v2/series/{series_id}", status_code=303)


@router.post("/series/{series_id:int}/generate-previews")
async def v2_series_generate_previews(request: Request, series_id: int):
    """Run image_generate for every pending preview in this series (sync wait)."""
    svc = get_preview_gen_service()
    # Auto-prepare in case operator skipped that step.
    try:
        svc.prepare_previews(series_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    result = svc.generate_pending_for_series(series_id)
    logger.info(
        "series #%d generate-previews: %d ok / %d error of %d attempted",
        series_id, result["ok"], result["error"], result["attempted"],
    )
    return RedirectResponse(url=f"/v2/series/{series_id}", status_code=303)


@router.get("/previews/{preview_id:int}", response_class=HTMLResponse)
def v2_preview_detail(request: Request, preview_id: int):
    svc = get_preview_gen_service()
    p = svc.get_preview(preview_id)
    if not p:
        raise HTTPException(status_code=404, detail="preview not found")
    p["image_url"] = _path_to_v2_url(p.get("image_path") or "")
    p["generated_human"] = _fmt_ts(p.get("generated_at"))
    for s in p.get("stickers", []):
        s["image_url"] = _path_to_v2_url(s.get("image_path") or "")
        s["generated_human"] = _fmt_ts(s.get("generated_at"))
    return templates.TemplateResponse(
        "v2_preview_detail.html",
        {
            "request": request,
            "page_title": f"预览 #{preview_id}",
            "preview": p,
        },
    )


@router.post("/previews/{preview_id:int}/regenerate")
async def v2_preview_regenerate(request: Request, preview_id: int):
    svc = get_preview_gen_service()
    p = svc.get_preview(preview_id)
    if not p:
        raise HTTPException(status_code=404, detail="preview not found")
    result = svc.regenerate_preview(preview_id)
    logger.info("preview #%d regenerate → %s", preview_id, result["status"])
    return RedirectResponse(url=f"/v2/previews/{preview_id}", status_code=303)


@router.post("/previews/{preview_id:int}/split")
async def v2_preview_split(request: Request, preview_id: int):
    """Auto-prepare + run image_edit for every sticker brief under this preview."""
    svc = get_preview_gen_service()
    try:
        prep = svc.prepare_stickers(preview_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if prep.get("skipped_reason"):
        logger.warning("preview #%d split skipped: %s", preview_id, prep["skipped_reason"])
        return RedirectResponse(url=f"/v2/previews/{preview_id}", status_code=303)
    result = svc.split_pending_for_preview(preview_id)
    logger.info(
        "preview #%d split: %d ok / %d error of %d attempted",
        preview_id, result["ok"], result["error"], result["attempted"],
    )
    return RedirectResponse(url=f"/v2/previews/{preview_id}", status_code=303)


@router.get("/stickers/{sticker_id:int}", response_class=HTMLResponse)
def v2_sticker_detail(request: Request, sticker_id: int):
    svc = get_preview_gen_service()
    s = svc.get_sticker(sticker_id)
    if not s:
        raise HTTPException(status_code=404, detail="sticker not found")
    s["image_url"] = _path_to_v2_url(s.get("image_path") or "")
    s["preview_image_url"] = _path_to_v2_url(s.get("preview_image_path") or "")
    s["generated_human"] = _fmt_ts(s.get("generated_at"))
    return templates.TemplateResponse(
        "v2_sticker_detail.html",
        {
            "request": request,
            "page_title": f"贴纸 #{sticker_id}",
            "sticker": s,
        },
    )


@router.post("/stickers/{sticker_id:int}/regenerate")
async def v2_sticker_regenerate(request: Request, sticker_id: int):
    svc = get_preview_gen_service()
    if not svc.get_sticker(sticker_id):
        raise HTTPException(status_code=404, detail="sticker not found")
    result = svc.regenerate_sticker(sticker_id)
    logger.info("sticker #%d regenerate → %s", sticker_id, result["status"])
    return RedirectResponse(url=f"/v2/stickers/{sticker_id}", status_code=303)


@router.post("/stickers/{sticker_id:int}/toggle")
async def v2_sticker_toggle(request: Request, sticker_id: int):
    form = await request.form()
    is_selected = (form.get("is_selected") or "0").strip() == "1"
    svc = get_preview_gen_service()
    if not svc.toggle_sticker_selection(sticker_id, is_selected):
        raise HTTPException(status_code=404, detail="sticker not found")
    return RedirectResponse(url=f"/v2/stickers/{sticker_id}", status_code=303)


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
