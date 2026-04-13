from __future__ import annotations

import io
import json as _json
import os
import queue
import re
import secrets
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.core.logger import get_logger
from src.services.ops.trend_service import TrendService
from src.services.tools.sticker_agent import StickerAgentService
from src.services.tools.blog_agent import BlogAgentService
from src.services.video.video_type_service import VideoTypeService
from src.services.video.video_combo_service import VideoComboService
from src.services.video.video_plan_service import VideoPlanService
from src.services.video.video_script_service import VideoScriptService
from src.services.ops.planning_service import PlanningService
from src.services.ops.backup_service import BackupService
from src.services.ops.direction_generator import DirectionGenerator
from src.services.blog.calendar_page_generator import generate_calendar_html
from src.web.auth_middleware import AuthMiddleware
from src.web.feishu_auth import FeishuAuthService

logger = get_logger("web.h5")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Feishu Sticker Workbench")

auth_service = FeishuAuthService()

_session_secret = os.getenv("FEISHU_H5_SESSION_SECRET") or os.getenv("SESSION_SECRET")
if not _session_secret:
    _env = os.getenv("ENV", "development")
    if _env == "production":
        raise RuntimeError(
            "SESSION_SECRET or FEISHU_H5_SESSION_SECRET must be set in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    _session_secret = "dev-session-secret-NOT-FOR-PRODUCTION"
    logger.warning("Using insecure default session secret — set SESSION_SECRET for production")

app.add_middleware(AuthMiddleware, feishu_configured=auth_service.is_configured())
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    same_site="lax",
)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

OUTPUTS_DIR = Path("output/h5_jobs")
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")

BLOG_OUTPUTS_DIR = Path("output/blogs")
BLOG_OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/blog-outputs", StaticFiles(directory=str(BLOG_OUTPUTS_DIR)), name="blog-outputs")

PREVIEW_IMAGES_DIR = Path("data/output/images")
PREVIEW_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/preview-images", StaticFiles(directory=str(PREVIEW_IMAGES_DIR)), name="preview-images")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
trend_service = TrendService()
chat_sticker_svc = StickerAgentService()
chat_blog_svc = BlogAgentService()
video_type_svc = VideoTypeService(trend_service.db)
video_combo_svc = VideoComboService(trend_service.db)
video_plan_svc = VideoPlanService(trend_service.db)
video_script_svc = VideoScriptService(trend_service.db)
planning_svc = PlanningService(trend_service.db)
direction_gen = DirectionGenerator(db=trend_service.db)
backup_svc = BackupService()
scheduler = BackgroundScheduler()

@app.on_event("startup")
def start_scheduler():
    logger.info("Initializing APScheduler attached to FastAPI...")
    scheduler.add_job(
        trend_service.run_daily_pipelines,
        'cron',
        hour=6,
        minute=0,
    )
    scheduler.start()

@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown()


