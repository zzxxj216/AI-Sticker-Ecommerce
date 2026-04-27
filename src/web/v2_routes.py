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

import shutil
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
from src.services.packs import get_pack_service
from src.services.preview_gen import get_preview_gen_service
from src.services.tk_videos import get_tk_video_service
from src.services.tkshop import get_tkshop_service
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
    "tk_dispatch_status_poll":    5 * 60,
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


# ----------------------------------------------------------------------
# A.4 — pack aggregate (promote series → pack, list, detail, gallery)
# ----------------------------------------------------------------------

@router.get("/packs", response_class=HTMLResponse)
def v2_packs_list(request: Request, status: str = "all", limit: int = 50):
    svc = get_pack_service()
    packs, total = svc.list_packs(
        status=status if status != "all" else None,
        limit=limit,
    )
    for p in packs:
        p["created_human"] = _fmt_ts(p.get("created_at"))
        p["cover_url"] = _path_to_v2_url(p.get("cover_image_path") or "")
    return templates.TemplateResponse(
        "v2_packs.html",
        {
            "request": request,
            "page_title": "卡包管理",
            "packs": packs,
            "total": total,
            "status_filter": status,
        },
    )


@router.get("/gallery", response_class=HTMLResponse)
def v2_gallery(request: Request):
    """Visual grid of all active packs (cover + name + sticker count)."""
    svc = get_pack_service()
    packs, total = svc.list_packs(status="active", limit=200)
    for p in packs:
        p["cover_url"] = _path_to_v2_url(p.get("cover_image_path") or "")
        p["created_human"] = _fmt_ts(p.get("created_at"))
    return templates.TemplateResponse(
        "v2_gallery.html",
        {
            "request": request,
            "page_title": "卡包画廊",
            "packs": packs,
            "total": total,
        },
    )


