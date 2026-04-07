from __future__ import annotations

import io
import json as _json
import os
import queue
import re
import secrets
import zipfile
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Form, HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.core.logger import get_logger
from src.services.ops.trend_service import TrendService
from src.services.tools.sticker_agent import StickerAgentService
from src.services.tools.blog_agent import BlogAgentService
from src.web.auth_middleware import AuthMiddleware
from src.web.feishu_auth import FeishuAuthService

logger = get_logger("web.h5")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Feishu Sticker Workbench")

auth_service = FeishuAuthService()

app.add_middleware(AuthMiddleware, feishu_configured=auth_service.is_configured())
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("FEISHU_H5_SESSION_SECRET", os.getenv("SESSION_SECRET", "dev-session-secret")),
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

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
trend_service = TrendService()
chat_sticker_svc = StickerAgentService()
chat_blog_svc = BlogAgentService()
scheduler = BackgroundScheduler()

@app.on_event("startup")
def start_scheduler():
    logger.info("Initializing APScheduler attached to FastAPI...")
    scheduler.add_job(
        trend_service.run_daily_pipelines,
        'cron',
        hour=6,
        minute=0,
        args=[f"cron_job_{int(datetime.now().timestamp())}"]
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
    """Convert an absolute/relative disk path under output/h5_jobs to a /outputs/ URL."""
    p = file_path.replace("\\", "/")
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
        next_url = request.session.pop("next_url", "/trends")
        return RedirectResponse(url=next_url, status_code=303)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/auth/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.pop("user", None)
    request.session.pop("feishu_access_token", None)
    request.session.pop("feishu_login_state", None)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/trends"):
    request.session["next_url"] = next
    if _current_user(request):
        return RedirectResponse(url=next, status_code=303)
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
        return {"error": str(exc)}


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
def api_trend_approve(trend_id: str, request: Request) -> dict:
    user = _current_user(request)
    reviewer = (user or {}).get("name", "anonymous")
    trend = trend_service.approve_trend(trend_id, reviewed_by=reviewer)
    if not trend:
        raise HTTPException(status_code=404, detail="Trend not found")
    return trend


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
    news_items = trend_service.list_trends("news", status=None)
    tiktok_items = trend_service.list_trends("tiktok", status=None)
    return templates.TemplateResponse(
        request,
        "trends.html",
        _base_context(
            request,
            news_items=news_items,
            tiktok_items=tiktok_items,
            page_title="热点咨询",
        ),
    )


@app.get("/hot-news", response_class=HTMLResponse)
def hot_news_page(request: Request, page: int = 1,
                  sort: str = 'created_at', dir: str = 'desc',
                  date_from: str = '', date_to: str = ''):
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
def approve_form(request: Request, trend_id: str):
    reviewer = (_current_user(request) or {}).get("name", "anonymous")
    trend_service.approve_trend(trend_id, reviewed_by=reviewer)
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
            results.append({"trend_id": tid, "ok": False, "error": str(exc)})
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
            p = Path(fp)
            if p.is_file():
                zf.write(p, f"{pack_name}/{p.name}")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{pack_name}.zip"'},
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
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
        raise HTTPException(400, f"Shopify 未配置或凭证错误: {e}")
    except Exception as e:
        raise HTTPException(500, f"发布失败: {e}")


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
    limit = int(request.query_params.get("limit", "100"))
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