def _sse_response(q: queue.Queue) -> StreamingResponse:
    """Create a Server-Sent Events response from a task queue."""
    def _generate():
        while True:
            try:
                event = q.get(timeout=60)
                if event is None:
                    yield f"data: {_json.dumps({'type': 'done'})}\n\n"
                    break
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception:
                yield f"data: {_json.dumps({'type': 'heartbeat'})}\n\n"
    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _format_views(value: float | int) -> str:
    """Format large numbers: 1234567 -> '1.2M', 12345 -> '12.3K'."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


templates.env.globals["format_views"] = _format_views


def _to_output_url(file_path: str) -> str:
    """Convert an absolute/relative disk path to a web-accessible URL."""
    p = file_path.replace("\\", "/")

    if p.startswith("/preview-images/") or p.startswith("/outputs/"):
        return p

    for marker in ("data/output/images/", "output/images/"):
        idx = p.find(marker)
        if idx >= 0:
            return "/preview-images/" + p[idx + len(marker):]

    marker = "output/h5_jobs/"
    idx = p.find(marker)
    if idx >= 0:
        return "/outputs/" + p[idx + len(marker):]
    if p.startswith("output/h5_jobs"):
        return "/outputs/" + p[len("output/h5_jobs"):].lstrip("/")

    return p


templates.env.globals["to_output_url"] = _to_output_url


def _current_user(request: Request) -> dict | None:
    return request.session.get("user")


def _base_context(request: Request, **extra) -> dict:
    context = {
        "request": request,
        "current_user": _current_user(request),
        "feishu_login_ready": auth_service.is_configured(),
    }
    context.update(extra)
    return context


@app.on_event("startup")
def startup_sync() -> None:
    try:
        result = trend_service.sync()
        logger.info("Initial ops sync completed: %s", result)
    except Exception as exc:
        logger.warning("Initial ops sync skipped: %s", exc)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/auth/feishu/login")
def feishu_login(request: Request) -> RedirectResponse:
    if not auth_service.is_configured():
        raise HTTPException(status_code=400, detail="Feishu H5 auth is not configured")
    state = secrets.token_urlsafe(16)
    request.session["feishu_login_state"] = state
    return RedirectResponse(url=auth_service.build_login_url(state))


@app.get("/auth/feishu/callback")
def feishu_callback(request: Request, code: str | None = None, state: str | None = None) -> RedirectResponse:
    if not code:
        raise HTTPException(status_code=400, detail="Missing Feishu auth code")
    expected_state = request.session.get("feishu_login_state")
    if not state or not expected_state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid Feishu login state")
    try:
        token_data = auth_service.exchange_code(code)
        access_token = token_data.get("access_token", "")
        user = auth_service.fetch_user(access_token) if access_token else {}
        request.session.pop("feishu_login_state", None)
        request.session["user"] = user
        request.session["feishu_access_token"] = access_token
        next_url = _safe_next_url(request.session.pop("next_url", "/trends"))
        return RedirectResponse(url=next_url, status_code=303)
    except Exception as exc:
        logger.warning("Feishu callback failed: %s", exc)
        raise HTTPException(status_code=400, detail="飞书登录失败，请重试") from exc


@app.get("/auth/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.pop("user", None)
    request.session.pop("feishu_access_token", None)
    request.session.pop("feishu_login_state", None)
    return RedirectResponse(url="/login", status_code=303)


def _safe_next_url(url: str) -> str:
    """Validate redirect target to prevent open-redirect attacks."""
    if not url or not url.startswith("/") or url.startswith("//"):
        return "/trends"
    return url


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/trends"):
    safe_next = _safe_next_url(next)
    request.session["next_url"] = safe_next
    if _current_user(request):
        return RedirectResponse(url=safe_next, status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "feishu_login_ready": auth_service.is_configured(),
        },
    )


@app.get("/api/me")
def me(request: Request) -> dict:
    user = _current_user(request)
    if user:
        return {
            "authenticated": True,
            "auth_mode": "feishu_oidc",
            "user": user,
            "login_url": "/auth/feishu/login",
            "logout_url": "/auth/logout",
        }
    return {
        "authenticated": False,
        "user_id": os.getenv("FEISHU_H5_DEFAULT_USER_ID", "local-dev"),
        "name": os.getenv("FEISHU_H5_DEFAULT_USER_NAME", "Local Dev"),
        "auth_mode": "placeholder" if not auth_service.is_configured() else "feishu_oidc_ready",
        "login_url": "/auth/feishu/login",
        "logout_url": "/auth/logout",
    }


@app.post("/api/jobs/trigger")
def trigger_pipeline(background_tasks: BackgroundTasks) -> dict:
    job_id = trend_service.trigger_background_pipeline(background_tasks)
    return {"message": "Job triggered", "job_id": job_id}

@app.post("/api/tiktok/crawl")
def crawl_tiktok(background_tasks: BackgroundTasks) -> dict:
    try:
        # Instead of hanging the HTTP, let's just trigger crawl
        job_id = f"job_tiktok_{int(datetime.now().timestamp())}"
        trend_service.db.create_sys_task(job_id, "tiktok_crawl")
        background_tasks.add_task(trend_service.crawl_tiktok, job_id)
        return {"message": "TikTok crawl started", "job_id": job_id}
    except Exception as exc:
        logger.exception("TikTok crawl failed")
        return JSONResponse(status_code=500, content={"error": "TikTok 抓取启动失败，请查看日志"})


@app.get("/api/trends")
def api_trends(source_type: str | None = None) -> dict:
    return {"items": trend_service.list_trends(source_type)}


@app.get("/api/trends/{trend_id}")
def api_trend_detail(trend_id: str) -> dict:
    trend = trend_service.get_trend(trend_id)
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    return trend


@app.post("/api/trends/{trend_id}/approve")
def api_trend_approve(
    trend_id: str, request: Request, background_tasks: BackgroundTasks
) -> dict:
    user = _current_user(request)
    reviewer = (user or {}).get("name", "anonymous")
    trend = trend_service.approve_trend(trend_id, reviewed_by=reviewer)
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    if not trend_id.startswith(("tiktok:", "tk:")):
        trend_service.enqueue_brief_after_approve_if_needed(
            trend_id, reviewer, "approve_api", background_tasks
        )
    return trend_service.get_trend(trend_id) or trend


@app.post("/api/trends/{trend_id}/skip")
def api_trend_skip(trend_id: str, request: Request) -> dict:
    user = _current_user(request)
    reviewer = (user or {}).get("name", "anonymous")
    trend = trend_service.skip_trend(trend_id, reviewed_by=reviewer)
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    return trend


@app.post("/api/trends/{trend_id}/queue")
def api_trend_queue(request: Request, trend_id: str) -> dict:
    created_by = (_current_user(request) or {}).get("name") or "system"
    try:
        return trend_service.queue_trend(trend_id, created_by=created_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trends/{trend_id}/brief")
def api_save_brief(request: Request, trend_id: str, payload: dict) -> dict:
    edited_by = (_current_user(request) or {}).get("name") or "system"
    return trend_service.brief_service.save_brief(trend_id, payload, edited_by=edited_by)


@app.get("/api/trends/{trend_id}/brief-status")
def api_brief_status(trend_id: str) -> dict:
    brief = trend_service.db.get_brief(trend_id)
    if not brief:
        return {"status": "none", "has_content": False}
    return {
        "status": brief.get("brief_status", "none"),
        "has_content": bool(brief.get("brief_json")),
        "error": brief.get("source_ref", "") if brief.get("brief_status") == "failed" else "",
    }


@app.get("/api/trends/{trend_id}/brief-logs")
def api_brief_gen_logs(trend_id: str, limit: int = 100) -> dict:
    """Brief 生成/保存过程日志（数据库 + 同步写入 logs/ops.brief.log）。"""
    limit = min(max(1, limit), 500)
    trend = trend_service.get_trend(trend_id)
    if not trend:
        raise HTTPException(404, "Trend not found")
    return {"trend_id": trend_id, "logs": trend_service.db.list_brief_gen_logs(trend_id, limit=limit)}


@app.post("/api/trends/{trend_id}/retry-brief")
def api_retry_brief(trend_id: str, background_tasks: BackgroundTasks) -> dict:
    from src.models.ops import TrendBriefRecord
    trend = trend_service.get_trend(trend_id)
    if not trend:
        raise HTTPException(404, "Trend not found")
    trend_service.db.upsert_brief(TrendBriefRecord(
        trend_id=trend_id, brief_status="generating", brief_json={},
    ))
    trend_service.db.log_brief_generation(
        trend_id, "已排队重新生成 Brief（API retry-brief）", source="retry_api",
    )
    background_tasks.add_task(trend_service.generate_brief_background, trend_id)
    return {"ok": True, "status": "generating"}


# ── Theme Family API ────────────────────────────────────

@app.get("/api/trends/{trend_id}/family")
def api_get_family(trend_id: str) -> dict:
    family = trend_service.get_theme_family(trend_id)
    if not family:
        return {"family": None}
    return {"family": family}


@app.post("/api/trends/{trend_id}/family")
def api_generate_family(trend_id: str, background_tasks: BackgroundTasks) -> dict:
    """全部推倒重来（重新生成整个家族）。"""
    try:
        result = trend_service.generate_theme_family_background(trend_id)
        return {"ok": True, **result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trends/{trend_id}/family/regenerate-selected")
def api_regenerate_selected(trend_id: str, payload: dict) -> dict:
    """重新生成选中的子题材（替换模式）。"""
    ids = payload.get("subtheme_ids", [])
    if not ids:
        raise HTTPException(400, "No subtheme_ids provided")
    try:
        return {"ok": True, **trend_service.regenerate_selected_subthemes(trend_id, ids)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trends/{trend_id}/allocation")
def api_save_allocation(trend_id: str, payload: dict) -> dict:
    allocations = payload.get("allocations", [])
    if not allocations:
        raise HTTPException(400, "No allocations provided")
    return trend_service.save_allocation(trend_id, allocations)


@app.post("/api/trends/{trend_id}/confirm-ai-allocation")
def api_confirm_ai_allocation(trend_id: str) -> dict:
    try:
        return trend_service.confirm_ai_allocation(trend_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trends/{trend_id}/subtheme-briefs")
def api_generate_subtheme_briefs(trend_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        background_tasks.add_task(trend_service.generate_subtheme_briefs_background, trend_id)
        return {"ok": True, "status": "generating"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/trends/{trend_id}/queue-family")
def api_queue_family(request: Request, trend_id: str) -> dict:
    created_by = (_current_user(request) or {}).get("name") or "system"
    try:
        jobs = trend_service.queue_family(trend_id, created_by=created_by)
        return {"ok": True, "jobs_created": len(jobs), "jobs": jobs}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/subthemes/{subtheme_id}")
def api_get_subtheme(subtheme_id: int) -> dict:
    from trend_fetcher.trend_db import TrendDB
    tdb = TrendDB("data/ops_workbench.db")
    sub = tdb.get_subtheme(subtheme_id)
    if not sub:
        raise HTTPException(404, "Subtheme not found")
    brief = tdb.get_subtheme_brief(subtheme_id)
    sub["brief"] = brief
    return sub


@app.get("/api/subthemes/{subtheme_id}/brief")
def api_get_subtheme_brief(subtheme_id: int) -> dict:
    from trend_fetcher.trend_db import TrendDB
    tdb = TrendDB("data/ops_workbench.db")
    brief = tdb.get_subtheme_brief(subtheme_id)
    if not brief:
        return {"brief": None}
    return {"brief": brief}


@app.post("/api/subthemes/{subtheme_id}/queue")
def api_queue_subtheme(request: Request, subtheme_id: int) -> dict:
    created_by = (_current_user(request) or {}).get("name") or "system"
    try:
        return trend_service.queue_subtheme(subtheme_id, created_by=created_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Theme Family HTML Page ─────────────────────────────

@app.get("/trends/{trend_id}/family", response_class=HTMLResponse)
def page_family_detail(request: Request, trend_id: str):
    trend = trend_service.get_trend(trend_id)
    if not trend:
        return templates.TemplateResponse(
            request,
            "error.html",
            _base_context(request, message="Trend 不存在"),
            status_code=404,
        )
    family = trend_service.get_theme_family(trend_id)
    return templates.TemplateResponse(
        request,
        "family_detail.html",
        _base_context(
            request,
            trend=trend,
            family=family,
            trend_id=trend_id,
            page_title=f"主题家族 — {trend.get('trend_name') or trend.get('title') or trend_id}",
        ),
    )


@app.get("/api/jobs")
def api_jobs() -> dict:
    return {"items": trend_service.list_jobs()}


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str) -> dict:
    detail = trend_service.get_job_detail(job_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Job not found")
    return detail

@app.get("/api/sys_jobs/{job_id}/logs")
def api_sys_job_logs(job_id: str) -> dict:
    logs = trend_service.db.list_sys_task_logs(job_id)
    return {"logs": logs}


@app.get("/api/sys_jobs/{job_id}/status")
def api_sys_job_status(job_id: str) -> dict:
    row = trend_service.db.conn.execute(
        "SELECT id, status, completed_at FROM sys_task_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Job not found")
    return dict(row)


@app.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: str) -> dict:
    try:
        return trend_service.retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = trend_service.db
    pending_news = db.list_trends("news", status="pending")
    pending_tk = db.list_trends("tiktok", status="pending")
    approved = db.list_approved_trends()
    pending_produce = sum(1 for t in approved if t.get("queue_status") in (None, "pending"))
    jobs = db.list_jobs()
    running_jobs = sum(1 for j in jobs if j.get("status") == "running")
    completed_packs = sum(1 for j in jobs if j.get("status") == "completed")
    blogs = db.list_blog_drafts()

    recent_jobs = [j for j in jobs if j.get("status") == "completed"][:5]

    reviewed = db.conn.execute(
        "SELECT * FROM trend_items WHERE review_status IN ('approved','skipped') "
        "ORDER BY reviewed_at DESC LIMIT 5"
    ).fetchall()
    recent_reviews = []
    for r in reviewed:
        d = dict(r)
        d["name"] = d.get("trend_name") or d.get("id", "")
        recent_reviews.append(d)

    stats = {
        "pending_review": len(pending_news) + len(pending_tk),
        "pending_produce": pending_produce,
        "running_jobs": running_jobs,
        "completed_packs": completed_packs,
        "blog_count": len(blogs),
    }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _base_context(
            request,
            stats=stats,
            recent_jobs=recent_jobs,
            recent_reviews=recent_reviews,
            page_title="工作台",
        ),
    )


@app.get("/trends", response_class=HTMLResponse)
def trends_page(request: Request):
    cn_tz = timezone(timedelta(hours=8))
    today = datetime.now(cn_tz).strftime("%Y-%m-%d")

    news_items = trend_service.list_trends("news", status=None, batch_date=today)
    tiktok_items = trend_service.list_trends("tiktok", status=None, batch_date=today)

    # 今日无数据则 fallback 到最近一次批次
    if not news_items:
        news_items = trend_service.list_trends("news", status=None)
    if not tiktok_items:
        tiktok_items = trend_service.list_trends("tiktok", status=None)

    batch_date_news = news_items[0].get("batch_date", today) if news_items else today
    batch_date_tk = tiktok_items[0].get("batch_date", today) if tiktok_items else today
    display_date = batch_date_news if batch_date_news == batch_date_tk else f"{batch_date_news} / {batch_date_tk}"

    return templates.TemplateResponse(
        request,
        "trends.html",
        _base_context(
            request,
            news_items=news_items,
            tiktok_items=tiktok_items,
            today_date=display_date,
            page_title="热点咨询",
        ),
    )


@app.get("/hot-news", response_class=HTMLResponse)
def hot_news_page(request: Request, page: int = 1,
                  sort: str = 'created_at', dir: str = 'desc',
                  date_from: str = '', date_to: str = ''):
    page = max(1, page)
    per_page = 10
    offset = (page - 1) * per_page
    items, total = trend_service.db.list_raw_news(
        limit=per_page, offset=offset,
        sort_by=sort, sort_dir=dir,
        date_from=date_from, date_to=date_to,
    )
    total_pages = (total + per_page - 1) // per_page
    
    # Format timestamps for clean display
    for item in items:
        raw_ts = item.get("created_at", "")
        if raw_ts and len(raw_ts) >= 16:
            item["created_at_display"] = raw_ts[:10] + " " + raw_ts[11:16]
        else:
            item["created_at_display"] = raw_ts or "-"
        
        pub = item.get("published_at", "")
        item["published_at_display"] = pub[:16] if pub and len(pub) >= 16 else (pub or "")
    
    return templates.TemplateResponse(
        request,
        "hot_news.html",
        _base_context(
            request,
            items=items,
            page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            sort=sort, dir=dir,
            date_from=date_from, date_to=date_to,
            page_title="热点新闻",
        ),
    )

@app.get("/tk-topics", response_class=HTMLResponse)
def tk_topics_page(request: Request, page: int = 1,
                   sort: str = 'video_views', dir: str = 'desc',
                   date_from: str = '', date_to: str = ''):
    page = max(1, page)
    per_page = 20
    offset = (page - 1) * per_page
    items, total = trend_service.db.list_tk_hashtags_paged(
        limit=per_page, offset=offset,
        sort_by=sort, sort_dir=dir,
        date_from=date_from, date_to=date_to,
    )
    total_pages = (total + per_page - 1) // per_page

    for item in items:
        for field in ("crawled_at", "first_seen_at", "last_seen_at"):
            raw = item.get(field, "") or ""
            item[f"{field}_display"] = (raw[:10] + " " + raw[11:16]) if len(raw) >= 16 else (raw or "-")

    return templates.TemplateResponse(
        request,
        "tk_topics.html",
        _base_context(
            request,
            items=items,
            page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            sort=sort, dir=dir,
            date_from=date_from, date_to=date_to,
            page_title="TK话题",
        ),
    )


def _fmt_items_ts(items: list) -> list:
    """Format created_at / updated_at / reviewed_at timestamps to readable Chinese format."""
    for item in items:
        for field in ("created_at", "updated_at", "reviewed_at"):
            raw = item.get(field, "") or ""
            if len(raw) >= 16:
                item[f"{field}_display"] = raw[:10] + " " + raw[11:16]
            else:
                item[f"{field}_display"] = raw or "-"
    return items


@app.get("/aggregated-topics", response_class=HTMLResponse)
def aggregated_topics_page(request: Request, q: str | None = None, page: int = 1,
                           sort: str = 'created_at', dir: str = 'desc',
                           date_from: str = '', date_to: str = ''):
    page = max(1, page)
    per_page = 10
    items, total = trend_service.list_archive_trends(
        search_text=q, page=page, per_page=per_page,
        sort_by=sort, sort_dir=dir, date_from=date_from, date_to=date_to,
    )
    total_pages = (total + per_page - 1) // per_page
    _fmt_items_ts(items)
    return templates.TemplateResponse(
        request,
        "aggregated_topics.html",
        _base_context(
            request,
            items=items,
            search_query=q or "",
            page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            sort=sort, dir=dir,
            date_from=date_from, date_to=date_to,
            page_title="聚合话题",
        ),
    )


@app.get("/approved", response_class=HTMLResponse)
def approved_page(request: Request):
    items = trend_service.list_approved_trends()
    _fmt_items_ts(items)
    return templates.TemplateResponse(
        request,
        "approved.html",
        _base_context(
            request,
            items=items,
            page_title="已采纳大盘",
        ),
    )

@app.get("/archive", response_class=HTMLResponse)
def archive_page(request: Request, q: str | None = None, page: int = 1,
                 sort: str = 'created_at', dir: str = 'desc',
                 date_from: str = '', date_to: str = ''):
    page = max(1, page)
    per_page = 20
    items, total = trend_service.list_archive_trends(
        search_text=q, page=page, per_page=per_page,
        sort_by=sort, sort_dir=dir, date_from=date_from, date_to=date_to,
    )
    total_pages = (total + per_page - 1) // per_page
    _fmt_items_ts(items)
    return templates.TemplateResponse(
        request,
        "archive.html",
        _base_context(
            request,
            items=items,
            search_query=q or "",
            page=page,
            total_pages=total_pages,
            total=total,
            sort=sort, dir=dir,
            date_from=date_from, date_to=date_to,
            page_title="数据全档案",
        ),
    )

@app.get("/trends/{trend_id}", response_class=HTMLResponse)
def trend_detail_page(request: Request, trend_id: str):
    import json as _json
    trend = trend_service.get_trend(trend_id)
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")

    tk_extra = {}
    raw = trend.get("raw_payload", {})
    if trend.get("source_type") == "tiktok":
        hashtag = raw.get("hashtag", {})
        ld = _json.loads(hashtag.get("list_data_json", "{}")) if isinstance(hashtag.get("list_data_json"), str) else hashtag.get("list_data_json") or {}
        dd = _json.loads(hashtag.get("detail_data_json") or "null") or {} if isinstance(hashtag.get("detail_data_json"), str) else hashtag.get("detail_data_json") or {}
        tk_extra["related_hashtags"] = [h.get("hashtagName", "") for h in dd.get("relatedHashtags", []) if h.get("hashtagName")]
        tk_extra["audience_interests"] = [a.get("interestInfo", {}).get("value", "") for a in dd.get("audienceInterests", []) if a.get("interestInfo")]

        from trend_fetcher.topic_prompts import _ts_to_date
        raw_trend = dd.get("trend") or ld.get("trend") or []
        tk_extra["trend_data"] = []
        for p in raw_trend[-14:]:
            t = p.get("time")
            date = _ts_to_date(t) if isinstance(t, (int, float)) else p.get("date", "")
            tk_extra["trend_data"].append({"date": date, "value": p.get("value", 0)})
        _age_map = {1: "13-17", 2: "18-24", 3: "25-34", 4: "35-44", 5: "45-54", 6: "55+"}
        tk_extra["audience_ages"] = [
            f"{_age_map.get(int(a.get('ageLevel', 0)), a.get('ageLevel', ''))} ({a.get('score', 0)}%)"
            for a in sorted(dd.get("audienceAges", []), key=lambda x: x.get("score", 0), reverse=True)
            if a.get("score", 0) > 0
        ]
        tk_extra["top_regions"] = [a.get("countryInfo", {}).get("value", "") for a in dd.get("audienceCountries", [])[:5] if a.get("countryInfo")]
        tk_extra["description"] = dd.get("description", "")
        tk_extra["video_views"] = ld.get("video_views", 0)
        tk_extra["publish_cnt"] = ld.get("publish_cnt", 0)
        longevity = dd.get("longevity") or {}
        tk_extra["popular_days"] = longevity.get("popularDays", 0)
        creators = _json.loads(hashtag.get("creators_raw_json", "[]")) if isinstance(hashtag.get("creators_raw_json"), str) else hashtag.get("creators_raw_json") or []
        tk_extra["top_creators"] = [c.get("nick_name", "") for c in creators[:5] if c.get("nick_name")]

    brief = trend.get("brief") or {"brief_json": {}}
    bj = brief.get("brief_json") or {}
    raw_resp = bj.get("_raw_response", "")
    clean_bj = {k: v for k, v in bj.items() if not k.startswith("_") and v}
    if not clean_bj and raw_resp:
        try:
            from trend_fetcher.topic_prompts import parse_brief_response
            re_parsed = parse_brief_response(raw_resp)
            clean_bj = {k: v for k, v in re_parsed.items() if k != "brief_status" and v}
        except Exception:
            pass
    brief["brief_json"] = clean_bj

    return templates.TemplateResponse(
        request,
        "trend_detail.html",
        _base_context(
            request,
            trend=trend,
            brief=brief,
            tk_extra=tk_extra,
            page_title=trend.get("trend_name") or trend.get("title") or trend_id,
        ),
    )


@app.post("/trends/{trend_id}/brief", response_class=HTMLResponse)
def save_brief_form(request: Request, trend_id: str, brief_json: str = Form(...)):
    import json

    trend = trend_service.get_trend(trend_id)
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    try:
        payload = json.loads(brief_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
    edited_by = (_current_user(request) or {}).get("name") or "system"
    trend_service.brief_service.save_brief(trend_id, payload, edited_by=edited_by)
    return RedirectResponse(url=f"/trends/{trend_id}", status_code=303)


@app.post("/trends/{trend_id}/approve", response_class=HTMLResponse)
def approve_form(request: Request, trend_id: str, background_tasks: BackgroundTasks):
    reviewer = (_current_user(request) or {}).get("name", "anonymous")
    trend_service.approve_trend(trend_id, reviewed_by=reviewer)
    if not trend_id.startswith(("tiktok:", "tk:")):
        trend_service.enqueue_brief_after_approve_if_needed(
            trend_id, reviewer, "approve_form", background_tasks
        )
    return RedirectResponse(url=f"/trends/{trend_id}", status_code=303)


@app.post("/trends/{trend_id}/skip", response_class=HTMLResponse)
def skip_form(request: Request, trend_id: str):
    reviewer = (_current_user(request) or {}).get("name", "anonymous")
    trend_service.skip_trend(trend_id, reviewed_by=reviewer)
    return RedirectResponse(url=f"/trends/{trend_id}", status_code=303)


@app.post("/trends/{trend_id}/queue", response_class=HTMLResponse)
def queue_form(request: Request, trend_id: str):
    created_by = (_current_user(request) or {}).get("name") or "system"
    try:
        trend_service.queue_trend(trend_id, created_by=created_by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/jobs", status_code=303)


@app.post("/trends/{trend_id}/restore", response_class=HTMLResponse)
def restore_form(request: Request, trend_id: str):
    reviewer = (_current_user(request) or {}).get("name") or "system"
    trend_service.restore_trend(trend_id, restored_by=reviewer)
    return RedirectResponse(url=f"/trends/{trend_id}", status_code=303)


@app.post("/api/trends/batch-queue")
def batch_queue(request: Request, payload: dict):
    trend_ids = payload.get("trend_ids", [])
    created_by = (_current_user(request) or {}).get("name") or "system"
    results = []
    for tid in trend_ids:
        try:
            job = trend_service.queue_trend(tid, created_by=created_by)
            results.append({"trend_id": tid, "ok": True, "job_id": job.get("id")})
        except Exception as exc:
            logger.warning("batch-queue failed for %s: %s", tid, exc)
            results.append({"trend_id": tid, "ok": False, "error": "排队失败"})
    return {"results": results}


@app.get("/stickers", response_class=HTMLResponse)
def stickers_page(request: Request):
    jobs_with_images = trend_service.db.list_completed_jobs_with_images()
    return templates.TemplateResponse(
        request,
        "sticker_gallery.html",
        _base_context(
            request,
            jobs=jobs_with_images,
            page_title="卡贴画廊",
        ),
    )


@app.get("/packs", response_class=HTMLResponse)
def packs_page(request: Request):
    jobs_with_images = trend_service.db.list_completed_jobs_with_images()
    return templates.TemplateResponse(
        request,
        "pack_manager.html",
        _base_context(
            request,
            jobs=jobs_with_images,
            page_title="卡包管理",
        ),
    )


@app.get("/blogs", response_class=HTMLResponse)
def blogs_page(request: Request):
    drafts = trend_service.db.list_blog_drafts()
    return templates.TemplateResponse(
        request,
        "blog_manager.html",
        _base_context(request, drafts=drafts, page_title="Blog 管理"),
    )


@app.get("/tools/chat-sticker", response_class=HTMLResponse)
def chat_sticker_page(request: Request):
    return templates.TemplateResponse(
        request,
        "chat_sticker.html",
        _base_context(request, page_title="对话生成卡贴"),
    )


@app.get("/tools/chat-blog", response_class=HTMLResponse)
def chat_blog_page(request: Request):
    return templates.TemplateResponse(
        request,
        "chat_blog.html",
        _base_context(request, page_title="对话生成 Blog"),
    )


# ── Chat Sticker API (Agent Mode) ───────────────────────────


@app.post("/api/tools/chat-sticker/send")
def chat_sticker_send(request: Request, payload: dict):
    session_id = payload.get("session_id", "default")
    message = payload.get("message", "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    created_by = (_current_user(request) or {}).get("name") or "system"
    result = chat_sticker_svc.process_message(session_id, message, created_by=created_by)
    return result


@app.post("/api/tools/chat-sticker/reset")
def chat_sticker_reset(payload: dict):
    session_id = payload.get("session_id", "default")
    chat_sticker_svc.reset(session_id)
    return {"ok": True}


@app.post("/api/tools/chat-sticker/restore")
def chat_sticker_restore(payload: dict):
    session_id = payload.get("session_id", "")
    if not session_id:
        raise HTTPException(400, "session_id is required")
    messages = chat_sticker_svc.restore_session(session_id)
    return {"messages": messages}


@app.get("/api/tools/chat-sticker/stream/{task_id}")
def chat_sticker_stream(task_id: str):
    q = chat_sticker_svc.get_task_queue(task_id)
    if not q:
        raise HTTPException(404, "Task not found")
    return _sse_response(q)


# ── Chat Blog API ───────────────────────────────────────────


@app.post("/api/tools/chat-blog/send")
def chat_blog_send(request: Request, payload: dict):
    session_id = payload.get("session_id", "default")
    message = payload.get("message", "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    created_by = (_current_user(request) or {}).get("name") or "system"
    result = chat_blog_svc.process_message(session_id, message, created_by=created_by)
    return result


@app.post("/api/tools/chat-blog/reset")
def chat_blog_reset(payload: dict):
    session_id = payload.get("session_id", "default")
    chat_blog_svc.reset(session_id)
    return {"ok": True}


@app.post("/api/tools/chat-blog/restore")
def chat_blog_restore(payload: dict):
    session_id = payload.get("session_id", "")
    if not session_id:
        raise HTTPException(400, "session_id is required")
    messages = chat_blog_svc.restore_session(session_id)
    return {"messages": messages}


@app.get("/api/tools/chat-blog/stream/{task_id}")
def chat_blog_stream(task_id: str):
    q = chat_blog_svc.get_task_queue(task_id)
    if not q:
        raise HTTPException(404, "Task not found")
    return _sse_response(q)


def _safe_folder_name(name: str) -> str:
    """Sanitize a string for use as a ZIP folder name."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name or 'pack')
    return name.strip()[:80] or 'pack'


