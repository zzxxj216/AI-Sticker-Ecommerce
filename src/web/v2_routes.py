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
import io
import re
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import json as _json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from src.web.template_compat import CompatJinja2Templates as Jinja2Templates

from src.core.logger import get_logger
from src.services.daily_sticker_topics import (
    DAILY_STICKER_TOPIC_TASK_ID,
    DEFAULT_DAILY_STICKER_TOPIC_PROVIDERS,
    get_daily_sticker_topic_service,
)
from src.services.feedback import get_feedback_service
from src.services.hot_topics import KNOWN_PROVIDERS as HOT_TOPIC_PROVIDERS, get_hot_topic_service
from src.web.background import is_running, list_running, run_async
from src.services.packs import get_pack_service
from src.services.preview_gen import get_preview_gen_service
from src.services.tk_videos import get_tk_video_service
from src.services.tk_videos.service import (
    video_status_label_zh,
    video_status_pill_cls,
)
from src.services.tkshop import get_tkshop_service
from src.services.tkshop.service import (
    TKSHOP_DEFAULT_SHOP,
    TKSHOP_SHOPS,
    status_label_zh,
    status_pill_cls,
)
from src.services.topic_plans import get_topic_plan_service
from src.services.topic_synthesis import get_synthesis_service
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
templates.env.globals["status_label_zh"] = status_label_zh
templates.env.globals["status_pill_cls"] = status_pill_cls
templates.env.globals["video_status_label_zh"] = video_status_label_zh
templates.env.globals["video_status_pill_cls"] = video_status_pill_cls
templates.env.globals["tkshop_shops"] = TKSHOP_SHOPS
templates.env.globals["tkshop_default_shop"] = TKSHOP_DEFAULT_SHOP

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


def _fmt_dt_local(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=_CN_TZ).strftime("%Y-%m-%dT%H:%M")


def _parse_local_datetime(raw: Any) -> int:
    raw_s = str(raw or "").strip()
    if not raw_s or raw_s == "0":
        return 0
    try:
        # HTML datetime-local input, interpreted as local China time for this UI.
        dt = datetime.fromisoformat(raw_s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_CN_TZ)
        return int(dt.timestamp())
    except ValueError:
        try:
            return int(raw_s)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"bad scheduled_at: {raw_s!r}")


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


def _safe_v2_redirect(back: Any, fallback: str) -> str:
    """Use caller-provided return path only inside the V2 namespace."""
    path = str(back or "").strip()
    if path.startswith("/v2") and not path.startswith("//"):
        return path
    return fallback


def _append_query(url: str, extra: dict[str, Any]) -> str:
    """Append/replace query parameters on a local redirect URL."""
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in extra.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = str(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))


def _safe_filename(text: str, fallback: str = "pack") -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text or "").strip()).strip("._")
    return (name[:80] or fallback)


_UPLOAD_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _upload_filename(upload: Any) -> str:
    return str(getattr(upload, "filename", "") or "").strip()