@router.post("/packs/from-series/{series_id:int}")
async def v2_pack_create(request: Request, series_id: int):
    """Promote a pack_series into a packs row. Idempotent."""
    form = await request.form()
    display_name = (form.get("display_name") or "").strip() or None
    cover_id_str = (form.get("cover_sticker_id") or "").strip()
    cover_sticker_id = int(cover_id_str) if cover_id_str.isdigit() else None
    svc = get_pack_service()
    try:
        result = svc.create_pack_from_series(
            series_id, display_name=display_name,
            cover_sticker_id=cover_sticker_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("pack create from series #%d → %s", series_id, result)
    return RedirectResponse(url=f"/v2/packs/{result['pack_id']}", status_code=303)


@router.get("/packs/{pack_id:int}", response_class=HTMLResponse)
def v2_pack_detail(request: Request, pack_id: int):
    svc = get_pack_service()
    pack = svc.get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    pack["created_human"] = _fmt_ts(pack.get("created_at"))
    pack["cover_url"] = _path_to_v2_url(pack.get("cover_image_path") or "")
    for s in pack.get("stickers", []):
        s["image_url"] = _path_to_v2_url(s.get("image_path") or "")
    for p in pack.get("previews", []):
        p["image_url"] = _path_to_v2_url(p.get("image_path") or "")
    return templates.TemplateResponse(
        "v2_pack_detail.html",
        {
            "request": request,
            "page_title": f"卡包 {pack['display_name']}",
            "pack": pack,
        },
    )


@router.post("/packs/{pack_id:int}/cover")
async def v2_pack_set_cover(request: Request, pack_id: int):
    form = await request.form()
    sticker_id_str = (form.get("sticker_id") or "").strip()
    if not sticker_id_str.isdigit():
        raise HTTPException(status_code=400, detail="sticker_id required")
    svc = get_pack_service()
    try:
        svc.set_cover(pack_id, int(sticker_id_str))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/v2/packs/{pack_id}", status_code=303)


@router.post("/packs/{pack_id:int}/rename")
async def v2_pack_rename(request: Request, pack_id: int):
    form = await request.form()
    new_name = (form.get("display_name") or "").strip()
    svc = get_pack_service()
    try:
        ok = svc.rename(pack_id, new_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="pack not found")
    return RedirectResponse(url=f"/v2/packs/{pack_id}", status_code=303)


@router.post("/packs/{pack_id:int}/status")
async def v2_pack_set_status(request: Request, pack_id: int):
    form = await request.form()
    new_status = (form.get("status") or "").strip()
    svc = get_pack_service()
    try:
        ok = svc.update_status(pack_id, new_status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="pack not found")
    return RedirectResponse(url=f"/v2/packs/{pack_id}", status_code=303)


@router.post("/packs/{pack_id:int}/refresh-total")
async def v2_pack_refresh_total(request: Request, pack_id: int):
    svc = get_pack_service()
    try:
        cnt = svc.refresh_total_stickers(pack_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info("pack #%d total_stickers refreshed to %d", pack_id, cnt)
    return RedirectResponse(url=f"/v2/packs/{pack_id}", status_code=303)


# ----------------------------------------------------------------------
# B.1 — TK videos (manual upload, AI caption two-step gen, schedule)
# ----------------------------------------------------------------------

@router.get("/videos", response_class=HTMLResponse)
def v2_videos_list(
    request: Request,
    pack_id: int | None = None,
    publish_status: str = "all",
    limit: int = 100,
):
    svc = get_tk_video_service()
    videos, total = svc.list_videos(
        pack_id=pack_id,
        publish_status=publish_status if publish_status != "all" else None,
        limit=limit,
    )
    for v in videos:
        v["scheduled_human"] = _fmt_ts(v.get("scheduled_at"))
        v["published_human"] = _fmt_ts(v.get("published_at"))
        v["pack_cover_url"] = _path_to_v2_url(v.get("pack_cover") or "")
    return templates.TemplateResponse(
        "v2_videos.html",
        {
            "request": request,
            "page_title": "TK 视频管理",
            "videos": videos,
            "total": total,
            "pack_id_filter": pack_id,
            "status_filter": publish_status,
        },
    )


@router.get("/videos/new", response_class=HTMLResponse)
def v2_video_new(request: Request, pack_id: int | None = None):
    pack = None
    if pack_id is not None:
        pack = get_pack_service().get_pack(pack_id)
        if not pack:
            raise HTTPException(status_code=404, detail=f"pack #{pack_id} not found")
        pack["cover_url"] = _path_to_v2_url(pack.get("cover_image_path") or "")
    # Pull recent active packs as picker options when no pack_id provided.
    packs, _ = get_pack_service().list_packs(status="active", limit=50)
    for p in packs:
        p["cover_url"] = _path_to_v2_url(p.get("cover_image_path") or "")
    return templates.TemplateResponse(
        "v2_video_new.html",
        {
            "request": request,
            "page_title": "新建 TK 视频",
            "pack": pack,
            "active_packs": packs,
        },
    )


@router.post("/videos")
async def v2_video_create(request: Request):
    """Create video row, then save uploaded file (multipart/form-data)."""
    form = await request.form()
    try:
        pack_id = int(form.get("pack_id") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="pack_id must be int")
    if pack_id <= 0:
        raise HTTPException(status_code=400, detail="pack_id required")
    account_open_id = (form.get("account_open_id") or "").strip()
    one_liner = (form.get("video_one_liner") or "").strip()
    upload = form.get("video_file")

    svc = get_tk_video_service()
    try:
        video_id = svc.create_video(
            pack_id, account_open_id=account_open_id,
            video_one_liner=one_liner,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # If a file was uploaded, save it via PackStore.
    if upload is not None and getattr(upload, "filename", ""):
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / upload.filename
        with open(tmp, "wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        try:
            svc.save_video_file(video_id, tmp, original_filename=upload.filename)
        finally:
            try:
                tmp.unlink()
                tmp.parent.rmdir()
            except Exception:
                pass

    return RedirectResponse(url=f"/v2/videos/{video_id}", status_code=303)


@router.get("/videos/{video_id:int}", response_class=HTMLResponse)
def v2_video_detail(request: Request, video_id: int):
    svc = get_tk_video_service()
    v = svc.get_video(video_id)
    if not v:
        raise HTTPException(status_code=404, detail="video not found")
    v["scheduled_human"] = _fmt_ts(v.get("scheduled_at"))
    v["published_human"] = _fmt_ts(v.get("published_at"))
    v["pack_cover_url"] = _path_to_v2_url(v.get("cover_image_path") or "")
    v["video_url"] = _path_to_v2_url(v.get("local_video_path") or "")
    return templates.TemplateResponse(
        "v2_video_detail.html",
        {
            "request": request,
            "page_title": f"视频 #{video_id}",
            "video": v,
        },
    )


@router.post("/videos/{video_id:int}/generate-caption")
async def v2_video_generate_caption(request: Request, video_id: int):
    svc = get_tk_video_service()
    try:
        result = svc.generate_caption(video_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("generate_caption failed")
        raise HTTPException(status_code=500, detail=str(e))
    logger.info("video #%d caption: extract_ok=%s caption=%r",
                video_id, result["extract_ok"], result.get("caption", "")[:80])
    return RedirectResponse(url=f"/v2/videos/{video_id}", status_code=303)


@router.post("/videos/{video_id:int}/edit-caption")
async def v2_video_edit_caption(request: Request, video_id: int):
    form = await request.form()
    caption = (form.get("caption") or "").strip()
    raw_tags = (form.get("hashtags") or "").strip()
    # Accept either newline-separated or space-separated; preserve "#"
    tags: list[str] = []
    for t in raw_tags.replace("\r", "").splitlines():
        for piece in t.split():
            piece = piece.strip()
            if piece:
                tags.append(piece if piece.startswith("#") else f"#{piece}")
    svc = get_tk_video_service()
    if not svc.update_caption_manual(video_id, caption, tags):
        raise HTTPException(status_code=404, detail="video not found")
    return RedirectResponse(url=f"/v2/videos/{video_id}", status_code=303)


@router.post("/videos/{video_id:int}/edit-meta")
async def v2_video_edit_meta(request: Request, video_id: int):
    form = await request.form()
    one_liner = (form.get("video_one_liner") or "").strip()
    account = (form.get("account_open_id") or "").strip()
    svc = get_tk_video_service()
    svc.update_one_liner(video_id, one_liner)
    svc.update_account(video_id, account)
    return RedirectResponse(url=f"/v2/videos/{video_id}", status_code=303)


@router.post("/videos/{video_id:int}/schedule")
async def v2_video_schedule(request: Request, video_id: int):
    form = await request.form()
    raw = (form.get("scheduled_at") or "").strip()
    if not raw or raw == "0":
        ts = 0
    else:
        # Accept "YYYY-MM-DDTHH:MM" (HTML datetime-local input, local time)
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(raw)
            ts = int(dt.timestamp())
        except ValueError:
            try:
                ts = int(raw)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"bad scheduled_at: {raw!r}")
    svc = get_tk_video_service()
    if not svc.schedule_video(video_id, ts):
        raise HTTPException(status_code=404, detail="video not found")
    return RedirectResponse(url=f"/v2/videos/{video_id}", status_code=303)


@router.get("/analytics", response_class=HTMLResponse)
def v2_analytics(request: Request):
    """Published videos with latest metrics — sortable by interaction rate."""
    svc = get_tk_video_service()
    videos, total = svc.list_videos(
        publish_status="published", limit=200,
        with_latest_metrics=True,
    )
    for v in videos:
        v["pack_cover_url"] = _path_to_v2_url(v.get("pack_cover") or "")
        v["published_human"] = _fmt_ts(v.get("published_at"))
        m = v.get("latest_metrics") or {}
        v["view_count"] = m.get("view_count", 0)
        v["like_count"] = m.get("like_count", 0)
        v["comment_count"] = m.get("comment_count", 0)
        v["share_count"] = m.get("share_count", 0)
        v["metrics_fetched_human"] = _fmt_ts(m.get("fetched_at")) if m else ""
        # interaction rate = (likes + comments + shares) / max(views, 1)
        engagements = v["like_count"] + v["comment_count"] + v["share_count"]
        v["interaction_rate"] = (engagements / v["view_count"]) if v["view_count"] else 0.0
    videos.sort(key=lambda x: x["interaction_rate"], reverse=True)
    return templates.TemplateResponse(
        "v2_analytics.html",
        {
            "request": request,
            "page_title": "TK 视频数据看板",
            "videos": videos,
            "total": total,
        },
    )


# ----------------------------------------------------------------------
# C — TKShop products (detail gen, image mgmt, publish, status)
# ----------------------------------------------------------------------

@router.get("/products", response_class=HTMLResponse)
def v2_products_list(
    request: Request,
    pack_id: int | None = None,
    publish_status: str = "all",
    limit: int = 100,
):
    svc = get_tkshop_service()
    products, total = svc.list_products(
        pack_id=pack_id,
        publish_status=publish_status if publish_status != "all" else None,
        limit=limit,
    )
    for p in products:
        p["created_human"] = _fmt_ts(p.get("created_at"))
        p["published_human"] = _fmt_ts(p.get("published_at"))
        p["pack_cover_url"] = _path_to_v2_url(p.get("pack_cover") or "")
    return templates.TemplateResponse(
        "v2_products.html",
        {
            "request": request,
            "page_title": "TKShop 产品",
            "products": products,
            "total": total,
            "pack_id_filter": pack_id,
            "status_filter": publish_status,
        },
    )


@router.post("/products/from-pack/{pack_id:int}")
async def v2_product_create(request: Request, pack_id: int):
    svc = get_tkshop_service()
    try:
        pid = svc.create_product_from_pack(pack_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return RedirectResponse(url=f"/v2/products/{pid}", status_code=303)


@router.get("/products/{product_id:int}", response_class=HTMLResponse)
def v2_product_detail(request: Request, product_id: int):
    svc = get_tkshop_service()
    p = svc.get_product(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="product not found")
    p["created_human"] = _fmt_ts(p.get("created_at"))
    p["published_human"] = _fmt_ts(p.get("published_at"))
    p["pack_cover_url"] = _path_to_v2_url(p.get("cover_image_path") or "")
    for img in p.get("images", []):
        img["url"] = _path_to_v2_url(img.get("local_path") or "")
        img["created_human"] = _fmt_ts(img.get("created_at"))
    for log in p.get("publish_logs", []):
        log["created_human"] = _fmt_ts(log.get("created_at"))
    return templates.TemplateResponse(
        "v2_product_detail.html",
        {
            "request": request,
            "page_title": f"产品 #{product_id}",
            "product": p,
        },
    )


@router.post("/products/{product_id:int}/generate-detail")
async def v2_product_generate_detail(request: Request, product_id: int):
    svc = get_tkshop_service()
    try:
        result = svc.generate_detail(product_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("generate_detail failed")
        raise HTTPException(status_code=500, detail=str(e))
    logger.info("product #%d detail generated: extract_ok=%s title=%r",
                product_id, result["extract_ok"], result.get("title", "")[:80])
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/edit-detail")
async def v2_product_edit_detail(request: Request, product_id: int):
    form = await request.form()
    title = (form.get("title") or "").strip()
    desc = form.get("description_html") or ""
    raw_sp = (form.get("selling_points") or "").strip()
    raw_kw = (form.get("keywords") or "").strip()
    cat = (form.get("category_id") or "").strip()
    sps = [l.strip().lstrip("-*•").strip() for l in raw_sp.splitlines() if l.strip()]
    kws = [l.strip().lstrip("#").strip() for l in raw_kw.splitlines() if l.strip()]
    svc = get_tkshop_service()
    if not svc.update_detail_manual(
        product_id, title=title, description_html=desc,
        selling_points=sps, keywords=kws, category_id=cat or None,
    ):
        raise HTTPException(status_code=404, detail="product not found")
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/images")
async def v2_product_add_image(request: Request, product_id: int):
    form = await request.form()
    role = (form.get("role") or "main").strip().lower()
    upload = form.get("image_file")
    if upload is None or not getattr(upload, "filename", ""):
        raise HTTPException(status_code=400, detail="image_file required")
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / upload.filename
    with open(tmp, "wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    svc = get_tkshop_service()
    try:
        svc.add_product_image(product_id, src_path=tmp, role=role)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        try:
            tmp.unlink(); tmp.parent.rmdir()
        except Exception:
            pass
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/images/{image_id:int}/delete")
async def v2_product_delete_image(request: Request, image_id: int):
    form = await request.form()
    svc = get_tkshop_service()
    svc.delete_image(image_id)
    back = (form.get("back") or "").strip()
    return RedirectResponse(url=back or "/v2/products", status_code=303)


@router.post("/products/{product_id:int}/publish")
async def v2_product_publish(request: Request, product_id: int):
    form = await request.form()
    dry_run = (form.get("dry_run") or "0").strip() == "1"
    svc = get_tkshop_service()
    try:
        result = svc.publish(product_id, dry_run=dry_run)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info("product #%d publish (dry_run=%s) → %s",
                product_id, dry_run, "ok" if result.get("ok") else "fail")
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)