_ALLOWED_OUTPUT_ROOT = Path("output").resolve()


def _is_safe_output_path(file_path: str) -> bool:
    """Verify a file path is within the allowed output directory."""
    try:
        resolved = Path(file_path).resolve()
        return str(resolved).startswith(str(_ALLOWED_OUTPUT_ROOT))
    except (OSError, ValueError):
        return False


def _content_disposition(filename: str) -> str:
    """Build a Content-Disposition header that handles non-ASCII filenames (RFC 5987)."""
    from urllib.parse import quote
    try:
        filename.encode("latin-1")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        encoded = quote(filename, safe="")
        return f"attachment; filename*=UTF-8''{encoded}"


@app.get("/api/stickers/{job_id}/download")
def download_single_pack(job_id: str):
    paths = trend_service.db.get_job_image_paths(job_id)
    if not paths:
        raise HTTPException(status_code=404, detail="No images found for this job")
    job = trend_service.db.get_job(job_id)
    pack_name = _safe_folder_name((job or {}).get("trend_name", job_id))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in paths:
            if not _is_safe_output_path(fp):
                continue
            p = Path(fp)
            if p.is_file():
                zf.write(p, f"{pack_name}/{p.name}")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": _content_disposition(f"{pack_name}.zip")},
    )


@app.post("/api/stickers/download")
def download_batch_packs(payload: dict):
    job_ids = payload.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="No job_ids provided")
    jobs_data = trend_service.db.get_jobs_image_paths(job_ids)
    if not jobs_data:
        raise HTTPException(status_code=404, detail="No images found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen_folders: dict[str, int] = {}
        for jid, info in jobs_data.items():
            folder = _safe_folder_name(info["trend_name"])
            if folder in seen_folders:
                seen_folders[folder] += 1
                folder = f"{folder}_{seen_folders[folder]}"
            else:
                seen_folders[folder] = 0
            for fp in info["paths"]:
                if not _is_safe_output_path(fp):
                    continue
                p = Path(fp)
                if p.is_file():
                    zf.write(p, f"{folder}/{p.name}")
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="sticker_packs_{ts}.zip"'},
    )


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request):
    jobs = trend_service.list_jobs()
    sys_jobs = trend_service.db.list_sys_tasks(limit=30)
    return templates.TemplateResponse(
        request,
        "jobs.html",
        _base_context(
            request,
            jobs=jobs,
            sys_jobs=sys_jobs,
            page_title="任务监控",
        ),
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail_page(request: Request, job_id: str):
    job = trend_service.get_job_detail(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        _base_context(
            request,
            job=job,
            outputs=job.get("outputs", []),
            page_title=job.get("trend_name") or job_id,
        ),
    )


@app.get("/system/backups", response_class=HTMLResponse)
def backups_page(request: Request):
    return templates.TemplateResponse(
        request,
        "backups.html",
        _base_context(request, page_title="数据库备份"),
    )


@app.get("/api/system/backups")
def api_list_backups():
    return {"backups": backup_svc.list_backups()}


@app.post("/api/system/backup")
def api_create_backup():
    try:
        result = backup_svc.create_backup()
        return {"ok": True, **result}
    except Exception as exc:
        logger.exception("Backup failed: %s", exc)
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=500)