def _is_image_filename(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in _UPLOAD_IMAGE_SUFFIXES


async def _read_upload_asset(upload: Any) -> dict[str, Any] | None:
    filename = _upload_filename(upload)
    if not filename:
        return None
    if not _is_image_filename(filename):
        raise HTTPException(
            status_code=400,
            detail=f"unsupported image type: {filename}",
        )
    data = await upload.read()
    if not data:
        return None
    return {"filename": Path(filename).name, "data": data}


async def _manual_pack_assets_from_form(form: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    assets: list[dict[str, Any]] = []
    for upload in form.getlist("sticker_files"):
        asset = await _read_upload_asset(upload)
        if asset:
            assets.append(asset)

    zip_upload = form.get("sticker_zip")
    if zip_upload is not None and _upload_filename(zip_upload):
        raw = await zip_upload.read()
        if raw:
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for info in sorted(zf.infolist(), key=lambda x: x.filename):
                        if info.is_dir():
                            continue
                        name = Path(info.filename).name
                        if not name or name.startswith(".") or "__MACOSX" in info.filename:
                            continue
                        if not _is_image_filename(name):
                            continue
                        data = zf.read(info)
                        if data:
                            assets.append({"filename": name, "data": data})
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="sticker_zip is not a valid zip file")

    cover_asset = None
    cover_upload = form.get("cover_file")
    if cover_upload is not None and _upload_filename(cover_upload):
        cover_asset = await _read_upload_asset(cover_upload)

    # Deduplicate exact same upload names from mixed file+zip input by keeping order.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for asset in assets:
        key = asset["filename"].lower()
        if key in seen:
            stem = Path(asset["filename"]).stem
            suffix = Path(asset["filename"]).suffix
            asset = dict(asset)
            asset["filename"] = f"{stem}_{len(seen) + 1}{suffix}"
            key = asset["filename"].lower()
        seen.add(key)
        deduped.append(asset)
    return deduped, cover_asset


def _ensure_feedback_table(conn: sqlite3.Connection) -> None:
    get_feedback_service().ensure_schema(conn)


def _decorate_pack_for_ui(pack: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pack:
        return None
    pack["created_human"] = _fmt_ts(pack.get("created_at"))
    pack["cover_url"] = _path_to_v2_url(
        pack.get("effective_cover_image_path") or pack.get("cover_image_path") or ""
    )
    for s in pack.get("stickers", []):
        s["image_url"] = _path_to_v2_url(s.get("image_path") or "")
    for p in pack.get("previews", []):
        p["image_url"] = _path_to_v2_url(p.get("image_path") or "")
        p["generated_human"] = _fmt_ts(p.get("generated_at"))
    for v in pack.get("videos", []):
        v["scheduled_human"] = _fmt_ts(v.get("scheduled_at"))
        v["published_human"] = _fmt_ts(v.get("published_at"))
    for pr in pack.get("products", []):
        pr["created_human"] = _fmt_ts(pr.get("created_at"))

    series_id = int(pack.get("series_id") or 0)
    preview_summary = pack.get("preview_summary") or {}
    sticker_summary = pack.get("sticker_summary") or {}
    pack["preview_task_running"] = bool(series_id and is_running(f"gen_previews:{series_id}"))
    pack["split_task_running"] = bool(series_id and is_running(f"split_series:{series_id}"))
    pack["has_inflight_generation"] = bool(
        pack["preview_task_running"]
        or pack["split_task_running"]
        or preview_summary.get("generating")
        or sticker_summary.get("generating")
    )
    pack["needs_preview_work"] = bool(
        preview_summary.get("missing")
        or preview_summary.get("pending")
        or preview_summary.get("error")
    )
    pack["needs_sticker_work"] = bool(
        sticker_summary.get("missing")
        or sticker_summary.get("pending")
        or sticker_summary.get("error")
    )
    pack["downstream_count"] = len(pack.get("videos", [])) + len(pack.get("products", []))
    return pack


def _pack_id_for_series(series_id: int | None) -> int | None:
    if not series_id:
        return None
    with _open_db() as conn:
        row = conn.execute(
            "SELECT id FROM packs WHERE series_id = ? ORDER BY id DESC LIMIT 1",
            (int(series_id),),
        ).fetchone()
    return int(row["id"]) if row else None


def _decorate_feedback_for_ui(row: dict[str, Any]) -> dict[str, Any]:
    row["created_human"] = _fmt_ts(row.get("created_at"))
    row["collected_human"] = _fmt_ts(row.get("collected_at"))
    row["asset_url"] = _path_to_v2_url(row.get("asset_image_path") or "")
    return row


def _blotato_account_label(account_id: Any, accounts: list[dict[str, Any]]) -> str:
    aid = str(account_id or "").strip()
    if not aid:
        return "未选择"
    for a in accounts:
        if str(a.get("id") or "") == aid:
            name = a.get("displayName") or a.get("username") or a.get("name") or "(unnamed)"
            username = f" @{a.get('username')}" if a.get("username") else ""
            return f"[{aid}] {name}{username}"
    return f"账号 {aid}"


def _split_stickers_for_series(series_id: int) -> dict[str, int]:
    """Prepare + split all generated previews for a series.

    Kept as a small route-level orchestration helper so the operator can do
    the common "cut every preview into stickers" action from the pack
    workbench instead of opening every preview detail page.
    """
    svc = get_preview_gen_service()
    series = svc.get_series_with_previews(series_id)
    if not series:
        raise ValueError(f"pack_series #{series_id} not found")

    prepared = attempted = ok = error = skipped = 0
    for preview in series.get("previews", []):
        if preview.get("generation_status") != "ok":
            skipped += 1
            continue
        prep = svc.prepare_stickers(int(preview["id"]))
        prepared += int(prep.get("created") or 0)
        result = svc.split_pending_for_preview(int(preview["id"]))
        attempted += int(result.get("attempted") or 0)
        ok += int(result.get("ok") or 0)
        error += int(result.get("error") or 0)
    return {
        "prepared": prepared,
        "attempted": attempted,
        "ok": ok,
        "error": error,
        "skipped_previews": skipped,
    }


def _split_urls(text: Any) -> list[str]:
    raw = str(text or "").replace(",", "\n")
    return [u.strip() for u in raw.splitlines() if u.strip()]


def _hot_topic_content(topic: dict[str, Any]) -> str:
    if topic.get("theme_summary"):
        return str(topic.get("theme_summary") or "")
    raw = topic.get("raw_payload") or {}
    if isinstance(raw, dict):
        return str(raw.get("snippet") or raw.get("summary") or raw.get("description") or "")
    return ""


def _decorate_hot_topic_for_ui(topic: dict[str, Any]) -> dict[str, Any]:
    topic["fetched_human"] = _fmt_ts(topic.get("fetched_at"))
    topic["content_preview"] = _hot_topic_content(topic)
    urls = topic.get("evidence_urls") or []
    if isinstance(urls, str):
        urls = _split_urls(urls)
    topic["primary_url"] = urls[0] if urls else ""
    topic["evidence_urls_text"] = "\n".join(urls)
    return topic


def _hot_topic_list_url(
    *,
    source: str = "all",
    status: str = "all",
    q: str = "",
    page: int = 1,
    limit: int = 10,
    edit_id: int | None = None,
) -> str:
    params: dict[str, Any] = {
        "source": source or "all",
        "status": status or "all",
        "q": q or "",
        "page": max(1, int(page or 1)),
        "limit": max(1, int(limit or 25)),
    }
    if edit_id:
        params["edit_id"] = edit_id
    return "/v2/hot-topics?" + urlencode(params)


def _topic_plan_list_url(
    *,
    status: str = "all",
    topic_id: int | None = None,
    q: str = "",
    page: int = 1,
    limit: int = 10,
) -> str:
    params: dict[str, Any] = {
        "status": status or "all",
        "q": q or "",
        "page": max(1, int(page or 1)),
        "limit": max(1, int(limit or 10)),
    }
    if topic_id:
        params["topic_id"] = topic_id
    return "/v2/topic-plans?" + urlencode(params)


def _pack_list_url(
    *,
    status: str = "all",
    q: str = "",
    page: int = 1,
    limit: int = 10,
) -> str:
    params: dict[str, Any] = {
        "status": status or "all",
        "q": q or "",
        "page": max(1, int(page or 1)),
        "limit": max(1, int(limit or 10)),
    }
    return "/v2/packs?" + urlencode(params)


def _decorate_topic_plan_for_ui(plan: dict[str, Any]) -> dict[str, Any]:
    plan["updated_human"] = _fmt_ts(plan.get("updated_at"))
    plan["created_human"] = _fmt_ts(plan.get("created_at"))
    cfg = plan.get("config") or {}
    plan["config_label"] = (
        f"{cfg.get('series_count', '?')}套×"
        f"{cfg.get('previews_per_series', '?')}图×"
        f"{cfg.get('stickers_per_preview', '?')}贴"
    )
    default_preview_count = int(cfg.get("previews_per_series") or DEFAULT_PREVIEWS_PER_SERIES)
    default_sticker_count = int(cfg.get("stickers_per_preview") or DEFAULT_STICKERS_PER_PREVIEW)
    for s in plan.get("series") or []:
        metadata = s.get("metadata") or {}
        briefs = list(metadata.get("preview_briefs") or [])
        s["preview_themes_text"] = "\n".join(
            str(b.get("theme") or "") for b in briefs
        )
        s["sticker_briefs_text"] = "\n".join(
            str(sticker)
            for b in briefs
            for sticker in list(b.get("stickers") or [])
        )
        s["preview_brief_count"] = len(briefs)
        s["sticker_brief_count"] = sum(
            len(list(b.get("stickers") or []))
            for b in briefs
        )
        first_sticker_count = 0
        for b in briefs:
            stickers = list(b.get("stickers") or [])
            if stickers:
                first_sticker_count = len(stickers)
                break
        s["edit_preview_count"] = len(briefs) or default_preview_count
        s["edit_stickers_per_preview"] = first_sticker_count or default_sticker_count
        s["preview_briefs_json"] = _json.dumps(briefs, ensure_ascii=False, indent=2)
    return plan


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
    "asset_feedback_collect":     6 * 60 * 60,
    DAILY_STICKER_TOPIC_TASK_ID: 24 * 60 * 60,
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
    page: int = 1,
    limit: int = 10,
    edit_id: int | None = None,
):
    svc = get_hot_topic_service()
    page = max(1, page)
    limit = min(max(5, limit), 100)
    offset = (page - 1) * limit
    topics, total = svc.list_topics(
        source=source if source != "all" else None,
        status=status if status != "all" else None,
        query_substring=q or None,
        limit=limit,
        offset=offset,
    )
    for t in topics:
        _decorate_hot_topic_for_ui(t)
        t["edit_url"] = _hot_topic_list_url(
            source=source, status=status, q=q, page=page, limit=limit, edit_id=int(t["id"]),
        )

    source_stats = svc.source_stats()
    stats_totals = {"total": sum(s["total"] for s in source_stats.values())}
    page_count = max(1, (total + limit - 1) // limit)
    if page > page_count and total:
        return RedirectResponse(
            url=_hot_topic_list_url(
                source=source, status=status, q=q, page=page_count, limit=limit,
            ),
            status_code=303,
        )

    edit_topic = None
    if edit_id:
        edit_topic = svc.get_topic(edit_id)
        if edit_topic:
            _decorate_hot_topic_for_ui(edit_topic)

    # Pull last_search_summary from session-style query string (search redirects here)
    last_search_summary = None
    if request.query_params.get("inserted") is not None:
        last_search_summary = {
            "inserted_total": int(request.query_params.get("inserted", "0")),
            "by_provider": _json.loads(request.query_params.get("by_provider", "{}") or "{}"),
            "errors":      _json.loads(request.query_params.get("errors", "{}") or "{}"),
        }

    daily_svc = get_daily_sticker_topic_service()
    daily_latest_run = daily_svc.latest_run()
    daily_collect_running = is_running(DAILY_STICKER_TOPIC_TASK_ID)

    return templates.TemplateResponse(
        "v2_hot_topics.html",
        {
            "request": request,
            "page_title": "热点池",
            "topics": topics,
            "total": total,
            "limit": limit,
            "page": page,
            "offset": offset,
            "pagination": {
                "page": page,
                "pages": page_count,
                "total": total,
                "limit": limit,
                "prev_url": _hot_topic_list_url(
                    source=source, status=status, q=q, page=max(1, page - 1), limit=limit,
                ),
                "next_url": _hot_topic_list_url(
                    source=source, status=status, q=q, page=min(page_count, page + 1), limit=limit,
                ),
                "first_url": _hot_topic_list_url(
                    source=source, status=status, q=q, page=1, limit=limit,
                ),
                "last_url": _hot_topic_list_url(
                    source=source, status=status, q=q, page=page_count, limit=limit,
                ),
            },
            "known_providers": HOT_TOPIC_PROVIDERS,
            "source_stats": source_stats,
            "stats_totals": stats_totals,
            "source_filter": source,
            "status_filter": status,
            "q": q,
            "edit_topic": edit_topic,
            "return_to": _hot_topic_list_url(
                source=source, status=status, q=q, page=page, limit=limit,
            ),
            "default_query": request.query_params.get("default_query", ""),
            "last_search_summary": last_search_summary,
            "daily_latest_run": daily_latest_run,
            "daily_collect_running": daily_collect_running,
            "daily_default_providers": DEFAULT_DAILY_STICKER_TOPIC_PROVIDERS,
        },
    )


@router.post("/hot-topics")
async def v2_hot_topic_create(request: Request):
    form = await request.form()
    svc = get_hot_topic_service()
    try:
        topic_id = svc.create_topic(
            topic_name=(form.get("topic_name") or "").strip(),
            theme_summary=(form.get("theme_summary") or "").strip(),
            source=(form.get("source") or "manual").strip(),
            query=(form.get("query") or "").strip(),
            region=(form.get("region") or "").strip(),
            evidence_urls=_split_urls(form.get("evidence_urls")),
            hot_score=float(form.get("hot_score") or 0),
            status=(form.get("status") or "pending").strip(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/v2/hot-topics?edit_id={topic_id}", status_code=303)


@router.post("/hot-topics/{topic_id:int}/edit")
async def v2_hot_topic_edit(request: Request, topic_id: int):
    form = await request.form()
    svc = get_hot_topic_service()
    try:
        ok = svc.update_topic(
            topic_id,
            topic_name=(form.get("topic_name") or "").strip(),
            theme_summary=(form.get("theme_summary") or "").strip(),
            source=(form.get("source") or "manual").strip(),
            query=(form.get("query") or "").strip(),
            region=(form.get("region") or "").strip(),
            evidence_urls=_split_urls(form.get("evidence_urls")),
            hot_score=float(form.get("hot_score") or 0),
            status=(form.get("status") or "pending").strip(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="topic not found")
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/hot-topics"),
        status_code=303,
    )


@router.post("/hot-topics/{topic_id:int}/delete")
async def v2_hot_topic_delete(request: Request, topic_id: int):
    form = await request.form()
    svc = get_hot_topic_service()
    try:
        ok = svc.delete_topic(topic_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="topic not found")
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/hot-topics"),
        status_code=303,
    )


@router.post("/hot-topics/batch-status")
async def v2_hot_topics_batch_status(request: Request):
    form = await request.form()
    raw_ids = form.getlist("topic_ids")
    ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
    new_status = (form.get("status") or "").strip()
    svc = get_hot_topic_service()
    try:
        count = svc.update_status_batch(ids, new_status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("hot_topics batch status → %s (%d/%d rows)", new_status, count, len(ids))
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/hot-topics"),
        status_code=303,
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


@router.post("/hot-topics/daily-collect")
async def v2_hot_topics_daily_collect(request: Request):
    """Start the daily sticker-topic collector in the background."""
    form = await request.form()
    region = (form.get("region") or "US").strip() or "US"
    providers = [str(p) for p in form.getlist("providers") if str(p).strip()]
    providers = providers or DEFAULT_DAILY_STICKER_TOPIC_PROVIDERS
    try:
        max_results = int(form.get("max_results_per_query") or 5)
    except ValueError:
        max_results = 5
    try:
        max_images = int(form.get("max_images_per_topic") or 3)
    except ValueError:
        max_images = 3
    force = str(form.get("force") or "").lower() in {"1", "true", "yes", "on"}

    svc = get_daily_sticker_topic_service()
    started = run_async(
        DAILY_STICKER_TOPIC_TASK_ID,
        svc.run_daily_collect,
        region=region,
        providers=providers,
        max_results_per_query=max_results,
        topic_count=3,
        max_images_per_topic=max_images,
        force=force,
        label="每日贴纸热点采集",
    )
    marker = "daily_started=1" if started else "daily_running=1"
    return RedirectResponse(url=f"/v2/hot-topics?{marker}", status_code=303)


@router.get("/hot-topics/daily-status")
def v2_hot_topics_daily_status():
    svc = get_daily_sticker_topic_service()
    latest = svc.latest_run()
    return JSONResponse(
        {
            "running": is_running(DAILY_STICKER_TOPIC_TASK_ID),
            "latest_run": latest,
        }
    )


@router.post("/hot-topics/daily-topics/{topic_id:int}/dismiss")
async def v2_hot_topics_daily_topic_dismiss(topic_id: int):
    """Hide a single daily-collect card. The linked hot_topics row stays
    untouched so operator can still promote it from /v2/hot-topics/{id}.
    """
    svc = get_daily_sticker_topic_service()
    ok = svc.dismiss_topic(topic_id)
    return JSONResponse({"ok": ok, "topic_id": topic_id})


@router.post("/hot-topics/daily-runs/{run_id}/dismiss-all")
async def v2_hot_topics_daily_run_dismiss_all(run_id: str):
    svc = get_daily_sticker_topic_service()
    n = svc.dismiss_run(run_id)
    return JSONResponse({"ok": True, "dismissed": n, "run_id": run_id})


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


@router.post("/hot-topics/synthesize")
async def v2_hot_topics_synthesize(request: Request):
    """A.1.5 — cluster N selected hot_topics into 1-3 themes via AI."""
    form = await request.form()
    raw_ids = form.getlist("topic_ids")
    extra = (form.get("extra_brief") or "").strip()
    try:
        ids = [int(x) for x in raw_ids if str(x).strip().isdigit()]
    except ValueError:
        raise HTTPException(status_code=400, detail="topic_ids must be ints")
    if len(ids) < 2:
        raise HTTPException(status_code=400,
                            detail="select at least 2 topics to synthesize")
    svc = get_synthesis_service()
    # Quick validation (cheap, sync) before backgrounding
    try:
        svc  # noqa: just touching to fail-fast on import
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    task_id = "synth:" + ",".join(str(x) for x in ids[:6])
    run_async(
        task_id, svc.synthesize, ids,
        extra_brief=extra,
        label=f"题材合成 ({len(ids)} 输入)",
    )
    return RedirectResponse(url="/v2/hot-topics?source=synthesized&synth_running=1",
                            status_code=303)


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
    q: str = "",
    page: int = 1,
    limit: int = 10,
):
    svc = get_topic_plan_service()
    page = max(1, page)
    limit = min(max(5, limit), 100)
    offset = (page - 1) * limit
    plans, total = svc.list_plans(
        topic_id=topic_id,
        status=status if status != "all" else None,
        query_substring=q or None,
        limit=limit,
        offset=offset,
    )
    for p in plans:
        _decorate_topic_plan_for_ui(p)
        detail = svc.get_plan(int(p["id"]))
        if detail:
            _decorate_topic_plan_for_ui(detail)
            detail["series_payload_json"] = _json.dumps(
                detail.get("series_payload") or {}, ensure_ascii=False, indent=2,
            )
            p.update(detail)

    page_count = max(1, (total + limit - 1) // limit)
    if page > page_count and total:
        return RedirectResponse(
            url=_topic_plan_list_url(
                status=status, topic_id=topic_id, q=q, page=page_count, limit=limit,
            ),
            status_code=303,
        )

    topic_candidates, _ = get_hot_topic_service().list_topics(limit=200)
    for t in topic_candidates:
        _decorate_hot_topic_for_ui(t)

    return templates.TemplateResponse(
        "v2_topic_plans.html",
        {
            "request": request,
            "page_title": "题材规划",
            "plans": plans,
            "total": total,
            "limit": limit,
            "page": page,
            "offset": offset,
            "pagination": {
                "page": page,
                "pages": page_count,
                "total": total,
                "limit": limit,
                "prev_url": _topic_plan_list_url(
                    status=status, topic_id=topic_id, q=q, page=max(1, page - 1), limit=limit,
                ),
                "next_url": _topic_plan_list_url(
                    status=status, topic_id=topic_id, q=q, page=min(page_count, page + 1), limit=limit,
                ),
                "first_url": _topic_plan_list_url(
                    status=status, topic_id=topic_id, q=q, page=1, limit=limit,
                ),
                "last_url": _topic_plan_list_url(
                    status=status, topic_id=topic_id, q=q, page=page_count, limit=limit,
                ),
            },
            "status_filter": status,
            "topic_id_filter": topic_id,
            "q": q,
            "topic_candidates": topic_candidates,
            "return_to": _topic_plan_list_url(
                status=status, topic_id=topic_id, q=q, page=page, limit=limit,
            ),
            "defaults": {
                "series_count":         DEFAULT_SERIES_COUNT,
                "previews_per_series":  DEFAULT_PREVIEWS_PER_SERIES,
                "stickers_per_preview": DEFAULT_STICKERS_PER_PREVIEW,
            },
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
        plan_id = svc.start_plan(
            topic_id,
            series_count=series_count,
            previews_per_series=previews_per_series,
            stickers_per_preview=stickers_per_preview,
            extra_brief=extra_brief,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    run_async(
        f"plan_gen:{plan_id}",
        svc.continue_plan, plan_id,
        label=f"题材规划 plan #{plan_id}",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/topic-plans?status=all"),
        status_code=303,
    )


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


@router.post("/topic-plans/{plan_id:int}/retry-extract")
async def v2_topic_plan_retry_extract(request: Request, plan_id: int):
    """Re-run only step 2 (extract) against the existing main markdown.

    Cheap recovery path when extract failed but main_raw_text is good.
    """
    svc = get_topic_plan_service()
    # Validation step (cheap, sync) — surface immediate errors before
    # backgrounding the AI call. Re-validates on the actual run too.
    with sqlite3.connect(str(DEFAULT_DB_PATH)) as _c:
        _c.row_factory = sqlite3.Row
        row = _c.execute(
            "SELECT main_raw_text FROM topic_plans WHERE id = ?", (plan_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"plan #{plan_id} not found")
        if not (row["main_raw_text"] or "").strip():
            raise HTTPException(status_code=409,
                                detail="plan has no main_raw_text — run generate_plan first")
        downstream = _c.execute(
            "SELECT COUNT(*) FROM pack_previews WHERE series_id IN "
            "(SELECT id FROM pack_series WHERE plan_id = ?)", (plan_id,),
        ).fetchone()[0]
        if downstream:
            raise HTTPException(
                status_code=409,
                detail=f"plan #{plan_id} has {downstream} pack_previews "
                       "already — would orphan generated images.",
            )
    run_async(
        f"plan_retry:{plan_id}",
        svc.retry_extract, plan_id,
        label=f"重试提取 plan #{plan_id}",
    )
    form = await request.form()
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/topic-plans/{plan_id}"),
        status_code=303,
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
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/topic-plans/{plan_id}"),
        status_code=303,
    )


@router.post("/topic-plans/{plan_id:int}/series/{series_id:int}/edit")
async def v2_topic_plan_series_edit(
    request: Request, plan_id: int, series_id: int,
):
    form = await request.form()
    svc = get_topic_plan_service()
    try:
        result = svc.update_series(
            series_id,
            series_name=(form.get("series_name") or "").strip(),
            style_anchor=(form.get("style_anchor") or "").strip(),
            palette=(form.get("palette") or "").strip(),
            pack_archetype=(form.get("pack_archetype") or "").strip(),
            priority=(form.get("priority") or "medium").strip(),
            preview_briefs=_manual_preview_briefs_from_form(form),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if int(result.get("plan_id") or 0) != int(plan_id):
        raise HTTPException(status_code=400, detail="series does not belong to this plan")
    logger.info("series #%d edited in plan #%d → %s", series_id, plan_id, result)
    target = _safe_v2_redirect(form.get("return_to"), f"/v2/topic-plans/{plan_id}")
    return RedirectResponse(
        url=_append_query(
            target,
            {
                "open_series": plan_id,
                "series_updated": result.get("series_idx") or series_id,
            },
        ),
        status_code=303,
    )


def _manual_preview_briefs_from_form(form: Any) -> list[dict[str, Any]]:
    raw_json = (form.get("preview_briefs_json") or "").strip()
    if raw_json:
        try:
            data = _json.loads(raw_json)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"preview_briefs_json invalid: {e}")
        if not isinstance(data, list):
            raise HTTPException(status_code=400, detail="preview_briefs_json must be a list")
        return data

    try:
        preview_count = max(1, min(int(form.get("preview_count") or 1), 20))
        stickers_per_preview = max(1, min(int(form.get("stickers_per_preview") or 10), 30))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad number: {e}")

    themes = [
        x.strip() for x in str(form.get("preview_themes") or "").replace("\r", "").splitlines()
        if x.strip()
    ]
    sticker_lines = [
        x.strip() for x in str(form.get("sticker_briefs") or "").replace("\r", "").splitlines()
        if x.strip()
    ]
    briefs: list[dict[str, Any]] = []
    cursor = 0
    for idx in range(1, preview_count + 1):
        theme = themes[idx - 1] if idx - 1 < len(themes) else f"手动预览 {idx}"
        stickers = sticker_lines[cursor: cursor + stickers_per_preview]
        cursor += stickers_per_preview
        if len(stickers) < stickers_per_preview:
            stickers.extend(
                f"{theme} sticker {n} / original die-cut sticker element"
                for n in range(len(stickers) + 1, stickers_per_preview + 1)
            )
        briefs.append({"preview_idx": idx, "theme": theme, "stickers": stickers})
    return briefs


@router.post("/topic-plans/{plan_id:int}/series/manual")
async def v2_topic_plan_series_manual_create(request: Request, plan_id: int):
    form = await request.form()
    target_users = [
        x.strip() for x in str(form.get("target_users_cn") or "").replace(",", "\n").splitlines()
        if x.strip()
    ]
    svc = get_topic_plan_service()
    try:
        result = svc.add_manual_series(
            plan_id,
            series_name=(form.get("series_name") or "").strip(),
            style_anchor=(form.get("style_anchor") or "").strip(),
            palette=(form.get("palette") or "").strip(),
            pack_archetype=(form.get("pack_archetype") or "").strip(),
            priority=(form.get("priority") or "medium").strip(),
            positioning_cn=(form.get("positioning_cn") or "").strip(),
            title_en=(form.get("title_en") or "").strip(),
            target_users_cn=target_users,
            target_audience_en=(form.get("target_audience_en") or "").strip(),
            preview_briefs=_manual_preview_briefs_from_form(form),
            is_selected=(form.get("is_selected") or "1").strip() == "1",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("manual series added to plan #%d → %s", plan_id, result)
    target = _safe_v2_redirect(form.get("return_to"), f"/v2/topic-plans/{plan_id}")
    return RedirectResponse(
        url=_append_query(
            target,
            {
                "open_series": plan_id,
                "series_added": result.get("series_idx") or result.get("series_id"),
            },
        ),
        status_code=303,
    )


@router.post("/topic-plans/{plan_id:int}/delete")
async def v2_topic_plan_delete(request: Request, plan_id: int):
    form = await request.form()
    svc = get_topic_plan_service()
    try:
        ok = svc.delete_plan(plan_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="plan not found")
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/topic-plans"),
        status_code=303,
    )


@router.post("/topic-plans/batch-delete")
async def v2_topic_plans_batch_delete(request: Request):
    form = await request.form()
    ids = [int(x) for x in form.getlist("plan_ids") if str(x).strip().isdigit()]
    svc = get_topic_plan_service()
    for plan_id in ids:
        try:
            svc.delete_plan(plan_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/topic-plans"),
        status_code=303,
    )


@router.post("/topic-plans/batch-retry-extract")
async def v2_topic_plans_batch_retry_extract(request: Request):
    form = await request.form()
    ids = [int(x) for x in form.getlist("plan_ids") if str(x).strip().isdigit()]
    svc = get_topic_plan_service()
    for plan_id in ids:
        run_async(
            f"plan_retry:{plan_id}",
            svc.retry_extract,
            plan_id,
            label=f"重试提取 plan #{plan_id}",
        )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/topic-plans"),
        status_code=303,
    )


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
    series["failure_summary"] = svc.series_failure_summary(series_id)
    return templates.TemplateResponse(
        "v2_series_detail.html",
        {
            "request": request,
            "page_title": f"系列 #{series_id} {series['series_name']}",
            "series": series,
        },
    )


@router.post("/series/{series_id:int}/repair")
async def v2_series_repair(request: Request, series_id: int):
    """One-click repair: retry failed previews + failed sticker splits."""
    form = await request.form()
    svc = get_preview_gen_service()
    if not svc.get_series_with_previews(series_id):
        raise HTTPException(status_code=404, detail="series not found")
    run_async(
        f"repair_series:{series_id}",
        svc.repair_series, series_id,
        label=f"修复失败 (series #{series_id})",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/series/{series_id}"),
        status_code=303,
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
    """Kick off image_generate in a background thread, redirect immediately.

    Operator can navigate away — page auto-refreshes via meta tag based
    on per-row generation_status until all done. Run twice = no-op when
    a job is already in flight (tracked via run_async task_id).
    """
    form = await request.form()
    svc = get_preview_gen_service()
    try:
        svc.prepare_previews(series_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    pack_result = None
    if (form.get("go_pack") or "").strip() == "1":
        try:
            pack_result = get_pack_service().create_pack_from_series(
                series_id,
                allow_pending_previews=True,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    run_async(
        f"gen_previews:{series_id}",
        svc.generate_pending_for_series, series_id,
        label=f"生成预览图 (series #{series_id})",
    )
    if pack_result:
        return RedirectResponse(
            url=f"/v2/packs/{pack_result['pack_id']}",
            status_code=303,
        )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/series/{series_id}"),
        status_code=303,
    )


@router.post("/series/{series_id:int}/manual-pack-upload")
async def v2_series_manual_pack_upload(request: Request, series_id: int):
    """Upload operator-made sticker images/zip for a series and create/update pack."""
    form = await request.form()
    sticker_assets, cover_asset = await _manual_pack_assets_from_form(form)
    if not sticker_assets:
        raise HTTPException(status_code=400, detail="请至少上传 1 张 sticker 图片或一个包含图片的 zip")
    display_name = (form.get("display_name") or "").strip() or None
    replace_existing = (form.get("replace_existing") or "").strip() == "1"
    try:
        result = get_pack_service().upload_manual_pack(
            series_id,
            display_name=display_name,
            sticker_assets=sticker_assets,
            cover_asset=cover_asset,
            replace_existing=replace_existing,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("manual pack upload series #%d → %s", series_id, result)
    return RedirectResponse(
        url=f"/v2/packs/{result['pack_id']}",
        status_code=303,
    )


@router.get("/previews/{preview_id:int}", response_class=HTMLResponse)
def v2_preview_detail(request: Request, preview_id: int):
    svc = get_preview_gen_service()
    p = svc.get_preview(preview_id)
    if not p:
        raise HTTPException(status_code=404, detail="preview not found")
    p["image_url"] = _path_to_v2_url(p.get("image_path") or "")
    p["generated_human"] = _fmt_ts(p.get("generated_at"))
    p["pack_id"] = _pack_id_for_series(p.get("series_id"))
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
    form = await request.form()
    svc = get_preview_gen_service()
    p = svc.get_preview(preview_id)
    if not p:
        raise HTTPException(status_code=404, detail="preview not found")
    run_async(
        f"regen_preview:{preview_id}",
        svc.regenerate_preview, preview_id,
        label=f"重新生成 preview #{preview_id}",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/previews/{preview_id}"),
        status_code=303,
    )


@router.post("/previews/{preview_id:int}/regenerate-prompt")
async def v2_preview_regenerate_prompt(request: Request, preview_id: int):
    form = await request.form()
    prompt_text = (form.get("prompt_text") or "").strip()
    feedback_text = (form.get("feedback_text") or "").strip()
    use_ai = (form.get("use_ai") or "").strip() == "1"
    svc = get_preview_gen_service()
    preview = svc.get_preview(preview_id)
    if not preview:
        raise HTTPException(status_code=404, detail="preview not found")
    if feedback_text:
        get_feedback_service().create_feedback(
            target_type="preview",
            target_id=preview_id,
            pack_id=_pack_id_for_series(preview.get("series_id")),
            rating="prompt_regenerate_ai" if use_ai else "prompt_regenerate_manual",
            reason=feedback_text,
        )
    run_async(
        f"regen_preview_prompt:{preview_id}",
        svc.regenerate_preview_with_prompt,
        preview_id,
        prompt_text=prompt_text,
        feedback_text=feedback_text,
        use_ai=use_ai,
        label=f"按反馈重生 preview #{preview_id}",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/previews/{preview_id}"),
        status_code=303,
    )


@router.post("/previews/{preview_id:int}/split")
async def v2_preview_split(request: Request, preview_id: int):
    """Auto-prepare + queue image_edit per sticker brief in background."""
    form = await request.form()
    svc = get_preview_gen_service()
    try:
        prep = svc.prepare_stickers(preview_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if prep.get("skipped_reason"):
        logger.warning("preview #%d split skipped: %s", preview_id, prep["skipped_reason"])
        return RedirectResponse(
            url=_safe_v2_redirect(form.get("return_to"), f"/v2/previews/{preview_id}"),
            status_code=303,
        )
    run_async(
        f"split_preview:{preview_id}",
        svc.split_pending_for_preview, preview_id,
        label=f"切单 sticker (preview #{preview_id})",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/previews/{preview_id}"),
        status_code=303,
    )


@router.get("/stickers/{sticker_id:int}", response_class=HTMLResponse)
def v2_sticker_detail(request: Request, sticker_id: int):
    svc = get_preview_gen_service()
    s = svc.get_sticker(sticker_id)
    if not s:
        raise HTTPException(status_code=404, detail="sticker not found")
    s["image_url"] = _path_to_v2_url(s.get("image_path") or "")
    s["preview_image_url"] = _path_to_v2_url(s.get("preview_image_path") or "")
    s["generated_human"] = _fmt_ts(s.get("generated_at"))
    s["pack_id"] = _pack_id_for_series(s.get("series_id"))
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
    form = await request.form()
    svc = get_preview_gen_service()
    if not svc.get_sticker(sticker_id):
        raise HTTPException(status_code=404, detail="sticker not found")
    run_async(
        f"regen_sticker:{sticker_id}",
        svc.regenerate_sticker, sticker_id,
        label=f"重新切 sticker #{sticker_id}",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/stickers/{sticker_id}"),
        status_code=303,
    )


@router.post("/stickers/{sticker_id:int}/toggle")
async def v2_sticker_toggle(request: Request, sticker_id: int):
    form = await request.form()
    is_selected = (form.get("is_selected") or "0").strip() == "1"
    svc = get_preview_gen_service()
    if not svc.toggle_sticker_selection(sticker_id, is_selected):
        raise HTTPException(status_code=404, detail="sticker not found")
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/stickers/{sticker_id}"),
        status_code=303,
    )


def _feedback_list_url(
    *,
    status: str = "open",
    target_type: str = "all",
    page: int = 1,
    limit: int = 50,
) -> str:
    return "/v2/feedback?" + urlencode(
        {
            "status": status,
            "target_type": target_type,
            "page": max(1, int(page or 1)),
            "limit": max(1, int(limit or 50)),
        }
    )


@router.get("/feedback", response_class=HTMLResponse)
def v2_feedback_list(
    request: Request,
    status: str = "open",
    target_type: str = "all",
    page: int = 1,
    limit: int = 50,
):
    status = status if status in {"open", "all", "new", "collected", "reviewed", "closed"} else "open"
    target_type = target_type if target_type in {"all", "pack", "preview", "sticker"} else "all"
    limit = max(10, min(int(limit or 50), 100))
    page = max(1, int(page or 1))
    offset = (page - 1) * limit
    svc = get_feedback_service()
    feedbacks, total = svc.list_feedback(
        status=status,
        target_type=target_type,
        limit=limit,
        offset=offset,
    )
    for f in feedbacks:
        _decorate_feedback_for_ui(f)
    pages = max(1, (total + limit - 1) // limit)
    stats = svc.stats()
    return templates.TemplateResponse(
        "v2_feedback.html",
        {
            "request": request,
            "page_title": "反馈收集",
            "feedbacks": feedbacks,
            "total": total,
            "stats": stats,
            "status_filter": status,
            "target_type_filter": target_type,
            "page": page,
            "limit": limit,
            "offset": offset,
            "pagination": {
                "page": page,
                "pages": pages,
                "prev_url": _feedback_list_url(status=status, target_type=target_type, page=max(1, page - 1), limit=limit),
                "next_url": _feedback_list_url(status=status, target_type=target_type, page=min(pages, page + 1), limit=limit),
            },
        },
    )


@router.post("/feedback/collect-now")
async def v2_feedback_collect_now(request: Request):
    form = await request.form()
    get_feedback_service().collect_pending()
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/feedback"),
        status_code=303,
    )


@router.post("/feedback/{feedback_id:int}/status")
async def v2_feedback_update_status(request: Request, feedback_id: int):
    form = await request.form()
    new_status = (form.get("status") or "").strip()
    try:
        ok = get_feedback_service().update_status(feedback_id, new_status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="feedback not found")
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/feedback"),
        status_code=303,
    )


@router.post("/feedback")
async def v2_feedback_create(request: Request):
    form = await request.form()
    target_type = (form.get("target_type") or "").strip().lower()
    if target_type not in {"pack", "preview", "sticker"}:
        raise HTTPException(status_code=400, detail="bad target_type")
    try:
        target_id = int(form.get("target_id") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad target_id")
    pack_id_raw = (form.get("pack_id") or "").strip()
    pack_id = int(pack_id_raw) if pack_id_raw.isdigit() else None
    reason = (form.get("reason") or "").strip()[:2000]
    rating = (form.get("rating") or "").strip()[:40]
    if not reason:
        raise HTTPException(status_code=400, detail="reason required")
    try:
        get_feedback_service().create_feedback(
            target_type=target_type,
            target_id=target_id,
            pack_id=pack_id,
            rating=rating,
            reason=reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/packs"),
        status_code=303,
    )


# ----------------------------------------------------------------------
# System / health
# ----------------------------------------------------------------------

@router.get("/system/health", response_class=HTMLResponse)
def v2_system_health(request: Request):
    """Quick at-a-glance config-and-state check across all integrations."""
    import os
    checks = []

    # AI Router (any of the OpenAI/AiHubMix keys)
    has_ai = bool(os.getenv("AIHUBMIX_API_KEY") or os.getenv("OPENAI_API_KEY"))
    checks.append({
        "name": "AI Router (text + image)",
        "configured": has_ai,
        "detail": "AIHUBMIX_API_KEY or OPENAI_API_KEY",
        "section": "A.2 / A.3 / B.1 / C.1",
    })

    # Hot-topic websearch
    checks.append({
        "name": "Web search providers",
        "configured": bool(os.getenv("AIHUBMIX_API_KEY")),
        "detail": "AIHUBMIX_API_KEY (aihubmix_surfing); TAVILY_API_KEY / PERPLEXITY_API_KEY for fallbacks",
        "section": "A.1",
    })

    # Blotato dispatcher
    try:
        from src.services.tiktok.blotato_service import BlotaToService
        bs = BlotaToService()
        checks.append({
            "name": "Blotato (TK video dispatch)",
            "configured": bs.is_configured(),
            "detail": "BLOTATO_API_KEY",
            "section": "B.1 publish",
        })
    except Exception as e:
        checks.append({"name": "Blotato", "configured": False,
                       "detail": f"import failed: {e}", "section": "B.1"})

    # TikTok Display API
    try:
        from src.services.tiktok.tiktok_display_service import TikTokDisplayService
        ts = TikTokDisplayService()
        accounts = ts.list_accounts()
        checks.append({
            "name": "TikTok Display (metrics)",
            "configured": ts.is_configured(),
            "detail": f"TIKTOK_CLIENT_KEY/SECRET; {len(accounts)} account(s) authorized",
            "section": "B.2",
        })
    except Exception as e:
        checks.append({"name": "TikTok Display", "configured": False,
                       "detail": f"import failed: {e}", "section": "B.2"})

    # TKShop wrapper
    tkshop_url = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000")
    checks.append({
        "name": "TKShop wrapper server (multi-channel-api)",
        "configured": True,  # always configured (default URL)
        "detail": (
            f"TKSHOP_SERVER_URL={tkshop_url} "
            "(POST /api/v1/tiktok/products/sticker_publish + "
            "GET /api/v1/tiktok/products/{id})"
        ),
        "section": "C.3 / C.4",
    })

    # Migrations
    conn = _open_db()
    try:
        try:
            applied = [r[0] for r in conn.execute(
                "SELECT filename FROM schema_migrations ORDER BY filename"
            ).fetchall()]
        except sqlite3.OperationalError:
            applied = []
        # Counts of all primary tables
        table_counts = {}
        for t in ["hot_topics", "topic_plans", "pack_series", "pack_previews",
                  "pack_stickers", "packs", "tk_videos", "tk_video_metrics",
                  "tkshop_products", "tkshop_product_images",
                  "tkshop_publish_logs", "ai_call_logs", "scheduled_jobs"]:
            try:
                table_counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                table_counts[t] = -1  # missing
    finally:
        conn.close()

    return templates.TemplateResponse(
        "v2_system_health.html",
        {
            "request": request,
            "page_title": "系统健康",
            "checks": checks,
            "applied_migrations": applied,
            "table_counts": table_counts,
            "running_tasks": list_running(),
        },
    )


# ----------------------------------------------------------------------
# A.4 — pack aggregate (promote series → pack, list, detail, gallery)
# ----------------------------------------------------------------------

@router.get("/packs", response_class=HTMLResponse)
def v2_packs_list(
    request: Request,
    status: str = "all",
    q: str = "",
    page: int = 1,
    limit: int = 10,
):
    svc = get_pack_service()
    page = max(1, page)
    limit = min(max(5, limit), 100)
    offset = (page - 1) * limit
    packs, total = svc.list_packs(
        status=status if status != "all" else None,
        query_substring=q or None,
        limit=limit,
        offset=offset,
    )
    page_count = max(1, (total + limit - 1) // limit)
    if page > page_count and total:
        return RedirectResponse(
            url=_pack_list_url(status=status, q=q, page=page_count, limit=limit),
            status_code=303,
        )
    for p in packs:
        p["created_human"] = _fmt_ts(p.get("created_at"))
        p["cover_url"] = _path_to_v2_url(
            p.get("effective_cover_image_path") or p.get("cover_image_path") or ""
        )
        p["detail_url"] = f"/v2/packs/{int(p['id'])}"

    candidates = svc.list_pack_candidates(limit=100)
    blotato = get_tk_video_service().list_blotato_accounts()
    return templates.TemplateResponse(
        "v2_packs.html",
        {
            "request": request,
            "page_title": "卡包管理",
            "packs": packs,
            "total": total,
            "status_filter": status,
            "q": q,
            "page": page,
            "limit": limit,
            "offset": offset,
            "pagination": {
                "page": page,
                "pages": page_count,
                "total": total,
                "limit": limit,
                "prev_url": _pack_list_url(
                    status=status, q=q, page=max(1, page - 1), limit=limit,
                ),
                "next_url": _pack_list_url(
                    status=status, q=q, page=min(page_count, page + 1), limit=limit,
                ),
                "first_url": _pack_list_url(
                    status=status, q=q, page=1, limit=limit,
                ),
                "last_url": _pack_list_url(
                    status=status, q=q, page=page_count, limit=limit,
                ),
            },
            "manual_candidates": candidates,
            "blotato_accounts": blotato["accounts"],
            "blotato_ok": blotato["ok"],
            "blotato_error": blotato["error"],
            "close_url": _pack_list_url(
                status=status, q=q, page=page, limit=limit,
            ),
            "return_to": _pack_list_url(
                status=status, q=q, page=page, limit=limit,
            ),
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
    return RedirectResponse(
        url=f"/v2/packs/{result['pack_id']}",
        status_code=303,
    )


@router.post("/packs/manual")
async def v2_pack_manual_create(request: Request):
    """Manual promote flow from the pack workbench."""
    form = await request.form()
    series_id_str = (form.get("series_id") or "").strip()
    if not series_id_str.isdigit():
        raise HTTPException(status_code=400, detail="series_id required")
    display_name = (form.get("display_name") or "").strip() or None
    cover_id_str = (form.get("cover_sticker_id") or "").strip()
    cover_sticker_id = int(cover_id_str) if cover_id_str.isdigit() else None

    svc = get_pack_service()
    try:
        result = svc.create_pack_from_series(
            int(series_id_str),
            display_name=display_name,
            cover_sticker_id=cover_sticker_id,
            allow_pending_previews=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("manual pack create from series #%s → %s", series_id_str, result)
    return RedirectResponse(
        url=f"/v2/packs/{result['pack_id']}",
        status_code=303,
    )


@router.get("/packs/{pack_id:int}", response_class=HTMLResponse)
def v2_pack_detail(request: Request, pack_id: int):
    svc = get_pack_service()
    pack = _decorate_pack_for_ui(svc.get_pack_with_downstream(pack_id))
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    return templates.TemplateResponse(
        "v2_pack_detail.html",
        {
            "request": request,
            "page_title": f"卡包 {pack['display_name']}",
            "pack": pack,
            "return_to": f"/v2/packs/{pack_id}",
        },
    )


@router.get("/packs/{pack_id:int}/stickers.zip")
def v2_pack_download_stickers(pack_id: int, selected_only: int = 1):
    pack = get_pack_service().get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")

    buf = io.BytesIO()
    added = 0
    safe_pack_name = _safe_filename(
        pack.get("display_name") or f"pack_{pack_id}",
        fallback=f"pack_{pack_id}",
    )
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in pack.get("stickers", []):
            if s.get("generation_status") != "ok" or not s.get("image_path"):
                continue
            if selected_only and not int(s.get("is_selected", 1)):
                continue
            path = Path(s["image_path"])
            if not path.is_file():
                continue
            arc = (
                f"{safe_pack_name}/"
                f"preview_{s.get('preview_idx')}_sticker_{s.get('sticker_idx')}_id_{s.get('id')}.png"
            )
            zf.write(path, arc)
            added += 1
    if added == 0:
        raise HTTPException(status_code=404, detail="no generated stickers to download")
    buf.seek(0)
    filename = f"{safe_pack_name}_stickers.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _stream_previews_zip(previews: list[dict[str, Any]], *, base_name: str, fallback: str) -> StreamingResponse:
    """Build a ZIP containing generated preview images."""
    buf = io.BytesIO()
    added = 0
    safe_name = _safe_filename(base_name, fallback=fallback)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in previews:
            if p.get("generation_status") != "ok" or not p.get("image_path"):
                continue
            path = Path(str(p["image_path"]))
            if not path.is_file():
                continue
            suffix = path.suffix.lower() or ".png"
            try:
                preview_idx = int(p.get("preview_idx") or 0)
            except Exception:
                preview_idx = 0
            arc = f"{safe_name}/preview_{preview_idx:02d}_id_{p.get('id')}{suffix}"
            zf.write(path, arc)
            added += 1
    if added == 0:
        raise HTTPException(status_code=404, detail="no generated preview images to download")
    buf.seek(0)
    filename = f"{safe_name}_previews.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/series/{series_id:int}/previews.zip")
def v2_series_download_previews(series_id: int):
    with _open_db() as conn:
        series = conn.execute(
            "SELECT id, series_idx, series_name FROM pack_series WHERE id = ?",
            (series_id,),
        ).fetchone()
        if not series:
            raise HTTPException(status_code=404, detail="series not found")
        rows = conn.execute(
            """
            SELECT id, preview_idx, image_path, generation_status
              FROM pack_previews
             WHERE series_id = ?
             ORDER BY preview_idx
            """,
            (series_id,),
        ).fetchall()
    base_name = f"series_{series['series_idx']}_{series['series_name'] or series_id}"
    return _stream_previews_zip(
        [dict(r) for r in rows],
        base_name=base_name,
        fallback=f"series_{series_id}",
    )


@router.get("/packs/{pack_id:int}/previews.zip")
def v2_pack_download_previews(pack_id: int):
    pack = get_pack_service().get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    return _stream_previews_zip(
        list(pack.get("previews") or []),
        base_name=pack.get("display_name") or f"pack_{pack_id}",
        fallback=f"pack_{pack_id}",
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
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/packs/{pack_id}"),
        status_code=303,
    )


@router.post("/packs/{pack_id:int}/cover-preview")
async def v2_pack_set_cover_preview(request: Request, pack_id: int):
    form = await request.form()
    preview_id_str = (form.get("preview_id") or "").strip()
    if not preview_id_str.isdigit():
        raise HTTPException(status_code=400, detail="preview_id required")
    svc = get_pack_service()
    try:
        svc.set_cover_from_preview(pack_id, int(preview_id_str))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/packs/{pack_id}"),
        status_code=303,
    )


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
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/packs/{pack_id}"),
        status_code=303,
    )


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
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/packs/{pack_id}"),
        status_code=303,
    )


@router.post("/packs/{pack_id:int}/refresh-total")
async def v2_pack_refresh_total(request: Request, pack_id: int):
    form = await request.form()
    svc = get_pack_service()
    try:
        cnt = svc.refresh_total_stickers(pack_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info("pack #%d total_stickers refreshed to %d", pack_id, cnt)
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/packs/{pack_id}"),
        status_code=303,
    )


@router.post("/packs/{pack_id:int}/generate-previews")
async def v2_pack_generate_previews(request: Request, pack_id: int):
    form = await request.form()
    pack = get_pack_service().get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    series_id = int(pack["series_id"])
    svc = get_preview_gen_service()
    try:
        svc.prepare_previews(series_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    run_async(
        f"gen_previews:{series_id}",
        svc.generate_pending_for_series,
        series_id,
        label=f"生成/补齐预览图 pack #{pack_id}",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/packs/{pack_id}"),
        status_code=303,
    )


@router.post("/packs/{pack_id:int}/split-stickers")
async def v2_pack_split_stickers(request: Request, pack_id: int):
    form = await request.form()
    pack = get_pack_service().get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    series_id = int(pack["series_id"])
    run_async(
        f"split_series:{series_id}",
        _split_stickers_for_series,
        series_id,
        label=f"切/补齐 sticker pack #{pack_id}",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/packs/{pack_id}"),
        status_code=303,
    )


@router.post("/packs/{pack_id:int}/delete")
async def v2_pack_delete(request: Request, pack_id: int):
    form = await request.form()
    svc = get_pack_service()
    try:
        result = svc.delete_pack(pack_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("pack #%d deleted → %s", pack_id, result)
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), "/v2/packs"),
        status_code=303,
    )


# ----------------------------------------------------------------------
# B.1 — TK videos (manual upload, AI caption two-step gen, schedule)
# ----------------------------------------------------------------------

@router.get("/videos", response_class=HTMLResponse)
def v2_videos_list(
    request: Request,
    pack_id: int | None = None,
    status: str = "all",
    publish_status: str | None = None,
    q: str | None = None,
    limit: int = 100,
):
    svc = get_tk_video_service()
    status_filter = publish_status or status or "all"
    q_norm = (q or "").strip()
    videos, total = svc.list_videos(
        pack_id=pack_id,
        publish_status=status_filter if status_filter != "all" else None,
        q=q_norm or None,
        limit=limit,
    )
    blotato = svc.list_blotato_accounts()
    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    script_templates = vss.list_templates()
    for v in videos:
        v["scheduled_human"] = _fmt_ts(v.get("scheduled_at"))
        v["published_human"] = _fmt_ts(v.get("published_at"))
        v["scheduled_value"] = _fmt_dt_local(v.get("scheduled_at"))
        v["pack_cover_url"] = _path_to_v2_url(v.get("pack_cover") or "")
        v["video_url"] = _path_to_v2_url(v.get("local_video_path") or "")
        v["hashtags_text"] = "\n".join(v.get("hashtags") or [])
        v["caption_task_running"] = is_running(f"caption_gen:{v['id']}")
        v["blotato_account_label"] = _blotato_account_label(v.get("blotato_account_id"), blotato["accounts"])
        # Per-video script context for the inline dialog
        v["scripts"] = vss.list_scripts(v["id"])
        for sc in v["scripts"]:
            sc["created_human"] = _fmt_ts(sc.get("created_at"))
            sc["updated_human"] = _fmt_ts(sc.get("updated_at"))
        v["script_task_running"] = is_running(f"video_script:{v['id']}")
    packs, _ = get_pack_service().list_packs(status="active", limit=200)
    for p in packs:
        p["cover_url"] = _path_to_v2_url(
            p.get("effective_cover_image_path") or p.get("cover_image_path") or ""
        )
    return templates.TemplateResponse(
        "v2_videos.html",
        {
            "request": request,
            "page_title": "TK 视频管理",
            "videos": videos,
            "total": total,
            "pack_id_filter": pack_id,
            "status_filter": status_filter,
            "q": q_norm,
            "limit": limit,
            "active_packs": packs,
            "blotato_accounts": blotato["accounts"],
            "blotato_ok": blotato["ok"],
            "blotato_error": blotato["error"],
            "script_templates": script_templates,
        },
    )


@router.get("/videos/new", response_class=HTMLResponse)
def v2_video_new(request: Request, pack_id: int | None = None):
    # New-video creation now happens in the /v2/videos management dialog.
    # Keep the old URL as a compatibility redirect.
    if pack_id is not None:
        return RedirectResponse(url=f"/v2/videos?pack_id={pack_id}", status_code=303)
    return RedirectResponse(url="/v2/videos", status_code=303)


def _kickoff_post_upload(video_id: int, *, include_caption: bool = True) -> None:
    """After a video file lands, auto-fire background generation so the operator
    gets文案 + 字幕 without clicking: full narration (Gemini analysis ->
    voiceover + synced subtitles -> narrated mp4) always, plus the publish
    caption unless the caller already triggered it. run_async keys make this
    idempotent (won't double-fire if one is already running)."""
    from src.services.video_narration import get_video_narration_service
    run_async(
        f"narration:{video_id}",
        get_video_narration_service().generate, video_id,
        label=f"配音独白 video #{video_id}",
    )
    if include_caption:
        run_async(
            f"caption_gen:{video_id}",
            get_tk_video_service().generate_caption, video_id,
            label=f"AI 文案 video #{video_id}",
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
    blotato_account_id = (form.get("blotato_account_id") or "").strip()
    account_open_id = (form.get("account_open_id") or "").strip()
    one_liner = (form.get("video_one_liner") or "").strip()
    scheduled_at = _parse_local_datetime(form.get("scheduled_at"))
    generate_caption = (form.get("generate_caption") or "").strip() == "1"
    caption_mode = (form.get("caption_mode") or "sync").strip()
    dispatch_now = (form.get("dispatch_now") or "").strip() == "1"
    upload = form.get("video_file")

    svc = get_tk_video_service()
    try:
        video_id = svc.create_video(
            pack_id, blotato_account_id=blotato_account_id,
            video_one_liner=one_liner,
            account_open_id=account_open_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # If a file was uploaded, save it via PackStore.
    file_saved = False
    if upload is not None and getattr(upload, "filename", ""):
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / upload.filename
        with open(tmp, "wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        try:
            svc.save_video_file(video_id, tmp, original_filename=upload.filename)
            file_saved = True
        finally:
            try:
                tmp.unlink()
                tmp.parent.rmdir()
            except Exception:
                pass

    # If "立即发布" is checked, ignore the scheduled_at field — dispatch happens
    # below after caption generation is kicked off.
    if scheduled_at and not dispatch_now:
        svc.schedule_video(video_id, scheduled_at)
    caption_ready = False
    caption_error = ""
    if generate_caption:
        if caption_mode == "async":
            run_async(
                f"caption_gen:{video_id}",
                svc.generate_caption, video_id,
                label=f"AI 文案 video #{video_id}",
            )
        else:
            try:
                svc.generate_caption(video_id)
                caption_ready = True
            except Exception as e:
                logger.exception("sync caption generation failed for video #%d", video_id)
                caption_error = str(e)[:180]

    # Auto-process every upload: full narration (voiceover + subtitles) always;
    # caption too unless the form already triggered it above.
    if file_saved:
        _kickoff_post_upload(video_id, include_caption=not generate_caption)

    dispatch_pending = False
    dispatch_error = ""
    if dispatch_now:
        v = svc.get_video(video_id)
        if not v or not v.get("local_video_path"):
            dispatch_error = "尚未上传视频文件，无法立即发布"
        elif not (v.get("blotato_account_id") or "").strip():
            dispatch_error = "未指定 Blotato 账号，无法立即发布"
        else:
            run_async(
                f"dispatch_now:{video_id}",
                svc.dispatch_video, video_id,
                label=f"立即发布 video #{video_id}",
            )
            dispatch_pending = True

    target = _safe_v2_redirect(form.get("return_to"), "/v2/videos")
    return RedirectResponse(
        url=_append_query(
            target,
            {
                "open_video": video_id,
                "video_created": 1,
                "caption_ready": 1 if caption_ready else None,
                "caption_pending": 1 if (generate_caption and caption_mode == "async") else None,
                "caption_error": caption_error or None,
                "dispatch_pending": 1 if dispatch_pending else None,
                "dispatch_error": dispatch_error or None,
            },
        ),
        status_code=303,
    )


@router.get("/videos/{video_id:int}", response_class=HTMLResponse)
def v2_video_detail(request: Request, video_id: int):
    svc = get_tk_video_service()
    v = svc.get_video(video_id)
    if not v:
        raise HTTPException(status_code=404, detail="video not found")
    v["scheduled_human"] = _fmt_ts(v.get("scheduled_at"))
    v["published_human"] = _fmt_ts(v.get("published_at"))
    v["scheduled_value"] = _fmt_dt_local(v.get("scheduled_at"))
    v["pack_cover_url"] = _path_to_v2_url(v.get("cover_image_path") or "")
    v["video_url"] = _path_to_v2_url(v.get("local_video_path") or "")
    v["hashtags_text"] = "\n".join(v.get("hashtags") or [])
    blotato = svc.list_blotato_accounts()
    v["caption_task_running"] = is_running(f"caption_gen:{v['id']}")
    v["blotato_account_label"] = _blotato_account_label(v.get("blotato_account_id"), blotato["accounts"])
    # Video script context (templates + existing scripts for this video)
    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    script_templates = vss.list_templates()
    scripts = vss.list_scripts(video_id)
    for sc in scripts:
        sc["created_human"] = _fmt_ts(sc.get("created_at"))
        sc["updated_human"] = _fmt_ts(sc.get("updated_at"))
    script_task_running = is_running(f"video_script:{video_id}")
    # Video narration (dubbing) context
    from src.services.video_narration import get_video_narration_service
    nsvc = get_video_narration_service()
    narration = nsvc.latest_for_video(video_id)
    if narration:
        narration["created_human"] = _fmt_ts(narration.get("created_at"))
    narration_task_running = is_running(f"narration:{video_id}")
    return templates.TemplateResponse(
        "v2_video_detail.html",
        {
            "request": request,
            "page_title": f"视频 #{video_id}",
            "video": v,
            "blotato_accounts": blotato["accounts"],
            "blotato_ok": blotato["ok"],
            "blotato_error": blotato["error"],
            "script_templates": script_templates,
            "scripts": scripts,
            "script_task_running": script_task_running,
            "narration": narration,
            "narration_task_running": narration_task_running,
        },
    )


# ----------------------------------------------------------------------
# Video script routes (Phase 1+2: caption-only, multi-variant, docx export)
# ----------------------------------------------------------------------

@router.post("/videos/{video_id:int}/scripts/generate")
async def v2_video_scripts_generate(request: Request, video_id: int):
    """Kick off background AI script gen. Form fields:
       - template_id (int)
       - variants (str, e.g. "A,B,C") — defaults to "A"
    """
    form = await request.form()
    try:
        template_id = int(form.get("template_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="bad template_id")
    if not template_id:
        raise HTTPException(status_code=400, detail="template_id required")
    variants_raw = (form.get("variants") or "A").strip()
    variants = [v.strip().upper() for v in variants_raw.split(",") if v.strip()][:5] or ["A"]

    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    run_async(
        f"video_script:{video_id}",
        vss.generate_scripts, video_id,
        template_id=template_id, variants=tuple(variants),
        label=f"AI 脚本 (video #{video_id}, {len(variants)} 变体)",
    )
    return RedirectResponse(url=f"/v2/videos/{video_id}#scripts", status_code=303)


@router.post("/videos/scripts/{script_id:int}/edit-scene")
async def v2_video_script_edit_scene(request: Request, script_id: int):
    form = await request.form()
    try:
        scene_idx = int(form.get("scene_idx") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="bad scene_idx")
    caption = form.get("caption")
    b_roll = form.get("b_roll_brief")
    duration = form.get("duration_s")
    duration_int: Optional[int] = None
    if duration is not None and str(duration).strip():
        try:
            duration_int = int(duration)
        except (TypeError, ValueError):
            duration_int = None
    raw_tags = (form.get("hashtags") or "").strip()
    hashtags = [t.strip().lstrip("#") for t in raw_tags.replace(",", "\n").splitlines() if t.strip()] if raw_tags else None

    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    ok = vss.update_scene(
        script_id, scene_idx,
        caption=caption if caption is not None else None,
        b_roll_brief=b_roll if b_roll is not None else None,
        duration_s=duration_int,
        hashtags=hashtags,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="scene not found")
    s = vss.get_script(script_id)
    return RedirectResponse(
        url=f"/v2/videos/{s['video_id']}#script-{script_id}",
        status_code=303,
    )


@router.post("/videos/scripts/{script_id:int}/status")
async def v2_video_script_status(request: Request, script_id: int):
    form = await request.form()
    new_status = (form.get("status") or "draft").strip()
    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    try:
        vss.update_script_status(script_id, new_status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    s = vss.get_script(script_id)
    return RedirectResponse(
        url=f"/v2/videos/{s['video_id']}#script-{script_id}", status_code=303,
    )


@router.post("/videos/scripts/{script_id:int}/delete")
async def v2_video_script_delete(request: Request, script_id: int):
    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    s = vss.get_script(script_id)
    vid = s["video_id"] if s else 0
    vss.delete_script(script_id)
    return RedirectResponse(url=f"/v2/videos/{vid}#scripts", status_code=303)


@router.get("/videos/scripts/{script_id:int}/export.docx")
def v2_video_script_export_docx(script_id: int):
    from src.services.video_scripts import get_video_script_service
    from io import BytesIO
    vss = get_video_script_service()
    try:
        data = vss.export_script_docx(script_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    from datetime import datetime as _dt
    s = vss.get_script(script_id)
    name_safe = (s.get("template_name") or "script").replace("/", "_")[:40]
    fname = f"video_{s['video_id']}_script_{s['variant_label']}_{_dt.now().strftime('%Y%m%d_%H%M')}.docx"
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Content-Length": str(len(data)),
        },
    )


# ----------------------------------------------------------------------
# Video narration (dubbing): analyze silent video -> voiceover + subtitles
# ----------------------------------------------------------------------

@router.post("/videos/{video_id:int}/narration/generate")
async def v2_video_narration_generate(request: Request, video_id: int):
    """Kick off background: Gemini analysis -> TTS voiceover -> subtitles ->
    narrated mp4. Optional form field: voice_id (else service default)."""
    form = await request.form()
    voice_id = (form.get("voice_id") or "").strip() or None
    from src.services.video_narration import get_video_narration_service
    nsvc = get_video_narration_service()
    run_async(
        f"narration:{video_id}",
        nsvc.generate, video_id,
        voice_id=voice_id,
        label=f"视频配音独白 (video #{video_id})",
    )
    return RedirectResponse(url=f"/v2/videos/{video_id}#narration", status_code=303)


@router.get("/videos/narration/{narration_id:int}/file/{name}")
def v2_video_narration_file(narration_id: int, name: str):
    """Stream a narration artifact: 'video' (narrated.mp4), 'audio'
    (voiceover.mp3) or 'srt' (subtitles.srt)."""
    from io import BytesIO
    from pathlib import Path
    from src.services.video_narration import get_video_narration_service
    n = get_video_narration_service().get_narration(narration_id)
    if not n:
        raise HTTPException(status_code=404, detail="narration not found")
    spec = {
        "video": (n.get("video_path"), "video/mp4", "narrated.mp4"),
        "audio": (n.get("audio_path"), "audio/mpeg", "voiceover.mp3"),
        "srt":   (str(Path(n.get("video_path") or ".").parent / "subtitles.srt") if n.get("video_path") else "",
                  "text/plain; charset=utf-8", "subtitles.srt"),
    }.get(name)
    if not spec:
        raise HTTPException(status_code=400, detail="bad name")
    path_str, mime, fname = spec
    p = Path(path_str or "")
    if not path_str or not p.is_file():
        raise HTTPException(status_code=404, detail="file not ready")
    data = p.read_bytes()
    disp = "attachment" if name == "srt" else "inline"
    return StreamingResponse(
        BytesIO(data), media_type=mime,
        headers={"Content-Disposition": f'{disp}; filename="{fname}"',
                 "Content-Length": str(len(data))},
    )


@router.post("/videos/narration/{narration_id:int}/promote")
async def v2_video_narration_promote(narration_id: int):
    """Copy the narrated mp4 into the publish slot so the existing Blotato
    flow ships the dubbed version."""
    from src.services.video_narration import get_video_narration_service
    nsvc = get_video_narration_service()
    n = nsvc.get_narration(narration_id)
    if not n:
        raise HTTPException(status_code=404, detail="narration not found")
    try:
        nsvc.promote_to_publish(narration_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/v2/videos/{n['video_id']}#narration", status_code=303)


@router.post("/videos/narration/{narration_id:int}/delete")
async def v2_video_narration_delete(narration_id: int):
    from src.services.video_narration import get_video_narration_service
    nsvc = get_video_narration_service()
    n = nsvc.get_narration(narration_id)
    vid = n["video_id"] if n else 0
    nsvc.delete(narration_id)
    return RedirectResponse(url=f"/v2/videos/{vid}#narration", status_code=303)


# ---- Template management page + CRUD ----

@router.get("/video-script-templates", response_class=HTMLResponse)
def v2_video_script_templates(request: Request):
    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    templates_list = vss.list_templates(include_archived=True)
    return templates.TemplateResponse(
        "v2_video_script_templates.html",
        {
            "request": request,
            "page_title": "视频脚本模板",
            "tpl_list": templates_list,
        },
    )


@router.post("/video-script-templates/save")
async def v2_video_script_template_save(request: Request):
    form = await request.form()
    tid_raw = (form.get("template_id") or "").strip()
    template_id = int(tid_raw) if tid_raw else None
    name = (form.get("name") or "").strip() or "未命名模板"
    description = (form.get("description") or "").strip()
    music_style = (form.get("music_style") or "").strip()
    is_default = (form.get("is_default") or "").strip().lower() in ("1", "on", "true", "yes")
    blueprint_raw = (form.get("scene_blueprint") or "").strip()
    try:
        scene_blueprint = _json.loads(blueprint_raw) if blueprint_raw else []
        if not isinstance(scene_blueprint, list):
            raise ValueError("blueprint must be a JSON list")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"bad scene_blueprint JSON: {e}")
    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    new_id = vss.upsert_template(
        template_id=template_id, name=name, description=description,
        music_style=music_style, scene_blueprint=scene_blueprint,
        is_default=is_default,
    )
    return RedirectResponse(url=f"/v2/video-script-templates#tpl-{new_id}", status_code=303)


@router.post("/video-script-templates/{template_id:int}/delete")
async def v2_video_script_template_delete(request: Request, template_id: int):
    from src.services.video_scripts import get_video_script_service
    vss = get_video_script_service()
    vss.delete_template(template_id)
    return RedirectResponse(url="/v2/video-script-templates", status_code=303)


@router.post("/videos/{video_id:int}/generate-caption")
async def v2_video_generate_caption(request: Request, video_id: int):
    form = await request.form()
    svc = get_tk_video_service()
    if not svc.get_video(video_id):
        raise HTTPException(status_code=404, detail="video not found")
    sync = (form.get("sync") or "").strip() == "1"
    caption_error = ""
    if sync:
        try:
            svc.generate_caption(video_id)
        except Exception as e:
            logger.exception("sync caption generation failed for video #%d", video_id)
            caption_error = str(e)[:180]
    else:
        run_async(
            f"caption_gen:{video_id}",
            svc.generate_caption, video_id,
            label=f"AI 文案 video #{video_id}",
        )
    target = _safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}")
    return RedirectResponse(
        url=_append_query(
            target,
            {
                "open_video": video_id,
                "caption_ready": 1 if sync and not caption_error else None,
                "caption_pending": 1 if not sync else None,
                "caption_error": caption_error or None,
            },
        ),
        status_code=303,
    )


@router.post("/videos/{video_id:int}/upload-video")
async def v2_video_upload_video(request: Request, video_id: int):
    """Replace (or initially upload) the local video file for a video row.
    Multipart form with field ``video_file``.
    """
    form = await request.form()
    upload = form.get("video_file")
    if upload is None or not getattr(upload, "filename", ""):
        raise HTTPException(status_code=400, detail="video_file is required")
    svc = get_tk_video_service()
    if not svc.get_video(video_id):
        raise HTTPException(status_code=404, detail="video not found")
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
    # Auto-process the (re)uploaded file: caption + full narration in background.
    _kickoff_post_upload(video_id, include_caption=True)
    back = (form.get("return_to") or "").strip()
    return RedirectResponse(
        url=_safe_v2_redirect(back, f"/v2/videos/{video_id}"),
        status_code=303,
    )


@router.post("/videos/{video_id:int}/select-caption-variant")
async def v2_video_select_caption_variant(request: Request, video_id: int):
    """Pick one of the 4 AI-generated caption variants. Body field
    ``variant_idx`` (0-based int).
    """
    form = await request.form()
    try:
        idx = int(form.get("variant_idx") or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="variant_idx must be int")
    svc = get_tk_video_service()
    try:
        result = svc.select_caption_variant(video_id, idx)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse({"ok": True, **result})


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
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}"),
        status_code=303,
    )


@router.post("/videos/{video_id:int}/edit-meta")
async def v2_video_edit_meta(request: Request, video_id: int):
    form = await request.form()
    one_liner = (form.get("video_one_liner") or "").strip()
    blotato_account = (form.get("blotato_account_id") or "").strip()
    open_id = (form.get("account_open_id") or "").strip()
    svc = get_tk_video_service()
    svc.update_one_liner(video_id, one_liner)
    svc.update_blotato_account(video_id, blotato_account)
    svc.update_account(video_id, open_id)
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}"),
        status_code=303,
    )


@router.post("/videos/{video_id:int}/dispatch-now")
async def v2_video_dispatch_now(request: Request, video_id: int):
    """Manual dispatch: bypasses the scheduler and calls Blotato inline.

    Useful when the operator wants to publish immediately without waiting
    for the next scheduler tick (60s) or for one-off retries of failed
    dispatches.
    """
    form = await request.form()
    svc = get_tk_video_service()
    v = svc.get_video(video_id)
    if not v:
        raise HTTPException(status_code=404, detail="video not found")
    if v["publish_status"] in ("published", "dispatching"):
        raise HTTPException(
            status_code=409,
            detail=f"already {v['publish_status']} — clear status first",
        )
    if not v.get("local_video_path"):
        raise HTTPException(status_code=400, detail="upload a video file first")
    try:
        result = svc.dispatch_video(video_id)
    except Exception as e:
        logger.exception("manual dispatch failed for video #%d", video_id)
        raise HTTPException(status_code=500, detail=str(e))
    logger.info("video #%d manual dispatch → %s", video_id,
                "ok" if result.get("ok") else f"fail: {result.get('error', '')[:80]}")
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}"),
        status_code=303,
    )


@router.post("/videos/{video_id:int}/poll-status")
async def v2_video_poll_status(request: Request, video_id: int):
    """Manual trigger of the dispatch-status poll for one video.

    Same as the tk_dispatch_status_poll scheduled job but immediate;
    resolves tiktok_video_id from Blotato so B.2 metrics can attach.
    """
    form = await request.form()
    svc = get_tk_video_service()
    if not svc.get_video(video_id):
        raise HTTPException(status_code=404, detail="video not found")
    result = svc.refresh_dispatch_status()
    logger.info("video #%d manual poll-status: %s", video_id, result)
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}"),
        status_code=303,
    )


@router.post("/videos/{video_id:int}/schedule")
async def v2_video_schedule(request: Request, video_id: int):
    """Set scheduled_at on the row AND immediately push it to Blotato so
    Blotato holds the post until that time. No local cron needed — Blotato
    fires the post on schedule; ``GET /poll-status`` later picks up the
    actual TikTok video id.

    Pass ``scheduled_at=0`` (or empty + ``cancel=1``) to clear the schedule.
    Cancellation only resets the local row — already-submitted Blotato
    posts must be canceled in the Blotato dashboard.
    """
    form = await request.form()
    ts = _parse_local_datetime(form.get("scheduled_at"))
    cancel = ts <= 0 or (form.get("cancel") or "").strip() == "1"

    svc = get_tk_video_service()
    if cancel:
        if not svc.schedule_video(video_id, 0):
            raise HTTPException(status_code=404, detail="video not found")
        return RedirectResponse(
            url=_safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}"),
            status_code=303,
        )

    # Persist the schedule, then dispatch to Blotato in background — image
    # upload + create_post can take 10-30s, no point blocking the redirect.
    if not svc.schedule_video(video_id, ts):
        raise HTTPException(status_code=404, detail="video not found")
    run_async(
        f"dispatch_now:{video_id}",
        svc.dispatch_video,
        video_id,
        label=f"派发到 Blotato (排期, video #{video_id})",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}"),
        status_code=303,
    )


@router.post("/videos/{video_id:int}/publish-to-draft")
async def v2_video_publish_to_draft(request: Request, video_id: int):
    """Send the video to TikTok inbox/drafts via Blotato (target.isDraft=true).
    Operator finalizes the post inside the TikTok app — no schedule, no
    cron, no auto-publish.
    """
    form = await request.form()
    svc = get_tk_video_service()
    if not svc.get_video(video_id):
        raise HTTPException(status_code=404, detail="video not found")
    run_async(
        f"dispatch_now:{video_id}",
        svc.dispatch_video,
        video_id,
        is_draft=True,
        label=f"上传到 TK 草稿 (video #{video_id})",
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}"),
        status_code=303,
    )


_ANALYTICS_PAGE_SIZES = (10, 20, 50, 100)


@router.get("/analytics", response_class=HTMLResponse)
def v2_analytics(
    request: Request,
    open_id: str | None = None,
    page: int = 1,
    limit: int = 20,
):
    """B.3 数据看板 — TikTok Display API account picker + per-account video
    list with periodic snapshots so we can show metric deltas."""
    from src.services.tiktok.display_metrics_service import (
        get_tk_display_metrics_service,
    )
    from src.services.tiktok.tiktok_display_service import TikTokDisplayService

    display = TikTokDisplayService()
    metrics_svc = get_tk_display_metrics_service()
    accounts = display.list_accounts()

    active_id = (
        open_id
        or request.session.get("tiktok_active_account")
        or (accounts[0]["open_id"] if accounts else "")
    )
    if active_id:
        request.session["tiktok_active_account"] = active_id

    active_account = None
    if active_id:
        acct = display.get_account(active_id)
        if acct:
            active_account = acct.get("user", {})

    if limit not in _ANALYTICS_PAGE_SIZES:
        limit = 20
    page = max(1, int(page or 1))
    offset = (page - 1) * limit

    if active_id:
        videos, total = metrics_svc.latest_per_video(
            active_id, limit=limit, offset=offset,
        )
    else:
        videos, total = [], 0
    for v in videos:
        v["create_human"] = _fmt_ts(v.get("create_time"))
        v["latest_human"] = _fmt_ts(v.get("latest_fetched_at"))
    pages = max(1, (total + limit - 1) // limit) if total else 1
    if page > pages:
        page = pages
        offset = (page - 1) * limit
        if active_id:
            videos, total = metrics_svc.latest_per_video(
                active_id, limit=limit, offset=offset,
            )
            for v in videos:
                v["create_human"] = _fmt_ts(v.get("create_time"))
                v["latest_human"] = _fmt_ts(v.get("latest_fetched_at"))

    last_snap = metrics_svc.last_snapshot_at(active_id) if active_id else None

    return templates.TemplateResponse(
        "v2_analytics.html",
        {
            "request": request,
            "page_title": "TK 视频数据看板",
            "tiktok_configured": display.is_configured(),
            "accounts": accounts,
            "active_id": active_id,
            "active_account": active_account,
            "videos": videos,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": pages,
            "offset": offset,
            "page_sizes": _ANALYTICS_PAGE_SIZES,
            "last_snapshot_human": _fmt_ts(last_snap) if last_snap else "",
            "last_snapshot_ts": last_snap or 0,
        },
    )


@router.post("/analytics/refresh")
async def v2_analytics_refresh(request: Request):
    """Pull a fresh snapshot from the TikTok Display API for one account."""
    from src.services.tiktok.display_metrics_service import (
        get_tk_display_metrics_service,
    )
    form = await request.form()
    open_id = (form.get("open_id") or request.session.get("tiktok_active_account") or "").strip()
    if not open_id:
        raise HTTPException(status_code=400, detail="未选择 TikTok 账号")
    svc = get_tk_display_metrics_service()
    try:
        result = svc.snapshot_account(open_id)
    except Exception as e:
        logger.warning("analytics refresh failed for %s: %s", open_id, e)
        target = _safe_v2_redirect(form.get("return_to"), "/v2/analytics")
        return RedirectResponse(
            url=_append_query(target, {"refresh_error": str(e)[:180]}),
            status_code=303,
        )
    target = _safe_v2_redirect(form.get("return_to"), "/v2/analytics")
    return RedirectResponse(
        url=_append_query(target, {
            "refreshed": 1,
            "snapped": result.get("appended", 0),
        }),
        status_code=303,
    )


@router.get("/analytics/data")
def v2_analytics_data(
    request: Request,
    open_id: str | None = None,
    page: int = 1,
    limit: int = 20,
    refresh: int = 1,
):
    """JSON endpoint used by the page's auto-refresh poller. By default
    triggers a fresh fetch *and* returns the updated video rows so the
    client can re-render without a full page reload. Pass ``refresh=0``
    to just paginate without re-snapping (used when the user changes
    page/page-size)."""
    from src.services.tiktok.display_metrics_service import (
        get_tk_display_metrics_service,
    )
    oid = (open_id or request.session.get("tiktok_active_account") or "").strip()
    if not oid:
        raise HTTPException(status_code=400, detail="未选择 TikTok 账号")
    svc = get_tk_display_metrics_service()
    error = ""
    if refresh:
        try:
            svc.snapshot_account(oid)
        except Exception as e:
            logger.warning("analytics auto-refresh failed for %s: %s", oid, e)
            error = str(e)[:180]
    if limit not in _ANALYTICS_PAGE_SIZES:
        limit = 20
    page = max(1, int(page or 1))
    rows, total = svc.latest_per_video(
        oid, limit=limit, offset=(page - 1) * limit,
    )
    pages = max(1, (total + limit - 1) // limit) if total else 1
    last = svc.last_snapshot_at(oid)
    return {
        "ok": not error,
        "error": error,
        "open_id": oid,
        "last_snapshot_at": last,
        "videos": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
    }


@router.get("/analytics/video/{tiktok_video_id}", response_class=HTMLResponse)
def v2_analytics_video_detail(request: Request, tiktok_video_id: str):
    """Per-video detail: latest stats + history table + per-snapshot delta."""
    from src.services.tiktok.display_metrics_service import (
        get_tk_display_metrics_service,
    )
    open_id = request.session.get("tiktok_active_account") or ""
    if not open_id:
        raise HTTPException(status_code=400, detail="未选择 TikTok 账号")
    svc = get_tk_display_metrics_service()
    meta = svc.video_meta(open_id, tiktok_video_id)
    if not meta:
        raise HTTPException(
            status_code=404,
            detail="未找到该视频的快照，请先在数据看板点「立即刷新」",
        )
    history = svc.video_history(open_id, tiktok_video_id, limit=100)
    # Decorate each history row with delta vs. the *previous* (older) snapshot.
    rows = []
    for i, h in enumerate(history):
        d = dict(h)
        d["fetched_human"] = _fmt_ts(h.get("fetched_at"))
        prev = history[i + 1] if i + 1 < len(history) else None
        for k in ("view", "like", "comment", "share"):
            cur = h.get(f"{k}_count") or 0
            old = prev.get(f"{k}_count") if prev else None
            d[f"delta_{k}"] = (cur - old) if old is not None else None
        rows.append(d)

    meta["create_human"] = _fmt_ts(meta.get("create_time"))
    meta["fetched_human"] = _fmt_ts(meta.get("fetched_at"))

    return templates.TemplateResponse(
        "v2_analytics_video.html",
        {
            "request": request,
            "page_title": f"TK 视频 {tiktok_video_id} 数据",
            "open_id": open_id,
            "video": meta,
            "history": rows,
        },
    )


# ----------------------------------------------------------------------
# C — TKShop products (detail gen, image mgmt, publish, status)
# ----------------------------------------------------------------------

@router.get("/products", response_class=HTMLResponse)
def v2_products_list(
    request: Request,
    q: str = "",
    pack_id: int | None = None,
    status: str = "all",
    shop: str = "",
    limit: int = 50,
    synced: int | None = None,
    checked: int | None = None,
):
    # Multi-shop default: if the operator hasn't explicitly chosen a shop in
    # the URL, land on the first configured shop instead of 'all' so each
    # store's products show separately.
    if not shop:
        shop = TKSHOP_DEFAULT_SHOP if len(TKSHOP_SHOPS) > 1 else "all"
    svc = get_tkshop_service()
    products, total = svc.list_products(
        pack_id=pack_id,
        publish_status=status if status != "all" else None,
        q=q.strip() or None,
        shop=shop if shop != "all" else None,
        limit=limit,
    )
    # Per-shop counts for the sub-tabs (uses the same filters except shop).
    shop_counts: dict[str, int] = {}
    if len(TKSHOP_SHOPS) > 1:
        all_total = svc.list_products(
            pack_id=pack_id,
            publish_status=status if status != "all" else None,
            q=q.strip() or None,
            shop=None,
            limit=1,
        )[1]
        shop_counts["all"] = all_total
        for s in TKSHOP_SHOPS:
            _, c = svc.list_products(
                pack_id=pack_id,
                publish_status=status if status != "all" else None,
                q=q.strip() or None,
                shop=s,
                limit=1,
            )
            shop_counts[s] = c
    # Enrichment: when pack_cover is missing, fall back to (a) product's
    # main product image, then (b) first ok preview from the series.
    import sqlite3 as _sql
    fb_conn = _sql.connect("data/ops_workbench.db")
    fb_conn.row_factory = _sql.Row
    try:
        for p in products:
            p["created_human"] = _fmt_ts(p.get("created_at"))
            p["published_human"] = _fmt_ts(p.get("published_at"))
            cover = _path_to_v2_url(p.get("pack_cover") or "")
            if not cover:
                # Fallback 1: product's main upload
                row = fb_conn.execute(
                    "SELECT local_path FROM tkshop_product_images "
                    "WHERE product_id = ? AND role = 'main' "
                    "ORDER BY sort_order LIMIT 1",
                    (p["id"],),
                ).fetchone()
                if row and row["local_path"]:
                    cover = _path_to_v2_url(row["local_path"])
            if not cover:
                # Fallback 2: any 'ok' preview from this pack's series
                row = fb_conn.execute(
                    """SELECT pp.image_path
                         FROM packs pk
                    LEFT JOIN pack_previews pp ON pp.series_id = pk.series_id
                                              AND pp.generation_status = 'ok'
                                              AND COALESCE(pp.image_path,'') != ''
                        WHERE pk.id = ?
                        ORDER BY pp.preview_idx LIMIT 1""",
                    (p["pack_id"],),
                ).fetchone()
                if row and row["image_path"]:
                    cover = _path_to_v2_url(row["image_path"])
            p["pack_cover_url"] = cover
    finally:
        fb_conn.close()
    return templates.TemplateResponse(
        "v2_products.html",
        {
            "request": request,
            "page_title": "TKShop 产品",
            "products": products,
            "total": total,
            "q": q,
            "pack_id_filter": pack_id,
            "status_filter": status,
            "shop_filter": shop,
            "shop_counts": shop_counts,
            "limit": limit,
            "is_platform_view": False,
            "synced": synced,
            "checked": checked,
        },
    )


@router.post("/products/sync-all")
async def v2_products_sync_all(request: Request):
    """Batch-refresh publish_status for every product that exists on the
    platform (has a tiktok_product_id), via the multi-channel-api wrapper."""
    form = await request.form()
    svc = get_tkshop_service()
    try:
        res = svc.sync_statuses()
    except Exception as e:
        logger.exception("sync_statuses failed")
        raise HTTPException(status_code=500, detail=str(e))
    back = _safe_v2_redirect((form.get("back") or "").strip(), "/v2/products")
    sep = "&" if "?" in back else "?"
    return RedirectResponse(
        url=f"{back}{sep}synced={res.get('updated', 0)}&checked={res.get('checked', 0)}",
        status_code=303,
    )


@router.post("/products/export")
async def v2_products_export(request: Request):
    """Bulk-export selected products. Accepts form fields:
      - product_ids: list of int (multi-select checkboxes)
      - fmt: 'xlsx' (default, embeds main images) or 'zip' (CSV + images folder)
    """
    form = await request.form()
    raw_ids = form.getlist("product_ids") if hasattr(form, "getlist") else []
    fmt = (form.get("fmt") or "xlsx").strip().lower()
    ids: list[int] = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(status_code=400, detail="no product_ids selected")

    svc = get_tkshop_service()
    from datetime import datetime as _dt
    stamp = _dt.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "zip":
        data = svc.export_products_zip(ids)
        filename = f"tkshop_products_{stamp}.zip"
        media = "application/zip"
    else:
        data = svc.export_products_xlsx(ids)
        filename = f"tkshop_products_{stamp}.xlsx"
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    from io import BytesIO
    return StreamingResponse(
        BytesIO(data),
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
        },
    )


@router.get("/products/platform", response_class=HTMLResponse)
def v2_products_platform(
    request: Request,
    status: str = "",
    page_size: int = 20,
    page_token: str = "",
):
    """Browse products that exist on TikTok Shop (via multi-channel-api)."""
    svc = get_tkshop_service()
    result = svc.list_platform_products(
        page_size=page_size,
        page_token=page_token,
        status=status or None,
    )
    # Best-effort enrich: cross-reference local rows by tiktok_product_id
    products = result.get("products") or []
    tt_ids = [str(p.get("id") or p.get("product_id") or "") for p in products]
    local_map: dict[str, dict] = {}
    if tt_ids:
        ph = ",".join(["?"] * len(tt_ids))
        import sqlite3 as _sql
        conn = _sql.connect("data/ops_workbench.db")
        conn.row_factory = _sql.Row
        try:
            rows = conn.execute(
                f"SELECT id, pack_id, tiktok_product_id, seller_sku FROM tkshop_products "
                f"WHERE tiktok_product_id IN ({ph})",
                tuple(tt_ids),
            ).fetchall()
            for r in rows:
                local_map[r["tiktok_product_id"]] = dict(r)
        finally:
            conn.close()
    return templates.TemplateResponse(
        "v2_products_platform.html",
        {
            "request": request,
            "page_title": "平台产品（TikTok Shop）",
            "ok": result.get("ok"),
            "raw": result.get("raw") or {},
            "products": products,
            "next_page_token": result.get("next_page_token") or "",
            "total_count": result.get("total_count") or 0,
            "endpoint": result.get("endpoint", ""),
            "error": result.get("error", ""),
            "status_filter": status,
            "page_size": page_size,
            "page_token": page_token,
            "local_map": local_map,
        },
    )


@router.post("/products/{product_id:int}/update_on_platform")
async def v2_product_update_on_platform(request: Request, product_id: int):
    """Push local edits to TikTok in background (re-uploads images +
    PUT product). Same long-running concern as publish — kick off async,
    redirect immediately. Detail page polls publish_status.
    """
    svc = get_tkshop_service()
    # Pre-mark publishing so the redirect lands on a "in-flight" page.
    import sqlite3 as _sql
    try:
        conn = _sql.connect("data/ops_workbench.db")
        conn.execute(
            "UPDATE tkshop_products SET publish_status = 'publishing' WHERE id = ?",
            (product_id,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("could not pre-mark publishing for product #%d: %s", product_id, e)

    def _push_then_restore():
        try:
            svc.update_on_platform(product_id)
        finally:
            # update_on_platform doesn't write publish_status itself; flip back
            # to a sane state from the platform's POV.
            try:
                svc.sync_one_status(product_id)
            except Exception:  # noqa: BLE001
                # Fall back to 'published' if sync fails — at least the
                # banner clears.
                conn = _sql.connect("data/ops_workbench.db")
                conn.execute(
                    "UPDATE tkshop_products SET publish_status = 'published' "
                    "WHERE id = ? AND publish_status = 'publishing'",
                    (product_id,),
                )
                conn.commit()
                conn.close()

    run_async(
        f"tkshop_publish:{product_id}",
        _push_then_restore,
        label=f"推送更新到 TikTok (product #{product_id})",
    )
    logger.info("product #%d update_on_platform dispatched", product_id)
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/edit-detail-then-push")
async def v2_product_edit_then_push(request: Request, product_id: int):
    """Save local edits synchronously (fast — DB only), then push to TikTok
    in background. Same async pattern as /publish.
    """
    form = await request.form()
    title = (form.get("title") or "").strip()
    desc = form.get("description_html") or ""
    raw_sp = (form.get("selling_points") or "").strip()
    raw_kw = (form.get("keywords") or "").strip()
    cat = (form.get("category_id") or "").strip()
    seller_sku = (form.get("seller_sku") or "").strip().upper()
    sps = [l.strip().lstrip("-*•").strip() for l in raw_sp.splitlines() if l.strip()]
    kws = [l.strip().lstrip("#").strip() for l in raw_kw.splitlines() if l.strip()]
    svc = get_tkshop_service()
    if not svc.update_detail_manual(
        product_id, title=title, description_html=desc,
        selling_points=sps, keywords=kws, category_id=cat or None,
        seller_sku=seller_sku or None,
    ):
        raise HTTPException(status_code=404, detail="product not found")

    import sqlite3 as _sql
    try:
        conn = _sql.connect("data/ops_workbench.db")
        conn.execute(
            "UPDATE tkshop_products SET publish_status = 'publishing' WHERE id = ?",
            (product_id,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("could not pre-mark publishing for product #%d: %s", product_id, e)

    def _push_then_restore():
        try:
            svc.update_on_platform(product_id)
        finally:
            try:
                svc.sync_one_status(product_id)
            except Exception:  # noqa: BLE001
                conn = _sql.connect("data/ops_workbench.db")
                conn.execute(
                    "UPDATE tkshop_products SET publish_status = 'published' "
                    "WHERE id = ? AND publish_status = 'publishing'",
                    (product_id,),
                )
                conn.commit()
                conn.close()

    run_async(
        f"tkshop_publish:{product_id}",
        _push_then_restore,
        label=f"保存并推送 (product #{product_id})",
    )
    logger.info("product #%d edit-then-push dispatched", product_id)
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/from-pack/{pack_id:int}")
async def v2_product_create(request: Request, pack_id: int):
    form = await request.form()
    svc = get_tkshop_service()
    target_shop = (form.get("shop") or "").strip() or TKSHOP_DEFAULT_SHOP
    try:
        pid = svc.create_product_from_pack(pack_id, shop=target_shop)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    title = (form.get("title") or "").strip()
    desc = (form.get("description_html") or "").strip()
    category_id = (form.get("category_id") or "").strip()
    raw_sp = (form.get("selling_points") or "").strip()
    raw_kw = (form.get("keywords") or "").strip()
    selling_points = [l.strip().lstrip("-*•").strip() for l in raw_sp.splitlines() if l.strip()]
    keywords = [l.strip().lstrip("#").strip() for l in raw_kw.splitlines() if l.strip()]
    if title or desc or category_id or selling_points or keywords:
        svc.update_detail_manual(
            pid,
            title=title or None,
            description_html=desc or None,
            selling_points=selling_points or None,
            keywords=keywords or None,
            category_id=category_id or None,
        )
    if (form.get("generate_detail") or "").strip() == "1":
        run_async(
            f"detail_gen:{pid}",
            svc.generate_detail,
            pid,
            label=f"AI 详情 product #{pid}",
        )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/products/{pid}"),
        status_code=303,
    )


@router.get("/products/{product_id:int}", response_class=HTMLResponse)
def v2_product_detail(request: Request, product_id: int):
    svc = get_tkshop_service()
    p = svc.get_product(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="product not found")
    p["created_human"] = _fmt_ts(p.get("created_at"))
    p["published_human"] = _fmt_ts(p.get("published_at"))
    cover = _path_to_v2_url(p.get("cover_image_path") or "")
    if not cover:
        # Fallback chain: product main image → first ok pack preview
        main_img = next(
            (i for i in (p.get("images") or [])
             if (i.get("role") or "") == "main" and i.get("local_path")),
            None,
        )
        if main_img:
            cover = _path_to_v2_url(main_img["local_path"])
    if not cover and p.get("pack_id"):
        import sqlite3 as _sql
        conn = _sql.connect("data/ops_workbench.db")
        conn.row_factory = _sql.Row
        try:
            row = conn.execute(
                """SELECT pp.image_path
                     FROM packs pk
                LEFT JOIN pack_previews pp ON pp.series_id = pk.series_id
                                          AND pp.generation_status = 'ok'
                                          AND COALESCE(pp.image_path,'') != ''
                    WHERE pk.id = ?
                    ORDER BY pp.preview_idx LIMIT 1""",
                (p["pack_id"],),
            ).fetchone()
            if row and row["image_path"]:
                cover = _path_to_v2_url(row["image_path"])
        finally:
            conn.close()
    p["pack_cover_url"] = cover
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
    if not svc.get_product(product_id):
        raise HTTPException(status_code=404, detail="product not found")
    run_async(
        f"detail_gen:{product_id}",
        svc.generate_detail, product_id,
        label=f"AI 详情 product #{product_id}",
    )
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/edit-detail")
async def v2_product_edit_detail(request: Request, product_id: int):
    form = await request.form()
    title = (form.get("title") or "").strip()
    desc = form.get("description_html") or ""
    raw_sp = (form.get("selling_points") or "").strip()
    raw_kw = (form.get("keywords") or "").strip()
    cat = (form.get("category_id") or "").strip()
    seller_sku = (form.get("seller_sku") or "").strip().upper()
    sps = [l.strip().lstrip("-*•").strip() for l in raw_sp.splitlines() if l.strip()]
    kws = [l.strip().lstrip("#").strip() for l in raw_kw.splitlines() if l.strip()]
    svc = get_tkshop_service()
    if not svc.update_detail_manual(
        product_id, title=title, description_html=desc,
        selling_points=sps, keywords=kws, category_id=cat or None,
        seller_sku=seller_sku or None,
    ):
        raise HTTPException(status_code=404, detail="product not found")
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/images")
async def v2_product_add_image(request: Request, product_id: int):
    """Multi-file upload. First uploaded file becomes 'main' if no main
    exists yet; rest become 'secondary'. The legacy single-file form
    posting one ``image_file`` is still supported.
    """
    form = await request.form()
    uploads = form.getlist("image_files") if hasattr(form, "getlist") else []
    if not uploads:
        single = form.get("image_file")
        if single is not None and getattr(single, "filename", ""):
            uploads = [single]
    files: list[tuple[str, bytes]] = []
    for u in uploads:
        if u is None or not getattr(u, "filename", ""):
            continue
        files.append((u.filename, u.file.read()))
    if not files:
        raise HTTPException(status_code=400, detail="at least one image required")
    svc = get_tkshop_service()
    try:
        result = svc.add_product_images_batch(product_id, files)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info("product #%d batch upload: %s", product_id, result)
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/import_pack_previews")
async def v2_product_import_pack_previews(request: Request, product_id: int):
    svc = get_tkshop_service()
    try:
        result = svc.import_pack_previews(product_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info("product #%d import previews: %s", product_id, result)
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/auto_design_images")
async def v2_product_auto_design_images(request: Request, product_id: int):
    """Background: synthesize image specs via gpt-5.4 and run image_edit
    (image-to-image) per concept. Operator can navigate away.
    """
    svc = get_tkshop_service()
    run_async(
        f"tkshop_auto_design:{product_id}",
        svc.auto_design_images, product_id,
        label=f"AI 主图设计 (product #{product_id})",
    )
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


# -------- JSON endpoints used by the product-detail page (no full-page reload)

@router.post("/products/{product_id:int}/auto_design_images_sync")
async def v2_product_auto_design_images_sync(request: Request, product_id: int):
    """Kick off AI image gen in a background thread. Returns immediately
    with ``{ok, task_id, message}``. Operator can close the modal or
    leave the page — task keeps running. Poll ``/auto_design_status``
    or just refresh the detail page later.
    """
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else dict(await request.form())
    try:
        count = max(1, min(int(payload.get("count", 4)), 8))
    except (TypeError, ValueError):
        count = 4
    language = (str(payload.get("language") or "en")).lower().strip() or "en"
    size = (str(payload.get("size") or "1024x1024")).strip() or "1024x1024"
    secondary_count = max(0, count - 1)
    task_id = f"tkshop_auto_design:{product_id}"
    svc = get_tkshop_service()
    started = run_async(
        task_id,
        svc.auto_design_images, product_id,
        secondary_count=secondary_count,
        language=language,
        replace_existing_ai=True,
        size=size,
        label=f"AI 主图设计 (product #{product_id}, {count} 张, {language}, {size})",
    )
    if not started:
        return JSONResponse({
            "ok": True, "task_id": task_id, "started": False,
            "message": "任务已在后台运行中（同一产品不会并行启动）",
        })
    return JSONResponse({
        "ok": True, "task_id": task_id, "started": True,
        "message": "已在后台启动 — 关弹窗或离开页面都不会中断。完成后刷新详情页即可看到。",
    })


@router.get("/products/{product_id:int}/auto_design_status")
def v2_product_auto_design_status(product_id: int):
    """Poll endpoint for the modal: returns {running, images} so the modal
    can show progress and the final result grid without a full reload.
    """
    task_id = f"tkshop_auto_design:{product_id}"
    svc = get_tkshop_service()
    images = svc.list_product_images(product_id)
    images_with_url = [
        {**i, "url": _path_to_v2_url(i.get("local_path") or "")}
        for i in images if i.get("source") == "ai"
    ]
    return JSONResponse({
        "ok": True,
        "running": is_running(task_id),
        "images": images_with_url,
    })


@router.post("/products/images/{image_id:int}/move")
async def v2_product_move_image(request: Request, image_id: int):
    """Update sort_order and/or role of a single image. Returns JSON."""
    body = await request.json()
    role = body.get("role")
    sort_order = body.get("sort_order")
    svc = get_tkshop_service()
    try:
        result = svc.update_image(
            image_id,
            role=role if role else None,
            sort_order=int(sort_order) if sort_order is not None else None,
        )
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse({"ok": True, **result})


@router.post("/products/{product_id:int}/activate")
async def v2_product_activate(request: Request, product_id: int):
    svc = get_tkshop_service()
    try:
        result = svc.activate_on_platform(product_id)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse(result)


@router.post("/products/{product_id:int}/deactivate")
async def v2_product_deactivate(request: Request, product_id: int):
    svc = get_tkshop_service()
    try:
        result = svc.deactivate_on_platform(product_id)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse(result)


@router.post("/products/{product_id:int}/sync_status")
async def v2_product_sync_status(request: Request, product_id: int):
    svc = get_tkshop_service()
    try:
        result = svc.sync_one_status(product_id)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse(result)


@router.post("/products/images/{image_id:int}/delete-json")
async def v2_product_delete_image_json(request: Request, image_id: int):
    svc = get_tkshop_service()
    ok = svc.delete_image(image_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "image not found"}, status_code=404)
    return JSONResponse({"ok": True, "image_id": image_id})


@router.post("/products/{product_id:int}/delete")
async def v2_product_delete(request: Request, product_id: int):
    form = await request.form()
    svc = get_tkshop_service()
    try:
        result = svc.delete_product(product_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info("product #%d deleted: %s", product_id, result)
    back = (form.get("back") or "").strip()
    return RedirectResponse(url=back or "/v2/products", status_code=303)


@router.post("/products/images/{image_id:int}/delete")
async def v2_product_delete_image(request: Request, image_id: int):
    form = await request.form()
    svc = get_tkshop_service()
    svc.delete_image(image_id)
    back = (form.get("back") or "").strip()
    return RedirectResponse(url=back or "/v2/products", status_code=303)


@router.post("/products/{product_id:int}/publish")
async def v2_product_publish(request: Request, product_id: int):
    """Kick off publish in background (60-300s for image upload + create + activate).
    Operator gets redirected back to detail page immediately; the page polls
    publish_status and refreshes when done. Closing tab does NOT cancel —
    background daemon thread runs to completion.
    """
    form = await request.form()
    dry_run = (form.get("dry_run") or "0").strip() == "1"
    auto_fix_raw = (form.get("auto_fix") or "").strip().lower()
    auto_fix = auto_fix_raw in ("1", "on", "true", "yes")
    auto_activate_raw = (form.get("auto_activate") or "1").strip().lower()
    auto_activate = auto_activate_raw not in ("0", "off", "false", "no")
    svc = get_tkshop_service()

    if dry_run:
        # Dry-run is fast (no HTTP) — keep synchronous so operator sees payload.
        try:
            svc.publish(product_id, dry_run=True, auto_activate=auto_activate)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)

    # Mark 'publishing' immediately so the redirect lands on a page that
    # already shows the in-flight banner — no race window.
    import sqlite3 as _sql
    try:
        conn = _sql.connect("data/ops_workbench.db")
        conn.execute(
            "UPDATE tkshop_products SET publish_status = 'publishing' WHERE id = ?",
            (product_id,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("could not pre-mark publishing for product #%d: %s", product_id, e)

    fn = svc.publish_with_self_heal if auto_fix else svc.publish
    kwargs = {"auto_activate": auto_activate}
    if not auto_fix:
        kwargs["dry_run"] = False
    run_async(
        f"tkshop_publish:{product_id}",
        fn, product_id,
        label=f"发布到 TKShop (product #{product_id}, auto_activate={auto_activate})",
        **kwargs,
    )
    logger.info(
        "product #%d publish dispatched (auto_fix=%s, auto_activate=%s)",
        product_id, auto_fix, auto_activate,
    )
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/republish")
async def v2_product_republish(request: Request, product_id: int):
    """Re-create the product on TikTok from scratch.

    Used when the platform listing is gone (status='deleted') or the
    operator wants to start over. Clears the local tiktok_product_id /
    tiktok_sku_id so publish_with_self_heal's duplicate-guard does not
    short-circuit, then dispatches the same async publish flow as
    /publish.

    Form: auto_activate=0|1 (default 1), auto_fix=on|<absent>.
    """
    form = await request.form()
    auto_activate_raw = (form.get("auto_activate") or "1").strip().lower()
    auto_activate = auto_activate_raw not in ("0", "off", "false", "no")
    auto_fix_raw = (form.get("auto_fix") or "").strip().lower()
    auto_fix = auto_fix_raw in ("1", "on", "true", "yes")

    svc = get_tkshop_service()
    try:
        svc.reset_for_republish(product_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"reset failed: {e}")

    import sqlite3 as _sql
    try:
        conn = _sql.connect("data/ops_workbench.db")
        conn.execute(
            "UPDATE tkshop_products SET publish_status = 'publishing' WHERE id = ?",
            (product_id,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("could not pre-mark publishing for product #%d: %s", product_id, e)

    fn = svc.publish_with_self_heal if auto_fix else svc.publish
    kwargs = {"auto_activate": auto_activate}
    if not auto_fix:
        kwargs["dry_run"] = False
    run_async(
        f"tkshop_publish:{product_id}",
        fn, product_id,
        label=f"重新发布到 TKShop (product #{product_id}, auto_activate={auto_activate})",
        **kwargs,
    )
    logger.info(
        "product #%d republish dispatched (auto_fix=%s, auto_activate=%s)",
        product_id, auto_fix, auto_activate,
    )
    return RedirectResponse(url=f"/v2/products/{product_id}", status_code=303)


@router.post("/products/{product_id:int}/clone-to-shop")
async def v2_product_clone_to_shop(request: Request, product_id: int):
    """Duplicate a product into another TikTok shop. The clone is a fresh
    local row (publish_status='draft', no tiktok_product_id) so the operator
    reviews then explicitly publishes — no automatic platform-side action.
    """
    form = await request.form()
    target = (form.get("target_shop") or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="target_shop is required")
    svc = get_tkshop_service()
    try:
        new_id = svc.clone_to_shop(product_id, target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("product #%d cloned to shop %s as #%d", product_id, target, new_id)
    return RedirectResponse(url=f"/v2/products/{new_id}", status_code=303)


@router.get("/products/{product_id:int}/publish_status")
def v2_product_publish_status(product_id: int):
    """Poll endpoint used by the detail page to detect when publish finishes."""
    task_id = f"tkshop_publish:{product_id}"
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        row = conn.execute(
            "SELECT publish_status, tiktok_product_id, tiktok_sku_id "
            "FROM tkshop_products WHERE id = ?",
            (product_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return JSONResponse({"ok": False, "error": "product not found"}, status_code=404)
    return JSONResponse({
        "ok": True,
        "running": is_running(task_id),
        "publish_status":      row["publish_status"],
        "tiktok_product_id":   row["tiktok_product_id"] or "",
        "tiktok_sku_id":       row["tiktok_sku_id"] or "",
    })



# ============================================================================
# 🧪 LAB: Multi-SKU sticker products (实验性，与现有 tkshop 流程隔离)
# ============================================================================


@router.get("/lab/multi-sku", response_class=HTMLResponse)
def v2_lab_multi_sku_list(request: Request):
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    products = svc.list_products()
    for p in products:
        p["created_human"] = _fmt_ts(p.get("created_at"))
        p["updated_human"] = _fmt_ts(p.get("updated_at"))
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        topics = conn.execute(
            """SELECT ht.id, ht.topic_name, ht.region, ht.status,
                      (SELECT COUNT(DISTINCT pk.id)
                         FROM topic_plans tp
                    LEFT JOIN pack_series ps ON ps.plan_id = tp.id
                    LEFT JOIN packs pk       ON pk.series_id = ps.id
                        WHERE tp.topic_id = ht.id) AS pack_count
                 FROM hot_topics ht
                ORDER BY ht.id DESC
                LIMIT 200"""
        ).fetchall()
        topics = [dict(t) for t in topics if (t["pack_count"] or 0) >= 1]
    finally:
        conn.close()
    return templates.TemplateResponse(
        "v2_lab_multi_sku_list.html",
        {
            "request": request,
            "page_title": "🧪 Lab · 多 SKU 产品",
            "products": products,
            "topics": topics,
        },
    )


@router.post("/lab/multi-sku/create")
async def v2_lab_multi_sku_create(request: Request):
    form = await request.form()
    try:
        topic_id = int(form.get("topic_id") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="bad topic_id")
    pack_ids_raw = form.getlist("pack_ids") if hasattr(form, "getlist") else []
    pack_ids: list[int] = []
    for v in pack_ids_raw:
        try:
            pack_ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not pack_ids:
        raise HTTPException(status_code=400, detail="select at least one pack")
    sales_attribute_name = (form.get("sales_attribute_name") or "Theme").strip() or "Theme"
    title = (form.get("title") or "").strip()
    description_html = (form.get("description_html") or "").strip()

    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    try:
        pid = svc.create_from_topic(
            topic_id=topic_id, pack_ids=pack_ids,
            sales_attribute_name=sales_attribute_name,
            title=title, description_html=description_html,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/v2/lab/multi-sku/{pid}", status_code=303)


@router.get("/lab/multi-sku/topics/{topic_id:int}/packs")
def v2_lab_multi_sku_topic_packs(topic_id: int):
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        rows = conn.execute(
            """SELECT pk.id, pk.display_name, pk.pack_uid, pk.total_stickers,
                      ps.series_name, ps.pack_archetype, ps.palette
                 FROM topic_plans tp
            LEFT JOIN pack_series ps ON ps.plan_id = tp.id
            LEFT JOIN packs pk       ON pk.series_id = ps.id
                WHERE tp.topic_id = ? AND pk.id IS NOT NULL
                ORDER BY pk.id""",
            (topic_id,),
        ).fetchall()
    finally:
        conn.close()
    return JSONResponse({"ok": True, "packs": [dict(r) for r in rows]})


@router.get("/lab/multi-sku/{product_id:int}", response_class=HTMLResponse)
def v2_lab_multi_sku_detail(request: Request, product_id: int):
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    p = svc.get_product(product_id)
    if not p:
        raise HTTPException(status_code=404, detail="product not found")
    p["created_human"] = _fmt_ts(p.get("created_at"))
    p["updated_human"] = _fmt_ts(p.get("updated_at"))
    p["published_human"] = _fmt_ts(p.get("published_at"))
    cover_url = ""
    if p.get("primary_pack_id"):
        import sqlite3 as _sql
        conn = _sql.connect("data/ops_workbench.db")
        try:
            r = conn.execute(
                "SELECT cover_image_path FROM packs WHERE id=?",
                (p["primary_pack_id"],),
            ).fetchone()
            if r and r[0]:
                cover_url = _path_to_v2_url(r[0])
        finally:
            conn.close()
    p["cover_url"] = cover_url
    avail_packs = []
    if p.get("topic_id"):
        import sqlite3 as _sql
        conn = _sql.connect("data/ops_workbench.db")
        conn.row_factory = _sql.Row
        try:
            avail_packs = [dict(r) for r in conn.execute(
                """SELECT pk.id, pk.display_name, pk.pack_uid, pk.total_stickers
                     FROM topic_plans tp
                LEFT JOIN pack_series ps ON ps.plan_id = tp.id
                LEFT JOIN packs pk       ON pk.series_id = ps.id
                    WHERE tp.topic_id = ? AND pk.id IS NOT NULL
                    ORDER BY pk.id""",
                (p["topic_id"],),
            ).fetchall()]
        finally:
            conn.close()
    return templates.TemplateResponse(
        "v2_lab_multi_sku_detail.html",
        {
            "request": request,
            "page_title": f"🧪 Lab 多 SKU 产品 #{product_id}",
            "product": p,
            "avail_packs": avail_packs,
        },
    )


@router.post("/lab/multi-sku/{product_id:int}/edit-meta")
async def v2_lab_multi_sku_edit_meta(request: Request, product_id: int):
    form = await request.form()
    raw_sp = (form.get("selling_points") or "").strip()
    raw_kw = (form.get("keywords") or "").strip()
    sps = [l.strip().lstrip("-*•").strip() for l in raw_sp.splitlines() if l.strip()]
    kws = [l.strip().lstrip("#").strip() for l in raw_kw.splitlines() if l.strip()]
    pp_raw = (form.get("primary_pack_id") or "").strip()
    primary_pack_id = int(pp_raw) if pp_raw else None
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    svc.update_product_meta(
        product_id,
        title=form.get("title") or "",
        description_html=form.get("description_html") or "",
        selling_points=sps, keywords=kws,
        category_id=(form.get("category_id") or "").strip() or None,
        sales_attribute_name=(form.get("sales_attribute_name") or "").strip() or None,
        primary_pack_id=primary_pack_id,
    )
    return RedirectResponse(url=f"/v2/lab/multi-sku/{product_id}", status_code=303)


@router.post("/lab/multi-sku/{product_id:int}/sku/add")
async def v2_lab_multi_sku_sku_add(request: Request, product_id: int):
    form = await request.form()
    pp_raw = (form.get("pack_id") or "").strip()
    pack_id = int(pp_raw) if pp_raw else None
    seller_sku = (form.get("seller_sku") or "").strip()
    value = (form.get("sales_attribute_value") or "").strip()
    price = (form.get("price_amount_override") or "").strip()
    stock_raw = (form.get("stock_override") or "").strip()
    stock = int(stock_raw) if stock_raw else None
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    try:
        svc.add_sku(
            product_id, pack_id=pack_id, seller_sku=seller_sku,
            sales_attribute_value=value,
            price_amount_override=price, stock_override=stock,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/v2/lab/multi-sku/{product_id}#skus", status_code=303)


@router.post("/lab/multi-sku/{product_id:int}/sku/{sku_id:int}/edit")
async def v2_lab_multi_sku_sku_edit(request: Request, product_id: int, sku_id: int):
    form = await request.form()
    seller_sku = form.get("seller_sku")
    value = form.get("sales_attribute_value")
    price = form.get("price_amount_override")
    stock_raw = form.get("stock_override")
    stock_arg: Any = ...
    if stock_raw is not None:
        s = str(stock_raw).strip()
        stock_arg = int(s) if s else None
    sort_raw = form.get("sort_order")
    sort_int = int(sort_raw) if sort_raw and str(sort_raw).strip() else None
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    try:
        svc.update_sku(
            sku_id,
            seller_sku=seller_sku,
            sales_attribute_value=value,
            price_amount_override=price,
            stock_override=stock_arg,
            sort_order=sort_int,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/v2/lab/multi-sku/{product_id}#sku-{sku_id}", status_code=303)


@router.post("/lab/multi-sku/{product_id:int}/sku/{sku_id:int}/delete")
async def v2_lab_multi_sku_sku_delete(request: Request, product_id: int, sku_id: int):
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    svc.delete_sku(sku_id)
    return RedirectResponse(url=f"/v2/lab/multi-sku/{product_id}#skus", status_code=303)


@router.post("/lab/multi-sku/{product_id:int}/publish")
async def v2_lab_multi_sku_publish(request: Request, product_id: int):
    form = await request.form()
    auto_activate_raw = (form.get("auto_activate") or "1").strip().lower()
    auto_activate = auto_activate_raw not in ("0", "off", "false", "no")
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    run_async(
        f"lab_msku_publish:{product_id}",
        svc.publish, product_id, auto_activate=auto_activate,
        label=f"Lab 多 SKU 发布 #{product_id} (auto_activate={auto_activate})",
    )
    return RedirectResponse(url=f"/v2/lab/multi-sku/{product_id}", status_code=303)


@router.post("/lab/multi-sku/{product_id:int}/update-on-platform")
async def v2_lab_multi_sku_update_on_platform(request: Request, product_id: int):
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    run_async(
        f"lab_msku_publish:{product_id}",
        svc.update_on_platform, product_id,
        label=f"Lab 多 SKU 推送 #{product_id}",
    )
    return RedirectResponse(url=f"/v2/lab/multi-sku/{product_id}", status_code=303)


@router.post("/lab/multi-sku/{product_id:int}/sync-status")
async def v2_lab_multi_sku_sync_status(request: Request, product_id: int):
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    try:
        result = svc.sync_status(product_id)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse(result)


@router.post("/lab/multi-sku/{product_id:int}/delete")
async def v2_lab_multi_sku_delete(request: Request, product_id: int):
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    svc.delete_product(product_id)
    return RedirectResponse(url="/v2/lab/multi-sku", status_code=303)


@router.get("/lab/multi-sku/{product_id:int}/publish_status")
def v2_lab_multi_sku_publish_status_json(product_id: int):
    task_id = f"lab_msku_publish:{product_id}"
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        row = conn.execute(
            "SELECT publish_status, tiktok_product_id FROM lab_msku_products WHERE id=?",
            (product_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({
        "ok": True,
        "running": is_running(task_id),
        "publish_status": row["publish_status"],
        "tiktok_product_id": row["tiktok_product_id"] or "",
    })