@app.get("/api/system/backups/{filename}/download")
def api_download_backup(filename: str):
    path = backup_svc.get_backup_path(filename)
    if not path:
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
    )


@app.delete("/api/system/backups/{filename}")
def api_delete_backup(filename: str):
    if not backup_svc.delete_backup(filename):
        return JSONResponse({"ok": False, "detail": "备份不存在或无效"}, status_code=404)
    return {"ok": True}


# --- Blog Management API ---

@app.get("/api/blogs/{blog_id}")
def api_get_blog(blog_id: str):
    draft = trend_service.db.get_blog_draft(blog_id)
    if not draft:
        raise HTTPException(404, "Blog not found")
    content = draft.get("content", "")
    content = re.sub(
        r"!\[([^\]]*)\]\((?!https?://|/)([^)]+)\)",
        r"![\1](/blog-outputs/images/\2)",
        content,
    )
    draft["content"] = content
    return draft


@app.get("/api/blogs/{blog_id}/download")
def api_download_blog(blog_id: str):
    draft = trend_service.db.get_blog_draft(blog_id)
    if not draft:
        raise HTTPException(404, "Blog not found")
    filename = (draft.get("url_slug") or draft.get("meta_title") or blog_id).replace(" ", "_") + ".md"
    from starlette.responses import Response
    return Response(
        content=draft.get("content", ""),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@app.post("/api/blogs/{blog_id}/publish")
def api_publish_blog(blog_id: str, payload: dict):
    draft = trend_service.db.get_blog_draft(blog_id)
    if not draft:
        raise HTTPException(404, "Blog not found")

    published_live = payload.get("published", False)

    try:
        from src.services.blog.shopify_converter import ShopifyConverter
        from src.services.blog.shopify_publisher import ShopifyPublisher

        converter = ShopifyConverter()
        article = converter.convert_draft(
            content_md=draft.get("content", ""),
            meta_title=draft.get("meta_title", ""),
            meta_description=draft.get("meta_description", ""),
            url_slug=draft.get("url_slug", ""),
            keywords=draft.get("seo_keywords", []),
        )
        publisher = ShopifyPublisher()
        result = publisher.publish(article=article, published=published_live)

        new_status = "published" if published_live else "shopify_draft"
        shopify_id = str(result.get("id", ""))
        shopify_handle = result.get("handle", "")
        shopify_url = f"https://{publisher.shop_domain}/blogs/{publisher.blog_handle}/{shopify_handle}" if shopify_handle else ""

        trend_service.db.update_blog_publish_status(
            blog_id,
            publish_status=new_status,
            shopify_article_id=shopify_id,
            shopify_url=shopify_url,
        )
        return {"status": new_status, "shopify_url": shopify_url, "shopify_article_id": shopify_id}
    except ValueError as e:
        raise HTTPException(400, "Shopify 未配置或凭证错误")
    except Exception as e:
        logger.error("Blog publish failed for %s: %s", blog_id, e, exc_info=True)
        raise HTTPException(500, "发布失败，请查看日志")


@app.delete("/api/blogs/{blog_id}")
def api_delete_blog(blog_id: str):
    ok = trend_service.db.delete_blog_draft(blog_id)
    if not ok:
        raise HTTPException(404, "Blog not found")
    return {"ok": True}


# ── Chat History ────────────────────────────────────────────


@app.get("/chat-history", response_class=HTMLResponse)
def chat_history_page(request: Request):
    agent_type = request.query_params.get("type", "")
    sessions = trend_service.db.list_chat_sessions(
        agent_type=agent_type or None, limit=100,
    )
    return templates.TemplateResponse(
        request,
        "chat_history.html",
        _base_context(request, sessions=sessions, filter_type=agent_type, page_title="对话记录"),
    )


@app.get("/api/chat-history/sessions")
def api_list_chat_sessions(request: Request):
    agent_type = request.query_params.get("type", "")
    try:
        limit = min(max(1, int(request.query_params.get("limit", "100"))), 500)
    except (ValueError, TypeError):
        limit = 100
    sessions = trend_service.db.list_chat_sessions(agent_type=agent_type or None, limit=limit)
    return {"sessions": sessions}


@app.get("/api/chat-history/sessions/{session_id}")
def api_get_chat_messages(session_id: str):
    messages = trend_service.db.get_chat_messages(session_id)
    if not messages:
        raise HTTPException(404, "Session not found or empty")
    return {"messages": messages}


@app.delete("/api/chat-history/sessions/{session_id}")
def api_delete_chat_session(session_id: str):
    ok = trend_service.db.delete_chat_session(session_id)
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"ok": True}


@app.post("/api/chat-history/sessions/batch-delete")
def api_batch_delete_chat_sessions(payload: dict):
    ids = payload.get("ids", [])
    if not ids:
        raise HTTPException(400, "ids is required")
    count = trend_service.db.delete_chat_sessions_batch(ids)
    return {"deleted": count}


# ── Video Type + Combo Script Generation System ─────────────


@app.get("/api/v2/video-types")
def api_v2_list_video_types(active_only: bool = False):
    types = video_type_svc.list_types(active_only=active_only)
    return {"types": types}


@app.get("/api/v2/video-types/{type_id}")
def api_v2_get_video_type(type_id: str):
    t = video_type_svc.get_type(type_id)
    if not t:
        raise HTTPException(404, "Video type not found")
    return t


@app.post("/api/v2/video-types")
def api_v2_save_video_type(payload: dict):
    try:
        video_type_svc.save_type(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.put("/api/v2/video-types/{type_id}/toggle")
def api_v2_toggle_video_type(type_id: str, payload: dict):
    is_active = payload.get("is_active", True)
    ok = video_type_svc.toggle_active(type_id, is_active)
    if not ok:
        raise HTTPException(404, "Video type not found")
    return {"ok": True}


@app.get("/api/v2/video-combos")
def api_v2_list_video_combos(active_only: bool = False):
    combos = video_combo_svc.list_combos(active_only=active_only)
    return {"combos": combos}


@app.get("/api/v2/video-combos/{combo_id}")
def api_v2_get_video_combo(combo_id: str):
    c = video_combo_svc.get_combo(combo_id)
    if not c:
        raise HTTPException(404, "Video combo not found")
    return c


@app.post("/api/v2/video-combos")
def api_v2_save_video_combo(payload: dict):
    try:
        video_combo_svc.save_combo(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.put("/api/v2/video-combos/{combo_id}/toggle")
def api_v2_toggle_video_combo(combo_id: str, payload: dict):
    is_active = payload.get("is_active", True)
    ok = video_combo_svc.toggle_active(combo_id, is_active)
    if not ok:
        raise HTTPException(404, "Video combo not found")
    return {"ok": True}


@app.delete("/api/v2/video-combos/{combo_id}")
def api_v2_delete_video_combo(combo_id: str):
    ok = video_combo_svc.delete_combo(combo_id)
    if not ok:
        raise HTTPException(404, "Video combo not found")
    return {"ok": True}


@app.get("/api/v2/video-combos/{combo_id}/validate")
def api_v2_validate_video_combo(combo_id: str):
    issues = video_combo_svc.validate_combo(combo_id)
    return {"valid": len(issues) == 0, "issues": issues}


@app.post("/api/v2/video-plans/generate")
def api_v2_generate_video_plan(request: Request, payload: dict):
    combo_id = payload.get("combo_id", "")
    script_input = payload.get("script_input", {})
    job_id = payload.get("job_id", "")
    if not combo_id:
        raise HTTPException(400, "combo_id is required")
    created_by = (_current_user(request) or {}).get("name") or "system"
    try:
        plan = video_plan_svc.generate_plan(
            combo_id, script_input, job_id=job_id, created_by=created_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Video plan generation failed: %s", e, exc_info=True)
        raise HTTPException(500, "视频计划生成失败，请查看日志")
    return plan


@app.get("/api/v2/video-plans")
def api_v2_list_video_plans(job_id: str | None = None, combo_id: str | None = None):
    plans = trend_service.db.list_video_script_plans_v2(job_id=job_id, combo_id=combo_id)
    return {"plans": plans}


@app.get("/api/v2/video-plans/{plan_id}")
def api_v2_get_video_plan(plan_id: str):
    p = trend_service.db.get_video_script_plan_v2(plan_id)
    if not p:
        raise HTTPException(404, "Video plan not found")
    return p


@app.delete("/api/v2/video-plans/{plan_id}")
def api_v2_delete_video_plan(plan_id: str):
    ok = trend_service.db.delete_video_script_plan_v2(plan_id)
    if not ok:
        raise HTTPException(404, "Video plan not found")
    return {"ok": True}


@app.post("/api/v2/video-scripts/generate")
def api_v2_generate_video_script(request: Request, payload: dict):
    plan_id = payload.get("plan_id", "")
    if not plan_id:
        raise HTTPException(400, "plan_id is required")
    created_by = (_current_user(request) or {}).get("name") or "system"
    try:
        script = video_script_svc.generate_script(plan_id, created_by=created_by)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Video script generation failed: %s", e, exc_info=True)
        raise HTTPException(500, "视频脚本生成失败，请查看日志")
    return script


@app.post("/api/v2/video-scripts/generate-full")
def api_v2_generate_full(request: Request, payload: dict):
    """Convenience endpoint: plan → script in one call."""
    combo_id = payload.get("combo_id", "")
    script_input = payload.get("script_input", {})
    job_id = payload.get("job_id", "")
    if not combo_id:
        raise HTTPException(400, "combo_id is required")
    created_by = (_current_user(request) or {}).get("name") or "system"
    try:
        result = video_script_svc.generate_from_combo(
            combo_id, script_input, job_id=job_id, created_by=created_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Full video script generation failed: %s", e, exc_info=True)
        raise HTTPException(500, "视频脚本一键生成失败，请查看日志")
    return result


@app.get("/api/v2/video-scripts")
def api_v2_list_video_scripts(job_id: str | None = None, combo_id: str | None = None, plan_id: str | None = None):
    scripts = trend_service.db.list_video_scripts(job_id=job_id, combo_id=combo_id, plan_id=plan_id)
    return {"scripts": scripts}


@app.get("/api/v2/video-scripts/{script_id}")
def api_v2_get_video_script(script_id: str):
    s = trend_service.db.get_video_script(script_id)
    if not s:
        raise HTTPException(404, "Video script not found")
    return s


@app.delete("/api/v2/video-scripts/{script_id}")
def api_v2_delete_video_script(script_id: str):
    ok = trend_service.db.delete_video_script(script_id)
    if not ok:
        raise HTTPException(404, "Video script not found")
    return {"ok": True}


@app.post("/api/v2/video-scripts/assemble-input")
def api_v2_assemble_input(payload: dict):
    """Assemble rich VideoScriptInput from a job_id.

    Pulls trend brief, sticker data, planning direction details,
    pack style guide, and product selling points so that the video
    script AI has enough context to produce creative, product-specific scripts.
    """
    job_id = payload.get("job_id", "")
    if not job_id:
        raise HTTPException(400, "job_id is required")

    job = trend_service.db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    script_input: dict[str, Any] = {
        "job_id": job_id,
        "pack_id": job_id,
        "trend_topic": job.get("trend_name", ""),
        "platform": "tiktok",
    }

    trend_id = job.get("trend_id", "")

    # --- Trend brief data ---
    if trend_id and not trend_id.startswith("chat:"):
        brief_row = trend_service.db.get_brief(trend_id)
        if brief_row:
            bj = brief_row.get("brief_json", {})
            script_input["one_line_explanation"] = bj.get("one_line_explanation", "")
            script_input["emotional_hooks"] = bj.get("emotional_hooks", bj.get("emotional_core", []))
            ta = bj.get("target_audience", {})
            script_input["audience_persona"] = ta.get("profile", "")
            script_input["visual_symbols"] = bj.get("visual_symbols", [])
            script_input["use_cases"] = ta.get("usage_scenarios", [])
            script_input["one_line_product_angle"] = bj.get("product_goal", "")

    # --- Planning calendar context (event + design direction) ---
    if trend_id and trend_id.startswith("planning:"):
        event_id = trend_id.replace("planning:", "", 1)
        event = trend_service.db.get_planning_event(event_id)
        if event:
            script_input["event_context"] = {
                "title": event.get("title", ""),
                "category": event.get("category", ""),
                "date": event.get("start_date", ""),
                "end_date": event.get("end_date", ""),
                "description": event.get("short_description", ""),
            }
        directions = trend_service.db.list_directions(event_id)
        if directions:
            matched = directions[0]
            for d in directions:
                if d.get("job_id") == job_id:
                    matched = d
                    break
            script_input["design_direction"] = {
                "name": matched.get("name_en", ""),
                "name_zh": matched.get("name_zh", ""),
                "keywords": matched.get("keywords", ""),
                "design_elements": matched.get("design_elements", ""),
                "text_slogans": matched.get("text_slogans", ""),
                "decorative_elements": matched.get("decorative_elements", ""),
            }

    # --- Brand & materials ---
    brand = video_plan_svc.brand_profile
    materials_cfg = brand.get("materials", {})
    script_input["materials"] = materials_cfg.get("safe_claims", [])
    script_input["brand_tone"] = (brand.get("brand", {}).get("voice", ""))[:200]

    # --- Sticker descriptions (enriched) ---
    outputs = trend_service.db.list_outputs(job_id)
    descs: list[str] = []
    for out in outputs:
        if out.get("output_type") != "image":
            continue
        meta = out.get("metadata_json", {})
        sticker_type = meta.get("type", "")
        concept = meta.get("concept", "")
        name = meta.get("name", "")
        prompt_txt = meta.get("prompt", "")

        if concept:
            prefix = f"[{sticker_type}] " if sticker_type else ""
            desc = f"{prefix}{name} — {concept}" if name else f"{prefix}{concept}"
            descs.append(desc)
        elif name:
            desc = name
            if prompt_txt:
                desc += f" — {prompt_txt[:80]}"
            descs.append(desc)
        elif prompt_txt:
            descs.append(prompt_txt[:120])
    script_input["sticker_descriptions"] = descs
    if descs:
        script_input["hero_sticker"] = descs[0]
    if len(descs) > 3:
        script_input["collection_sheet"] = "available"

    # --- Pack style guide (from sticker_packs JSON if exists) ---
    _enrich_pack_style(script_input, job_id)

    # --- Product selling points ---
    sticker_count = len(descs) or "multiple"
    script_input["product_selling_points"] = [
        f"AI-designed unique sticker pack with {sticker_count} original designs",
        "Premium waterproof vinyl — durable, vibrant colors",
        "Perfect for laptops, water bottles, journals, phone cases",
        "Themed around trending topics — timely and relevant",
    ]
    if script_input.get("design_direction"):
        dd = script_input["design_direction"]
        if dd.get("text_slogans"):
            slogans = [s.strip() for s in dd["text_slogans"].split(",") if s.strip()]
            if slogans:
                script_input["product_selling_points"].append(
                    f"Features bold text designs: {', '.join(slogans[:3])}"
                )

    # --- Family context ---
    family_context = None
    fid = job.get("family_id")
    if fid:
        sibling_jobs = trend_service.db.list_jobs_by_family(fid)
        family_context = {
            "family_id": fid,
            "variant_label": job.get("variant_label", ""),
            "sibling_packs": [
                {"job_id": sj["id"], "trend_name": sj["trend_name"], "status": sj["status"], "variant_label": sj.get("variant_label", "")}
                for sj in sibling_jobs if sj["id"] != job_id
            ],
        }

    return {"script_input": script_input, "family_context": family_context}


def _enrich_pack_style(script_input: dict[str, Any], job_id: str) -> None:
    """Try to load style_guide and theme_content from the pack JSON on disk."""
    import glob as _glob

    pack_dir = Path("data/output/sticker_packs")
    if not pack_dir.exists():
        return
    for f in sorted(pack_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("id", "") != job_id and job_id not in f.name:
            continue
        sg = data.get("style_guide", {})
        if sg:
            script_input["pack_style"] = {
                "art_style": sg.get("art_style", ""),
                "color_palette": sg.get("color_palette", {}),
                "mood": sg.get("mood", ""),
                "line_style": sg.get("line_style", ""),
                "typography_style": sg.get("typography_style", ""),
            }
        tc = data.get("theme_content", {})
        if tc:
            script_input["theme_details"] = {
                "theme_english": tc.get("theme_english", ""),
                "visual_keywords": tc.get("visual_keywords", []),
                "cultural_context": tc.get("cultural_context", ""),
                "mood_board": tc.get("mood_board", []),
            }
        break


# ── V2: HTML Pages ──────────────────────────────────────────


@app.get("/video-types", response_class=HTMLResponse)
def video_types_page(request: Request):
    types = video_type_svc.list_types()
    return templates.TemplateResponse(
        request,
        "video_types.html",
        _base_context(request, types=types, page_title="视频类型管理"),
    )


@app.get("/video-combos", response_class=HTMLResponse)
def video_combos_page(request: Request):
    combos = video_combo_svc.list_combos()
    types = video_type_svc.list_types(active_only=True)
    return templates.TemplateResponse(
        request,
        "video_combos.html",
        _base_context(request, combos=combos, types=types, page_title="组合管理"),
    )


@app.get("/video-studio", response_class=HTMLResponse)
def video_studio_page(request: Request):
    combos = video_combo_svc.list_combos(active_only=True)
    all_jobs = trend_service.db.list_jobs()
    jobs = [j for j in all_jobs if j.get("status") == "completed"]
    plans = trend_service.db.list_video_script_plans_v2()
    scripts = trend_service.db.list_video_scripts()

    family_groups: dict = {}
    for j in jobs:
        fid = j.get("family_id")
        if not fid:
            continue
        if fid not in family_groups:
            family_groups[fid] = {"label": j.get("trend_name") or fid, "total_images": 0}
        family_groups[fid]["total_images"] += j.get("image_count") or 0

    return templates.TemplateResponse(
        request,
        "video_studio.html",
        _base_context(
            request,
            combos=combos,
            jobs=jobs,
            plans=plans,
            scripts=scripts,
            family_groups=family_groups,
            page_title="脚本工作室",
        ),
    )


# ── Planning Calendar ─────────────────────────────────────


@app.get("/planning", response_class=HTMLResponse)
def planning_page(request: Request):
    now = datetime.now(timezone(timedelta(hours=8)))
    stats = planning_svc.get_stats()
    return templates.TemplateResponse(
        request,
        "planning.html",
        _base_context(
            request,
            page_title="选品日历",
            stats=stats,
            default_year=now.year,
            default_month=now.month,
        ),
    )


@app.get("/api/planning/events")
def api_planning_events(
    region: str | None = None,
    year: int | None = None,
    month: int | None = None,
):
    if not year or not month:
        now = datetime.now(timezone(timedelta(hours=8)))
        year = year or now.year
        month = month or now.month
    events = planning_svc.get_calendar_data(year, month, region or None)
    return {"events": events, "year": year, "month": month, "region": region}


@app.post("/api/planning/fetch")
def api_planning_fetch(
    request: Request,
    background_tasks: BackgroundTasks,
    region: str = Form("us"),
    year: int = Form(0),
    month: int = Form(0),
):
    if not year or not month:
        now = datetime.now(timezone(timedelta(hours=8)))
        year = year or now.year
        month = month or now.month

    if region == "all":
        background_tasks.add_task(planning_svc.fetch_all_regions, year, month)
        return {"ok": True, "message": f"Fetching all regions for {year}-{month:02d}"}
    else:
        background_tasks.add_task(planning_svc.fetch_events, region, year, month)
        return {"ok": True, "message": f"Fetching {region} for {year}-{month:02d}"}


@app.delete("/api/planning/events/{event_id}")
def api_planning_delete_event(event_id: str):
    ok = planning_svc.db.delete_planning_event(event_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"ok": True}


@app.get("/api/planning/stats")
def api_planning_stats():
    return planning_svc.get_stats()


@app.post("/api/planning/events/{event_id}/directions")
async def api_generate_directions(event_id: str, request: Request, background_tasks: BackgroundTasks):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    count = max(1, min(10, int(body.get("count", 2))))

    existing = trend_service.db.list_directions_by_event(event_id)
    if existing:
        return {"ok": True, "directions": existing, "cached": True}

    import threading, queue as _q

    result_q: _q.Queue = _q.Queue()

    def _run():
        try:
            dirs = direction_gen.generate_directions(event_id, count=count)
            result_q.put({"ok": True, "directions": dirs})
        except Exception as exc:
            result_q.put({"ok": False, "error": str(exc)})

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=180)

    if result_q.empty():
        return {"ok": False, "error": "Direction generation timed out"}
    return result_q.get()


@app.get("/api/planning/events/{event_id}/directions")
def api_list_directions(event_id: str):
    return {"directions": trend_service.db.list_directions_by_event(event_id)}


@app.post("/api/planning/events/{event_id}/directions/manual")
async def api_create_manual_direction(event_id: str, request: Request):
    """Create a direction from manual user input (no AI)."""
    event = trend_service.db.get_planning_event(event_id)
    if not event:
        raise HTTPException(404, "Event not found")
    body = await request.json()
    name_en = (body.get("name_en") or "").strip()
    if not name_en:
        return {"ok": False, "error": "name_en is required"}

    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    existing = trend_service.db.list_directions_by_event(event_id)
    rec = {
        "id": f"dir_{_uuid.uuid4().hex[:12]}",
        "event_id": event_id,
        "direction_index": len(existing),
        "name_en": name_en,
        "name_zh": (body.get("name_zh") or "").strip(),
        "keywords": (body.get("keywords") or "").strip(),
        "design_elements": (body.get("design_elements") or "").strip(),
        "text_slogans": (body.get("text_slogans") or "").strip(),
        "decorative_elements": (body.get("decorative_elements") or "").strip(),
        "preview_path": "",
        "preview_status": "pending",
        "gen_status": "pending",
        "job_id": "",
        "sticker_count": int(body.get("sticker_count", 10)),
        "created_at": _dt.now(_tz(_td(hours=8))).isoformat(),
    }
    trend_service.db.insert_planning_direction(rec)
    return {"ok": True, "direction": rec}


@app.patch("/api/planning/directions/{direction_id}")
async def api_update_direction(direction_id: str, request: Request):
    """Update direction fields (e.g. sticker_count)."""
    d = trend_service.db.get_direction(direction_id)
    if not d:
        raise HTTPException(404, "Direction not found")
    body = await request.json()
    allowed = {"sticker_count"}
    updates = {}
    for k in allowed:
        if k in body:
            updates[k] = int(body[k])
    if updates:
        trend_service.db.update_direction(direction_id, **updates)
    return {"ok": True, "updated": updates}


@app.post("/api/planning/directions/{direction_id}/preview")
def api_direction_preview(request: Request, direction_id: str):
    """Generate preview collection sheets (1 per 10 stickers), saved to gallery/pack management."""
    d = trend_service.db.get_direction(direction_id)
    if not d:
        raise HTTPException(404, "Direction not found")
    if d.get("preview_status") == "generating":
        return {"ok": True, "status": "generating", "message": "Preview already in progress"}
    created_by = (_current_user(request) or {}).get("name") or "system"
    result = direction_gen.generate_preview(direction_id, created_by=created_by)
    return {"ok": True, **result}


@app.post("/api/planning/directions/{direction_id}/generate")
def api_direction_generate(request: Request, direction_id: str):
    """Run full pipeline to generate individual sticker images, saved to gallery/pack management."""
    d = trend_service.db.get_direction(direction_id)
    if not d:
        raise HTTPException(404, "Direction not found")
    if d.get("gen_status") in ("generating",):
        return {"ok": True, "status": "generating", "job_id": d.get("job_id", "")}
    created_by = (_current_user(request) or {}).get("name") or "system"
    result = direction_gen.start_generation(direction_id, created_by=created_by)
    return {"ok": True, **result}


@app.get("/api/planning/directions/{direction_id}")
def api_direction_detail(direction_id: str):
    d = trend_service.db.get_direction(direction_id)
    if not d:
        raise HTTPException(404, "Direction not found")
    job = None
    if d.get("job_id"):
        job = trend_service.db.get_job(d["job_id"])
        if job:
            job["outputs"] = trend_service.db.list_outputs(d["job_id"])
    return {"direction": d, "job": job}


@app.post("/api/planning/publish-shopify")
def api_publish_shopify_calendar(
    year: int | None = None,
    month: int | None = None,
):
    """Generate static HTML calendar and push it to Shopify as a Page."""
    from src.services.blog.shopify_publisher import ShopifyPublisher

    now = datetime.now(timezone(timedelta(hours=8)))
    year = year or now.year
    month = month or now.month

    body_html = generate_calendar_html(
        db=planning_svc.db,
        year=year,
        month=month,
        title="Upcoming Holidays & Events — Sticker Collection Calendar",
    )

    try:
        publisher = ShopifyPublisher()
    except ValueError as e:
        raise HTTPException(500, f"Shopify credentials not configured: {e}")

    page = publisher.publish_page(
        title="Sticker Collection Calendar",
        body_html=body_html,
        handle="sticker-calendar",
        published=True,
    )

    return {
        "ok": True,
        "page_id": page.get("id"),
        "page_url": page.get("page_url", ""),
        "handle": page.get("handle", "sticker-calendar"),
    }


@app.post("/api/planning/unpublish-shopify")
def api_unpublish_shopify_calendar():
    """Set the Shopify calendar page to draft (hidden from storefront)."""
    from src.services.blog.shopify_publisher import ShopifyPublisher

    try:
        publisher = ShopifyPublisher()
    except ValueError as e:
        raise HTTPException(500, f"Shopify credentials not configured: {e}")

    try:
        page = publisher.unpublish_page(handle="sticker-calendar")
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"ok": True, "page_id": page.get("id"), "status": "draft"}


@app.delete("/api/planning/delete-shopify")
def api_delete_shopify_calendar():
    """Permanently delete the Shopify calendar page."""
    from src.services.blog.shopify_publisher import ShopifyPublisher

    try:
        publisher = ShopifyPublisher()
    except ValueError as e:
        raise HTTPException(500, f"Shopify credentials not configured: {e}")

    try:
        publisher.delete_page(handle="sticker-calendar")
    except ValueError as e:
        raise HTTPException(404, str(e))

    return {"ok": True, "deleted": True}


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return templates.TemplateResponse(
        request,
        "error.html",
        _base_context(
            request,
            page_title="Error",
            status_code=exc.status_code,
            detail=exc.detail,
        ),
        status_code=exc.status_code,
    )
