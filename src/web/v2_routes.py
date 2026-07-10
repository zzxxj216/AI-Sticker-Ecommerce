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
import threading
import time
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
from src.services.shopify.sync import get_shopify_sync
from src.services.amazon.listings import get_amazon_service
from src.services.amazon import sync as amazon_sync
from src.services.tkshop.service import (
    TKSHOP_DEFAULT_SHOP,
    TKSHOP_SHOPS,
    TKShopService,
    get_shop_label,
    get_shop_labels_map,
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

# One AI image-gen job at a time in the web UI — concurrent background
# tasks on a shared singleton caused listing→master mirror to be skipped.
_auto_design_lock = threading.Lock()


def _run_local_auto_design_bg(local_product_id: int, **kwargs: Any) -> dict:
    with _auto_design_lock:
        return TKShopService().auto_design_images_for_local_product(
            local_product_id, **kwargs,
        )


def _run_listing_auto_design_bg(product_id: int, **kwargs: Any) -> dict:
    with _auto_design_lock:
        return TKShopService().auto_design_images(product_id, **kwargs)

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
# Sidebar nav + every page that surfaces a shop name uses this label.
# Keeps legacy env keys (main) mapped to display names but uses canonical keys in DB.
templates.env.globals["shop_label"] = get_shop_label
templates.env.globals["tkshop_shop_labels"] = get_shop_labels_map()

# 按角色的功能开关(各平台上架入口显隐)。模板用 {% if can_see(request, 'etsy') %}。
# 角色映射见 permissions.py + .env 的 FEISHU_ROLES_JSON; 默认 admin 看全部。
from src.web.permissions import can_see as _can_see  # noqa: E402
templates.env.globals["can_see"] = _can_see

router = APIRouter(prefix="/v2", tags=["v2"])

_CN_TZ = timezone(timedelta(hours=8))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DEFAULT_DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _auto_design_mode_params(
    *, mode: str, count: int, existing_images: int,
) -> tuple[bool, int]:
    """Map UI mode to auto_design_images replace/append parameters."""
    mode_norm = (mode or "all").strip().lower()
    replace_existing_ai = True
    secondary_count = max(0, count - 1)
    if mode_norm == "secondary" and existing_images >= 1:
        replace_existing_ai = False
        secondary_count = 1
    return replace_existing_ai, secondary_count


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


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_disk_path(disk_path: str) -> Path:
    norm = (disk_path or "").replace("\\", "/").strip()
    if not norm:
        return Path()
    p = Path(norm)
    if p.is_file():
        return p
    candidate = _PROJECT_ROOT / norm
    return candidate if candidate.is_file() else Path()


def _cover_cache_key(disk_path: str, created_at: int = 0) -> int:
    try:
        p = _resolve_disk_path(disk_path)
        if p.is_file():
            return max(int(created_at or 0), int(p.stat().st_mtime))
    except Exception:
        pass
    return int(created_at or 0)


def _versioned_url_from_path(disk_path: str, *, cache_key: int = 0) -> str:
    if not disk_path or not _resolve_disk_path(disk_path).is_file():
        return ""
    url = _path_to_v2_url(disk_path)
    if not url:
        return ""
    key = cache_key or _cover_cache_key(disk_path)
    return f"{url}?v={key}" if key else url


def _ensure_sqlite_row_factory(conn: sqlite3.Connection) -> None:
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row


def _row_val(row: Any, key: str, idx: int = 0) -> Any:
    if row is None:
        return None
    if hasattr(row, "keys"):
        return row[key]
    return row[idx]


def _first_ok_pack_preview_url(conn: sqlite3.Connection, pack_id: int) -> str:
    _ensure_sqlite_row_factory(conn)
    row = conn.execute(
        """SELECT pp.image_path
             FROM packs pk
        LEFT JOIN pack_previews pp ON pp.series_id = pk.series_id
                                  AND pp.generation_status = 'ok'
                                  AND COALESCE(pp.image_path,'') != ''
            WHERE pk.id = ?
            ORDER BY pp.preview_idx LIMIT 1""",
        (pack_id,),
    ).fetchone()
    image_path = _row_val(row, "image_path", 0)
    if image_path:
        return _versioned_url_from_path(str(image_path))
    return ""


def _resolve_local_product_cover_url(
    conn: sqlite3.Connection,
    *,
    local_product_id: int,
    pack_id: int,
    pack_cover_path: str = "",
    updated_at: int = 0,
) -> str:
    """Gallery main wins over packs.cover_image_path so deleting the main
    image does not keep showing a stale product draft file on disk."""
    _ensure_sqlite_row_factory(conn)
    row = conn.execute(
        """
        SELECT local_path, created_at FROM local_product_images
         WHERE local_product_id = ? AND role = 'main'
         ORDER BY sort_order ASC, id ASC
         LIMIT 1
        """,
        (local_product_id,),
    ).fetchone()
    local_path = _row_val(row, "local_path", 0)
    created_at = int(_row_val(row, "created_at", 1) or 0)
    if local_path and _resolve_disk_path(str(local_path)).is_file():
        return _versioned_url_from_path(str(local_path), cache_key=created_at)
    listing_row = conn.execute(
        """
        SELECT ti.local_path, ti.created_at
          FROM tkshop_products t
          JOIN tkshop_product_images ti ON ti.product_id = t.id
         WHERE t.local_product_id = ? AND ti.role = 'main'
         ORDER BY ti.created_at DESC, ti.id DESC
         LIMIT 1
        """,
        (local_product_id,),
    ).fetchone()
    listing_path = _row_val(listing_row, "local_path", 0)
    listing_at = int(_row_val(listing_row, "created_at", 1) or 0)
    if listing_path and _resolve_disk_path(str(listing_path)).is_file():
        return _versioned_url_from_path(str(listing_path), cache_key=listing_at)
    preview = _first_ok_pack_preview_url(conn, pack_id)
    if preview:
        return preview
    if pack_cover_path and _resolve_disk_path(pack_cover_path).is_file():
        return _versioned_url_from_path(
            pack_cover_path, cache_key=int(updated_at or 0),
        )
    return ""


def _resolve_listing_cover_url(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    pack_id: int,
    pack_cover_path: str = "",
    updated_at: int = 0,
) -> str:
    _ensure_sqlite_row_factory(conn)
    row = conn.execute(
        """
        SELECT local_path, created_at FROM tkshop_product_images
         WHERE product_id = ? AND role = 'main'
         ORDER BY sort_order ASC, id ASC
         LIMIT 1
        """,
        (product_id,),
    ).fetchone()
    local_path = _row_val(row, "local_path", 0)
    created_at = int(_row_val(row, "created_at", 1) or 0)
    if local_path and _resolve_disk_path(str(local_path)).is_file():
        return _versioned_url_from_path(str(local_path), cache_key=created_at)
    preview = _first_ok_pack_preview_url(conn, pack_id)
    if preview:
        return preview
    if pack_cover_path and _resolve_disk_path(pack_cover_path).is_file():
        return _versioned_url_from_path(
            pack_cover_path, cache_key=int(updated_at or 0),
        )
    return ""


def _versioned_product_image_url(img: dict[str, Any]) -> str:
    p = img.get("local_path") or ""
    return _versioned_url_from_path(p, cache_key=_cover_cache_key(p, int(img.get("created_at") or 0)))


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


def _form_bool(form: Any, name: str, *, default: bool = False) -> bool:
    if hasattr(form, "getlist"):
        values = [str(v).strip().lower() for v in form.getlist(name)]
    else:
        raw = form.get(name)
        values = [] if raw is None else [str(raw).strip().lower()]
    if not values:
        return default
    return any(v in ("1", "on", "true", "yes") for v in values)


_UPLOAD_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_UPLOAD_EXTERNAL_PACK_SUFFIXES = _UPLOAD_IMAGE_SUFFIXES | {".pdf"}


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


async def _read_external_pack_asset(upload: Any) -> dict[str, Any] | None:
    filename = _upload_filename(upload)
    if not filename:
        return None
    suffix = Path(filename).suffix.lower()
    if suffix not in _UPLOAD_EXTERNAL_PACK_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported external pack file type: {filename}",
        )
    data = await upload.read()
    if not data:
        return None
    return {"filename": Path(filename).name, "data": data}


async def _external_pack_assets_from_form(form: Any) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for upload in form.getlist("source_files"):
        asset = await _read_external_pack_asset(upload)
        if asset:
            assets.append(asset)

    zip_upload = form.get("source_zip")
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
                        suffix = Path(name).suffix.lower()
                        if suffix not in _UPLOAD_EXTERNAL_PACK_SUFFIXES:
                            continue
                        data = zf.read(info)
                        if data:
                            assets.append({"filename": name, "data": data})
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail="source_zip is not a valid zip file")

    seen: dict[str, int] = {}
    deduped: list[dict[str, Any]] = []
    for asset in assets:
        name = asset["filename"]
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            stem = Path(name).stem
            suffix = Path(name).suffix
            asset = dict(asset)
            asset["filename"] = f"{stem}_{seen[key]}{suffix}"
        deduped.append(asset)
    return deduped


def _external_pack_name_from_filename(filename: str) -> str:
    stem = Path(filename or "external_pack").stem.strip()
    if stem.lower().endswith(".cdr"):
        stem = Path(stem).stem.strip()
    return (stem[:200] or "External Sticker Pack")


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
    for lp in pack.get("local_products", []):
        lp["created_human"] = _fmt_ts(lp.get("created_at"))
        lp["updated_human"] = _fmt_ts(lp.get("updated_at"))

    series_id = int(pack.get("series_id") or 0)
    preview_summary = pack.get("preview_summary") or {}
    sticker_summary = pack.get("sticker_summary") or {}
    pack["preview_task_running"] = bool(series_id and is_running(f"gen_previews:{series_id}"))
    pack["split_task_running"] = bool(series_id and is_running(f"split_series:{series_id}"))
    pack["external_ai_task_running"] = bool(
        pack.get("id") and is_running(f"external_pack_ai:{int(pack['id'])}")
    )
    pack["has_inflight_generation"] = bool(
        pack["preview_task_running"]
        or pack["split_task_running"]
        or pack["external_ai_task_running"]
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
    pack["downstream_count"] = (
        len(pack.get("videos", []))
        + len(pack.get("products", []))
        + len(pack.get("local_products", []))
    )
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


@router.get("/system/running-tasks", response_class=HTMLResponse)
def v2_running_tasks(request: Request):
    """At-a-glance view of in-flight background tasks (AI image gen,
    sticker split, caption gen, etc.). Read from the in-memory registry
    in ``src.web.background`` — DB-tracked jobs go under 调度任务 instead.
    """
    tasks = list_running()
    for t in tasks:
        t["started_human"] = _fmt_ts(t.get("started_at"))
        # Best-effort link-out for AI image tasks so the operator can jump
        # to the detail page that started it.
        tid = t.get("task_id", "")
        if tid.startswith("tkshop_auto_design_local:"):
            t["link"] = f"/v2/local-products/{tid.split(':', 1)[1]}"
        elif tid.startswith("tkshop_auto_design:"):
            t["link"] = f"/v2/products/{tid.split(':', 1)[1]}"
        else:
            t["link"] = ""
    return templates.TemplateResponse(
        "v2_running_tasks.html",
        {
            "request": request,
            "page_title": "后台任务",
            "tasks": tasks,
        },
    )


@router.get("/api/system/running-tasks")
def v2_running_tasks_json():
    """JSON variant for any UI that wants live data without a refresh."""
    return JSONResponse({"tasks": list_running()})


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


@router.post("/packs/batch-generate-products")
async def v2_packs_batch_generate_products(request: Request):
    """Multi-select 'batch-generate TK products' from the pack-manager.

    Form: ``pack_ids`` (repeated int), ``return_to`` (optional). Kicks off
    a single background daemon task that processes the selected packs with
    a 3-pack concurrency cap inside ``TKShopService.batch_create_products_from_packs``.
    Packs that already have any product row are filtered out by the service.

    The route returns immediately (303 back to /v2/packs) so the operator can
    keep working — task progress is visible on the system-health page and
    the affected packs gain a "查看 TKShop →" link once their first product
    row lands.
    """
    form = await request.form()
    raw_ids = form.getlist("pack_ids") if hasattr(form, "getlist") else []
    pack_ids: list[int] = []
    for v in raw_ids:
        try:
            pack_ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not pack_ids:
        raise HTTPException(status_code=400, detail="no pack_ids selected")
    svc = get_tkshop_service()
    task_id = f"tkshop_batch_gen:{int(time.time())}"
    # The service does its own concurrency cap, so we just fire-and-forget one
    # daemon thread for the whole batch.
    run_async(
        task_id,
        svc.batch_create_products_from_packs,
        pack_ids,
        label=f"批量生成 TK 商品 ({len(pack_ids)} 选)",
    )
    logger.info("batch-generate dispatched: %d packs (task=%s)", len(pack_ids), task_id)
    back = _safe_v2_redirect((form.get("return_to") or "").strip(), "/v2/packs")
    sep = "&" if "?" in back else "?"
    msg = f"已派发 {len(pack_ids)} 个卡包到后台生成（结果在「本地产品库」可见）"
    return RedirectResponse(
        url=f"{back}{sep}batch_summary={msg}",
        status_code=303,
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


@router.post("/packs/import-external")
async def v2_pack_import_external(request: Request):
    """Import operator-made pack files, then optionally AI-enrich/group previews."""
    form = await request.form()
    assets = await _external_pack_assets_from_form(form)
    if not assets:
        raise HTTPException(status_code=400, detail="upload at least one PDF or image file")
    display_name = (form.get("display_name") or "").strip()
    batch_each_file = _form_bool(form, "batch_each_file", default=False)
    auto_name = _form_bool(form, "auto_name", default=not bool(display_name))
    total_raw = (form.get("total_stickers") or "").strip()
    total_stickers = 0
    if total_raw:
        try:
            total_stickers = max(0, int(total_raw))
        except ValueError:
            raise HTTPException(status_code=400, detail="total_stickers must be a number")
    ai_enrich = _form_bool(form, "ai_enrich", default=True)
    group_previews = _form_bool(form, "group_previews", default=True)
    max_raw = (form.get("max_stickers_total") or form.get("max_stickers_per_preview") or "").strip()
    max_stickers = 0
    if max_raw:
        try:
            max_stickers = int(max_raw)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="max_stickers_total must be a number",
            )
    target_raw = (form.get("target_preview_count") or "").strip()
    target_preview_count = 0
    if target_raw:
        try:
            target_preview_count = max(0, int(target_raw))
        except ValueError:
            raise HTTPException(status_code=400, detail="target_preview_count must be a number")
    per_raw = (form.get("stickers_per_preview") or "").strip()
    stickers_per_preview = 0
    if per_raw:
        try:
            stickers_per_preview = max(0, int(per_raw))
        except ValueError:
            raise HTTPException(status_code=400, detail="stickers_per_preview must be a number")
    import_groups: list[tuple[str, list[dict[str, Any]], bool]] = []
    if batch_each_file:
        for asset in assets:
            file_name = _external_pack_name_from_filename(asset["filename"])
            name = f"{display_name} - {file_name}" if display_name else file_name
            import_groups.append((name, [asset], auto_name or not display_name))
    else:
        name = display_name or _external_pack_name_from_filename(assets[0]["filename"])
        import_groups.append((name, assets, auto_name or not display_name))

    svc = get_pack_service()
    imported: list[dict[str, Any]] = []
    try:
        for name, group_assets, group_auto_name in import_groups:
            result = svc.import_external_pack(
                display_name=name,
                source_assets=group_assets,
                total_stickers=total_stickers,
                style_anchor=(form.get("style_anchor") or "").strip(),
                palette=(form.get("palette") or "").strip(),
                pack_archetype=(form.get("pack_archetype") or "external_upload").strip(),
                topic_name=(form.get("topic_name") or "").strip(),
                auto_named=group_auto_name,
            )
            imported.append(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    kwargs: dict[str, Any] = {
        "split": False,
        "group_previews": group_previews,
    }
    if max_stickers > 0:
        kwargs["max_stickers_per_preview"] = max_stickers
    if target_preview_count > 0:
        kwargs["target_preview_count"] = target_preview_count
    if stickers_per_preview > 0:
        kwargs["stickers_per_preview"] = stickers_per_preview
    if ai_enrich:
        for result in imported:
            run_async(
                f"external_pack_ai:{result['pack_id']}",
                svc.enrich_external_pack_with_ai,
                int(result["pack_id"]),
                label=f"AI enrich external pack #{result['pack_id']}",
                **kwargs,
            )
    logger.info("external pack import -> %s", imported)
    if len(imported) > 1:
        back = _safe_v2_redirect(form.get("return_to"), "/v2/packs")
        sep = "&" if "?" in back else "?"
        msg = f"Imported {len(imported)} external packs"
        return RedirectResponse(
            url=f"{back}{sep}batch_summary={msg}",
            status_code=303,
        )
    result = imported[0]
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
    try:
        from src.services.etsy.service import get_etsy_service
        etsy = get_etsy_service().get_for_pack(pack_id)
    except Exception:  # noqa: BLE001 — Etsy 状态是附加信息, 不该拖垮页面
        etsy = None
    return templates.TemplateResponse(
        "v2_pack_detail.html",
        {
            "request": request,
            "page_title": f"卡包 {pack['display_name']}",
            "pack": pack,
            "etsy": etsy,
            "return_to": f"/v2/packs/{pack_id}",
        },
    )


@router.post("/packs/{pack_id:int}/sync-etsy")
def v2_pack_sync_etsy(pack_id: int):
    """一键同步贴纸包到 Etsy(建草稿 listing + 传主副图 + 可选视频)。

    同步阻塞(生成文案 → 调中间层建 listing → 传图/视频, 约 20-40 秒),
    前端 fetch 后显示结果。返回 dict 自动转 JSON。
    """
    from src.services.etsy.service import get_etsy_service

    return get_etsy_service().sync_pack(pack_id)


@router.post("/packs/{pack_id:int}/sync-shopify")
def v2_pack_sync_shopify(pack_id: int):
    """一键同步贴纸包到 Shopify(建草稿商品)。

    复用现成的 ShopifyProductSync.sync_one(经中间层调 Shopify Admin API):
    文案自动生成/缓存、图片优先 Shopify 图兜底 master 图。唯一前置是 default_price
    (原价), 缺则从 TikTok 模板价补一个(两平台共享此原价, 各自算折扣)。
    """
    from src.services.shopify.sync import get_shopify_sync
    from src.services.tkshop.service import (
        _price_from_default_template,
        get_tkshop_service,
    )

    tk = get_tkshop_service()
    sh = get_shopify_sync()
    lp_id = tk.get_or_create_local_product(pack_id)
    master = tk.get_local_product(lp_id) or {}
    if master.get("default_price") is None:
        try:
            sale, _disc = _price_from_default_template(master.get("default_template_json") or "{}")
            base = float(sale) if sale else 13.98
        except Exception:  # noqa: BLE001
            base = 13.98
        sh.batch_set_local_default_price([lp_id], base)
    return sh.sync_one(lp_id)


@router.post("/packs/{pack_id:int}/sync-all")
def v2_pack_sync_all(pack_id: int, tiktok_shop: str = Form("")):
    """一键上架三平台 —— 全部只建**草稿**(不上架/不提交审核), best-effort:
    某个平台失败不影响其余。Etsy 草稿 listing + Shopify 草稿商品 + TikTok 本地草稿
    (人工到 TKShop 产品页 push 提交审核)。返回 {etsy, shopify, tiktok} 三个结果。
    """
    from src.services.tkshop.service import (
        _price_from_default_template,
        get_tkshop_service,
    )

    result: dict = {}

    # 1) Etsy 草稿 listing
    try:
        from src.services.etsy.service import get_etsy_service
        result["etsy"] = get_etsy_service().sync_pack(pack_id)
    except Exception as e:  # noqa: BLE001
        result["etsy"] = {"ok": False, "error": str(e)[:200]}

    # 2) Shopify 草稿商品
    try:
        from src.services.shopify.sync import get_shopify_sync
        tk = get_tkshop_service()
        sh = get_shopify_sync()
        lp_id = tk.get_or_create_local_product(pack_id)
        master = tk.get_local_product(lp_id) or {}
        if master.get("default_price") is None:
            try:
                sale, _d = _price_from_default_template(master.get("default_template_json") or "{}")
                base = float(sale) if sale else 13.98
            except Exception:  # noqa: BLE001
                base = 13.98
            sh.batch_set_local_default_price([lp_id], base)
        result["shopify"] = sh.sync_one(lp_id)
    except Exception as e:  # noqa: BLE001
        result["shopify"] = {"ok": False, "error": str(e)[:200]}

    # 3) TikTok 本地草稿 (不 publish — 人工到 TKShop 产品页提交审核)
    try:
        tk = get_tkshop_service()
        listing_id = tk.create_product_from_pack(pack_id, shop=(tiktok_shop or None))
        result["tiktok"] = {"ok": True, "listing_id": listing_id,
                            "shop": tiktok_shop or "default",
                            "note": "本地草稿已建，到 TKShop 产品页 push 上架"}
    except Exception as e:  # noqa: BLE001
        result["tiktok"] = {"ok": False, "error": str(e)[:200]}

    return result


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


@router.post("/packs/{pack_id:int}/ai-enrich-and-split")
async def v2_pack_ai_enrich_and_split(request: Request, pack_id: int):
    form = await request.form()
    svc = get_pack_service()
    pack = svc.get_pack(pack_id)
    if not pack:
        raise HTTPException(status_code=404, detail="pack not found")
    split = _form_bool(form, "split", default=False)
    group_previews = _form_bool(form, "group_previews", default=True)
    max_raw = (form.get("max_stickers_total") or form.get("max_stickers_per_preview") or "").strip()
    kwargs: dict[str, Any] = {"split": split, "group_previews": group_previews}
    if max_raw:
        try:
            kwargs["max_stickers_per_preview"] = int(max_raw)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="max_stickers_total must be a number",
            )
    target_raw = (form.get("target_preview_count") or "").strip()
    if target_raw:
        try:
            kwargs["target_preview_count"] = max(0, int(target_raw))
        except ValueError:
            raise HTTPException(status_code=400, detail="target_preview_count must be a number")
    per_raw = (form.get("stickers_per_preview") or "").strip()
    if per_raw:
        try:
            kwargs["stickers_per_preview"] = max(0, int(per_raw))
        except ValueError:
            raise HTTPException(status_code=400, detail="stickers_per_preview must be a number")
    run_async(
        f"external_pack_ai:{pack_id}",
        svc.enrich_external_pack_with_ai,
        pack_id,
        label=f"AI 补全外部卡包 #{pack_id}",
        **kwargs,
    )
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/packs/{pack_id}"),
        status_code=303,
    )


@router.get("/packs/{pack_id:int}/delete")
def v2_pack_delete_get(pack_id: int):
    return RedirectResponse(url=f"/v2/packs/{pack_id}", status_code=303)


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
        v["video_create_task_running"] = is_running(f"video_create:{v['id']}")
        v["narration_task_running"] = is_running(f"narration:{v['id']}")
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


def _cleanup_upload_tmp(tmp_path: str) -> None:
    """Remove the temp file (and its dedicated dir) we staged the upload into."""
    try:
        p = Path(tmp_path)
        if p.exists():
            p.unlink()
        # We mkdtemp() a dedicated dir per upload, so it should now be empty.
        try:
            p.parent.rmdir()
        except OSError:
            pass
    except Exception:
        logger.warning("upload tmp cleanup failed for %s", tmp_path)


def _enqueue_post_create_tasks(
    video_id: int,
    *,
    file_saved: bool,
    scheduled_at: int,
    dispatch_now: bool,
    generate_caption: bool,
) -> None:
    """Kick off slow follow-up work after the row (and optional file) exist.

    Caption + narration always run as their own background tasks so the
    operator isn't blocked on the create redirect. Immediate publish is the
    only path that generates caption inline (dispatch needs the text first).
    """
    svc = get_tk_video_service()

    if scheduled_at and not dispatch_now:
        try:
            svc.schedule_video(video_id, scheduled_at)
        except Exception:
            logger.exception("video #%d: background schedule failed", video_id)

    want_caption = generate_caption or file_saved

    if dispatch_now:
        if want_caption:
            try:
                svc.generate_caption(video_id)
            except Exception:
                logger.exception("video #%d: background caption gen failed", video_id)
        if file_saved:
            from src.services.video_narration import get_video_narration_service
            run_async(
                f"narration:{video_id}",
                get_video_narration_service().generate, video_id,
                label=f"配音独白 video #{video_id}",
            )
        v = svc.get_video(video_id)
        if v and v.get("local_video_path") and (v.get("blotato_account_id") or "").strip():
            try:
                svc.dispatch_video(video_id)
            except Exception:
                logger.exception("video #%d: background dispatch failed", video_id)
        else:
            logger.warning(
                "video #%d: dispatch skipped — missing video file or Blotato account",
                video_id,
            )
        return

    if file_saved:
        _kickoff_post_upload(video_id, include_caption=want_caption)
    elif generate_caption:
        run_async(
            f"caption_gen:{video_id}",
            svc.generate_caption, video_id,
            label=f"AI 文案 video #{video_id}",
        )


def _process_new_video(
    video_id: int,
    *,
    tmp_path: str,
    original_filename: str,
    scheduled_at: int,
    dispatch_now: bool,
    generate_caption: bool,
) -> None:
    """Stage-1 background task: move upload into pack tree, then enqueue AI."""
    svc = get_tk_video_service()
    file_saved = False
    if tmp_path:
        try:
            svc.save_video_file(
                video_id, Path(tmp_path),
                original_filename=original_filename or "local.mp4",
            )
            file_saved = True
        except Exception:
            logger.exception("video #%d: background save_video_file failed", video_id)
        finally:
            _cleanup_upload_tmp(tmp_path)

    _enqueue_post_create_tasks(
        video_id,
        file_saved=file_saved,
        scheduled_at=scheduled_at,
        dispatch_now=dispatch_now,
        generate_caption=generate_caption,
    )


@router.post("/videos")
async def v2_video_create(request: Request):
    """Create the video row + persist the upload inline, then run all the slow
    work (file copy, AI caption, narration, dispatch) in one background task so
    the request returns immediately."""
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

    # Read the multipart upload into a stable temp file NOW — the stream can't
    # be read after we respond. Kept until the background task moves it into
    # the pack tree (which then cleans it up).
    has_file = upload is not None and bool(getattr(upload, "filename", ""))
    tmp_path = ""
    if has_file:
        import tempfile
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"vidup_{video_id}_"))
        dest = tmp_dir / upload.filename
        with open(dest, "wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        tmp_path = str(dest)

    # Cheap preconditions we can check without the saved file, so obvious
    # mistakes surface instantly instead of failing silently in the worker.
    dispatch_error = ""
    if dispatch_now:
        if not has_file:
            dispatch_error = "尚未上传视频文件，无法立即发布"
        elif not blotato_account_id:
            dispatch_error = "未指定 Blotato 账号，无法立即发布"
    do_dispatch = dispatch_now and not dispatch_error

    run_async(
        f"video_create:{video_id}",
        _process_new_video,
        video_id,
        tmp_path=tmp_path,
        original_filename=getattr(upload, "filename", "") if has_file else "",
        scheduled_at=scheduled_at,
        dispatch_now=do_dispatch,
        generate_caption=generate_caption,
        label=f"创建视频 #{video_id}（文件 / AI 文案 / 配音）",
    )

    target = _safe_v2_redirect(form.get("return_to"), f"/v2/videos/{video_id}")
    return RedirectResponse(
        url=_append_query(
            target,
            {
                "video_created": 1,
                "processing": 1,
                "caption_pending": 1 if (generate_caption or has_file) else None,
                "dispatch_pending": 1 if do_dispatch else None,
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
    v["video_create_task_running"] = is_running(f"video_create:{video_id}")
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
            "video_create_task_running": v["video_create_task_running"],
            "video_processing": bool(
                v["video_create_task_running"]
                or v["caption_task_running"]
                or narration_task_running
            ),
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


@router.post("/videos/{video_id:int}/delete")
async def v2_video_delete(request: Request, video_id: int):
    """Hard-delete a video row (+ its scripts, narrations, metrics, files).

    Does not recall anything already published or queued/scheduled on
    TikTok/Blotato — that must be cancelled upstream. Always lands back on
    the video list (never the now-deleted row's detail page/dialog).
    """
    form = await request.form()
    svc = get_tk_video_service()
    if not svc.delete_video(video_id):
        raise HTTPException(status_code=404, detail="video not found")
    # Force the filtered list, dropping any open_video=<id> that would try
    # to reopen the deleted dialog.
    return_to = (form.get("return_to") or "").strip()
    if "/v2/videos/" in return_to or f"open_video={video_id}" in return_to:
        return_to = ""
    target = _safe_v2_redirect(return_to, "/v2/videos")
    return RedirectResponse(
        url=_append_query(target, {"video_deleted": video_id}),
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
    pack_id: str = "",
    status: str = "all",
    shop: str = "",
    tab: str = "",
    limit: int = 50,
    synced: int | None = None,
    checked: int | None = None,
    batch_summary: str = "",
):
    # Local-products listing was split out to /v2/local-products/. Redirect
    # legacy ?tab=local URLs (and any bare landing without a shop choice)
    # so old bookmarks keep working.
    if tab == "local":
        return RedirectResponse(url="/v2/local-products/", status_code=307)
    # ``pack_id`` arrives as a string because the filter form posts an empty
    # value when blank — coercing to ``int | None`` directly in the signature
    # makes FastAPI return 422 on every search submission. Parse defensively.
    pack_id_int: Optional[int] = None
    if pack_id and pack_id.strip().isdigit():
        pack_id_int = int(pack_id.strip())
    # Tabs are per-shop now. Back-compat: a bare ?shop=X URL still maps to
    # tab=X; otherwise default to the first configured shop.
    if not tab:
        if shop and shop in TKSHOP_SHOPS:
            tab = shop
        elif TKSHOP_SHOPS:
            tab = TKSHOP_SHOPS[0]
        else:
            tab = ""
    is_local_tab = False  # Local moved to /v2/local-products/
    shop_filter: Optional[str] = tab or None
    svc = get_tkshop_service()
    # Local tab now reads from the master catalog (local_products); each
    # row is one pack with shop-listing badges built from the joined
    # tkshop_products rows. The shop tabs keep reading tkshop_products
    # because they show per-shop platform IDs/SKUs.
    if is_local_tab:
        products, total = svc.list_local_products(
            pack_id=pack_id_int,
            q=q.strip() or None,
            limit=limit,
        )
    else:
        products, total = svc.list_products(
            pack_id=pack_id_int,
            publish_status=status if status != "all" else None,
            q=q.strip() or None,
            shop=shop_filter,
            on_platform_only=True,
            limit=limit,
        )
    # Tab counts — drives the sidebar sub-link labels (本地产品 (12) /
    # 🏪 inkelligentsticker (8) / 🏪 inkelligentstudio (3)).
    tab_counts: dict[str, int] = {}
    _, tab_counts["local"] = svc.list_local_products(
        pack_id=pack_id_int,
        q=q.strip() or None,
        limit=1,
    )
    for s in TKSHOP_SHOPS:
        _, c = svc.list_products(
            pack_id=pack_id_int,
            publish_status=status if status != "all" else None,
            q=q.strip() or None,
            shop=s,
            on_platform_only=True,
            limit=1,
        )
        tab_counts[s] = c
    # Enrichment: when pack_cover is missing, fall back to a stored image.
    # Master rows: try local_product_images; listing rows: try
    # tkshop_product_images; fallback for both is the first ok preview.
    import sqlite3 as _sql
    fb_conn = _sql.connect("data/ops_workbench.db")
    fb_conn.row_factory = _sql.Row
    try:
        for p in products:
            p["created_human"] = _fmt_ts(p.get("created_at"))
            p["published_human"] = _fmt_ts(p.get("published_at"))
            cover = ""
            if is_local_tab:
                cover = _resolve_local_product_cover_url(
                    fb_conn,
                    local_product_id=p["id"],
                    pack_id=p["pack_id"],
                    pack_cover_path=p.get("pack_cover") or "",
                    updated_at=int(p.get("updated_at") or 0),
                )
            elif p.get("pack_id"):
                cover = _resolve_listing_cover_url(
                    fb_conn,
                    product_id=p["id"],
                    pack_id=p["pack_id"],
                    pack_cover_path=p.get("pack_cover") or "",
                    updated_at=int(p.get("updated_at") or 0),
                )
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
            "pack_id_filter": pack_id_int,
            "status_filter": status,
            "shop_filter": shop_filter or "",
            "tab": tab,
            "is_local_tab": is_local_tab,
            "tab_counts": tab_counts,
            "limit": limit,
            "is_platform_view": False,
            "synced": synced,
            "checked": checked,
            "batch_summary": batch_summary,
        },
    )


# ----------------------------------------------------------------------
# Local products (master catalog) — split out from /v2/products listings
# ----------------------------------------------------------------------

@router.get("/local-products/", response_class=HTMLResponse)
@router.get("/local-products", response_class=HTMLResponse)
def v2_local_products_list(
    request: Request,
    q: str = "",
    pack_id: str = "",
    limit: int = 50,
    batch_summary: str = "",
):
    """Master catalog list. Took over the 本地产品 tab that used to live
    inside /v2/products. Each row is one pack's master record with badges
    showing which TKShop stores it's already listed on."""
    pack_id_int: Optional[int] = None
    if pack_id and pack_id.strip().isdigit():
        pack_id_int = int(pack_id.strip())
    svc = get_tkshop_service()
    products, total = svc.list_local_products(
        pack_id=pack_id_int,
        q=q.strip() or None,
        limit=limit,
    )
    # Cover fallback: pack_cover → main local_product_image → any 'ok' preview.
    import sqlite3 as _sql
    fb_conn = _sql.connect("data/ops_workbench.db")
    fb_conn.row_factory = _sql.Row
    try:
        for p in products:
            p["created_human"] = _fmt_ts(p.get("created_at"))
            p["updated_human"] = _fmt_ts(p.get("updated_at"))
            p["pack_cover_url"] = _resolve_local_product_cover_url(
                fb_conn,
                local_product_id=p["id"],
                pack_id=p["pack_id"],
                pack_cover_path=p.get("pack_cover") or "",
                updated_at=int(p.get("updated_at") or 0),
            )
    finally:
        fb_conn.close()
    return templates.TemplateResponse(
        "v2_local_products.html",
        {
            "request": request,
            "page_title": "本地产品库",
            "products": products,
            "total": total,
            "q": q,
            "pack_id_filter": pack_id_int,
            "limit": limit,
            "batch_summary": batch_summary,
        },
    )


def _render_platform_products(request: Request, *, platform: str, q: str, limit: int):
    """Etsy / Shopify「已上架产品」列表 —— 读本地映射表(etsy_products /
    shopify_products), 复用通用模板 v2_platform_products.html。"""
    # 功能开关: 无该平台上架权限的角色(如 cs)即使直接输 URL 也挡在门外, 回工作台首页。
    if not _can_see(request, platform):
        return RedirectResponse("/v2", status_code=302)
    svc = get_tkshop_service()
    products, total = svc.list_platform_products(
        platform=platform, q=q.strip() or None, limit=limit,
    )
    meta = {
        "etsy":    {"label": "Etsy",    "emoji": "🟠", "id_label": "Listing ID"},
        "shopify": {"label": "Shopify", "emoji": "🛍️", "id_label": "Product ID"},
    }[platform]
    import sqlite3 as _sql
    fb_conn = _sql.connect("data/ops_workbench.db")
    fb_conn.row_factory = _sql.Row
    try:
        for p in products:
            p["created_human"] = _fmt_ts(p.get("created_at"))
            p["synced_human"] = _fmt_ts(p.get("synced_at")) if p.get("synced_at") else ""
            p["pack_cover_url"] = _resolve_local_product_cover_url(
                fb_conn,
                local_product_id=p["local_product_id"],
                pack_id=p["pack_id"],
                pack_cover_path=p.get("pack_cover") or "",
                updated_at=int(p.get("synced_at") or p.get("created_at") or 0),
            )
            pid = str(p.get("platform_id") or "").strip()
            # Etsy listing 有公开 URL(纯 id 可拼); Shopify 前台需 store 域名, 暂不拼。
            p["listing_url"] = f"https://www.etsy.com/listing/{pid}" if (platform == "etsy" and pid) else ""
    finally:
        fb_conn.close()
    return templates.TemplateResponse(
        "v2_platform_products.html",
        {
            "request": request,
            "page_title": f"{meta['label']} 已上架产品",
            "platform": platform,
            "platform_label": meta["label"],
            "platform_emoji": meta["emoji"],
            "id_label": meta["id_label"],
            "products": products,
            "total": total,
            "q": q,
            "limit": limit,
        },
    )


@router.get("/etsy/products", response_class=HTMLResponse)
def v2_etsy_products(request: Request, q: str = "", limit: int = 200):
    """Etsy 已上架产品列表(读本地 etsy_products 映射表)。"""
    return _render_platform_products(request, platform="etsy", q=q, limit=limit)


@router.get("/shopify/products", response_class=HTMLResponse)
def v2_shopify_products(request: Request, q: str = "", limit: int = 200):
    """Shopify 已上架产品列表(读本地 shopify_products 映射表)。"""
    return _render_platform_products(request, platform="shopify", q=q, limit=limit)


def _render_platform_local_products(request: Request, *, platform: str, listed: str, q: str, limit: int):
    """Etsy / Shopify「本地产品」页(平台视角) —— 列出所有 master(可上架的源头) +
    该平台上架状态, 未上架的给发起上架入口。复用 v2_local_products_platform.html。"""
    if not _can_see(request, platform):
        return RedirectResponse("/v2", status_code=302)
    svc = get_tkshop_service()
    listed = listed if listed in ("all", "listed", "unlisted") else "all"
    products, total = svc.list_local_products_for_platform(
        platform=platform, listed=listed, q=q.strip() or None, limit=limit,
    )
    meta = {
        "etsy":    {"label": "Etsy",    "emoji": "🟠"},
        "shopify": {"label": "Shopify", "emoji": "🛍️"},
    }[platform]
    import sqlite3 as _sql
    fb_conn = _sql.connect("data/ops_workbench.db")
    fb_conn.row_factory = _sql.Row
    try:
        for p in products:
            p["updated_human"] = _fmt_ts(p.get("updated_at"))
            p["pack_cover_url"] = _resolve_local_product_cover_url(
                fb_conn,
                local_product_id=p["local_product_id"],
                pack_id=p["pack_id"],
                pack_cover_path=p.get("pack_cover") or "",
                updated_at=int(p.get("updated_at") or 0),
            )
            pid = str(p.get("platform_id") or "").strip()
            p["listing_url"] = f"https://www.etsy.com/listing/{pid}" if (platform == "etsy" and pid) else ""
            p["is_listed"] = bool(p.get("mapping_id"))
    finally:
        fb_conn.close()
    listed_count = sum(1 for p in products if p["is_listed"])
    return templates.TemplateResponse(
        "v2_local_products_platform.html",
        {
            "request": request,
            "page_title": f"{meta['label']} 本地产品",
            "platform": platform,
            "platform_label": meta["label"],
            "platform_emoji": meta["emoji"],
            "products": products,
            "total": total,
            "listed_count": listed_count,
            "listed": listed,
            "q": q,
            "limit": limit,
        },
    )


@router.get("/etsy/local-products", response_class=HTMLResponse)
def v2_etsy_local_products(request: Request, q: str = "", listed: str = "all", limit: int = 200):
    """Etsy 本地产品(平台视角: 所有 master + Etsy 上架状态)。"""
    return _render_platform_local_products(request, platform="etsy", listed=listed, q=q, limit=limit)


@router.get("/shopify/local-products", response_class=HTMLResponse)
def v2_shopify_local_products(request: Request, q: str = "", listed: str = "all", limit: int = 200):
    """Shopify 本地产品(平台视角: 所有 master + Shopify 上架状态)。"""
    return _render_platform_local_products(request, platform="shopify", listed=listed, q=q, limit=limit)


@router.get("/local-products/{local_product_id:int}", response_class=HTMLResponse)
def v2_local_product_detail(request: Request, local_product_id: int):
    """Master editor: title/description/keywords/SKU/category + image set +
    each shop's listing status with per-listing 'push to TikTok' actions.
    """
    svc = get_tkshop_service()
    lp = svc.get_local_product(local_product_id)
    if not lp:
        return templates.TemplateResponse(
            "v2_not_found.html",
            {
                "request": request,
                "page_title": "本地产品不存在",
                "what": f"本地产品 #{local_product_id}",
                "back_url": "/v2/local-products/",
                "back_label": "← 返回本地产品库",
                "hint": "可能已被删除，或链接里的 id 写错了。",
            },
            status_code=404,
        )
    lp["created_human"] = _fmt_ts(lp.get("created_at"))
    lp["updated_human"] = _fmt_ts(lp.get("updated_at"))
    import sqlite3 as _sql
    _fb = _sql.connect("data/ops_workbench.db")
    _fb.row_factory = _sql.Row
    try:
        lp["pack_cover_url"] = _resolve_local_product_cover_url(
            _fb,
            local_product_id=local_product_id,
            pack_id=lp.get("pack_id") or 0,
            pack_cover_path=lp.get("pack_cover") or "",
            updated_at=int(lp.get("updated_at") or 0),
        )
    finally:
        _fb.close()
    for img in lp.get("images", []):
        img["url"] = _versioned_local_product_image_url(img)
        img["created_human"] = _fmt_ts(img.get("created_at"))
    for ls in lp.get("listings", []):
        ls["created_human"] = _fmt_ts(ls.get("created_at"))
        ls["published_human"] = _fmt_ts(ls.get("published_at"))
        ls["last_pushed_human"] = _fmt_ts(ls.get("last_pushed_at"))
        ls["shop_label"] = get_shop_label(ls.get("shop") or "")
    # Which shops the operator can still publish to (no LIVE listing yet).
    listed_shops = {ls.get("shop") for ls in lp.get("listings", [])
                    if ls.get("tiktok_product_id")}
    missing_shops = [s for s in TKSHOP_SHOPS if s not in listed_shops]
    # Etsy / Shopify 上架状态(详情页「多平台上架」卡片)。按角色门控: 无权限的平台不塞数据。
    pl = svc.get_platform_listings(local_product_id)
    etsy_row = pl["etsy"] if _can_see(request, "etsy") else None
    shopify_row = pl["shopify"] if _can_see(request, "shopify") else None
    if etsy_row:
        _eid = str(etsy_row.get("etsy_listing_id") or "").strip()
        etsy_row["listing_url"] = f"https://www.etsy.com/listing/{_eid}" if _eid else ""
        etsy_row["synced_human"] = _fmt_ts(etsy_row.get("synced_at")) if etsy_row.get("synced_at") else ""
    if shopify_row:
        shopify_row["synced_human"] = _fmt_ts(shopify_row.get("synced_at")) if shopify_row.get("synced_at") else ""
    return templates.TemplateResponse(
        "v2_local_product_detail.html",
        {
            "request": request,
            "page_title": f"本地产品 #{local_product_id}",
            "lp": lp,
            "missing_shops": missing_shops,
            "etsy_listing": etsy_row,
            "shopify_listing": shopify_row,
            "can_etsy": _can_see(request, "etsy"),
            "can_shopify": _can_see(request, "shopify"),
        },
    )


@router.post("/local-products/{local_product_id:int}/edit-detail")
async def v2_local_product_edit_detail(request: Request, local_product_id: int):
    """Save master text edits. Does NOT auto-sync to listings — the operator
    uses each listing's '推送本地修改到 TikTok' button to push."""
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
    if not svc.update_local_product_detail(
        local_product_id,
        title=title, description_html=desc,
        selling_points=sps, keywords=kws,
        category_id=cat or None,
        seller_sku=seller_sku or None,
    ):
        raise HTTPException(status_code=404, detail="local product not found")
    return RedirectResponse(
        url=f"/v2/local-products/{local_product_id}", status_code=303,
    )


@router.post("/local-product-images/{image_id:int}/delete-json")
async def v2_local_product_image_delete_json(image_id: int):
    """Delete a single master image row. Mirrors the per-listing
    /v2/products/images/{id}/delete-json so the master gallery can offer
    the same trash icon. On-disk file is left in place — it may still be
    referenced by a listing snapshot. Returns JSON.
    """
    svc = get_tkshop_service()
    ok = svc.delete_local_product_image(image_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "image not found"}, status_code=404)
    return JSONResponse({"ok": True})


@router.post("/local-products/{local_product_id:int}/clear-images")
async def v2_local_product_clear_images(request: Request, local_product_id: int):
    """Wipe every image row from the master gallery (both AI- and manual-
    tagged). On-disk files stay; only the DB rows go. Useful when the
    operator wants to start fresh before re-running AI gen.
    """
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    try:
        cur = conn.execute(
            "DELETE FROM local_product_images WHERE local_product_id = ?",
            (local_product_id,),
        )
        conn.commit()
        deleted = cur.rowcount
    finally:
        conn.close()
    logger.info("cleared %d master images for local_product #%d", deleted, local_product_id)
    return RedirectResponse(
        url=f"/v2/local-products/{local_product_id}", status_code=303,
    )


@router.post("/local-products/{local_product_id:int}/auto_design_images_sync")
async def v2_local_product_auto_design_sync(request: Request, local_product_id: int):
    """Kick off master-level AI image generation. Returns immediately;
    operator can poll ``auto_design_status`` or just refresh the page.

    Internally resolves a draft tkshop_products listing (creates one if
    needed) and runs the standard pipeline; AI images are mirrored back
    to ``local_product_images`` so the master gallery picks them up.
    """
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else dict(await request.form())
    try:
        count = max(1, min(int(payload.get("count", 5)), 8))
    except (TypeError, ValueError):
        count = 5
    language = (str(payload.get("language") or "en")).lower().strip() or "en"
    size = (str(payload.get("size") or "1024x1024")).strip() or "1024x1024"
    shop = (str(payload.get("shop") or "")).strip() or None
    mode = (str(payload.get("mode") or "all")).strip().lower()
    if mode not in {"all", "main", "secondary"}:
        mode = "all"
    conn = _open_db()
    try:
        existing_images = conn.execute(
            "SELECT COUNT(*) FROM local_product_images WHERE local_product_id = ?",
            (local_product_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    replace_existing_ai, secondary_count = _auto_design_mode_params(
        mode=mode, count=count, existing_images=int(existing_images or 0),
    )
    task_id = f"tkshop_auto_design_local:{local_product_id}"
    try:
        started = run_async(
            task_id,
            _run_local_auto_design_bg, local_product_id,
            shop=shop,
            secondary_count=secondary_count,
            language=language,
            replace_existing_ai=replace_existing_ai,
            size=size,
            mode=mode,
            label=f"AI 主图设计 (master #{local_product_id}, mode={mode}, {count} 张, {language}, {size})",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not started:
        return JSONResponse({
            "ok": True, "task_id": task_id, "started": False,
            "message": "任务已在后台运行中（同一本地产品不会并行启动）",
        })
    return JSONResponse({
        "ok": True, "task_id": task_id, "started": True,
        "message": "已在后台启动 — 关弹窗或离开页面都不会中断。完成后刷新详情页即可看到。",
    })


@router.get("/local-products/{local_product_id:int}/auto_design_status")
def v2_local_product_auto_design_status(local_product_id: int):
    """Poll endpoint for the modal: returns {running, images} so the modal
    can show progress and the final result grid without a full reload.
    """
    task_id = f"tkshop_auto_design_local:{local_product_id}"
    svc = get_tkshop_service()
    images = svc.list_local_product_images(local_product_id)
    images_with_url = [
        {**i, "url": _versioned_local_product_image_url(i)}
        for i in images if i.get("source") == "ai"
    ]
    return JSONResponse({
        "ok": True,
        "running": is_running(task_id),
        "images": images_with_url,
    })


@router.post("/local-products/{local_product_id:int}/publish-to-shop")
async def v2_local_product_publish_to_shop(request: Request, local_product_id: int):
    """One-click 'publish this master to shop X' from the detail page.

    Wraps ``batch_publish_local_to_shop`` with a single id, fire-and-forget
    so the operator sees a redirect right away. Use this when only one
    shop is left to publish to — the table's batch button handles multi.
    """
    form = await request.form()
    target_shop = (form.get("target_shop") or "").strip()
    if not target_shop:
        raise HTTPException(status_code=400, detail="target_shop is required")
    if target_shop not in TKSHOP_SHOPS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown shop {target_shop!r}; configured: {TKSHOP_SHOPS}",
        )
    svc = get_tkshop_service()
    task_id = f"tkshop_publish_local:{local_product_id}:{target_shop}"
    run_async(
        task_id,
        svc.batch_publish_local_to_shop,
        [local_product_id], target_shop,
        label=f"上架本地 #{local_product_id} → {target_shop}",
    )
    return RedirectResponse(
        url=f"/v2/local-products/{local_product_id}", status_code=303,
    )


def _versioned_local_product_image_url(img: dict) -> str:
    """Build a /v2-outputs URL for a local_product_images row with a busting
    suffix so the browser doesn't show a stale cached image after a regen."""
    p = img.get("local_path") or ""
    key = _cover_cache_key(p, int(img.get("created_at") or img.get("id") or 0))
    return _versioned_url_from_path(p, cache_key=key)


@router.post("/products/batch-publish-from-local")
async def v2_products_batch_publish_from_local(request: Request):
    """Multi-select batch-publish driven by local_product ids.

    The 本地产品 tab's rows are masters now, so the checkbox values are
    local_product ids. This endpoint dispatches a master-aware publish
    pipeline (creates listings as needed, snapshots master images, then
    publishes).
    """
    form = await request.form()
    target_shop = (form.get("target_shop") or "").strip()
    if not target_shop:
        raise HTTPException(status_code=400, detail="target_shop is required")
    if target_shop not in TKSHOP_SHOPS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown shop {target_shop!r}; configured: {TKSHOP_SHOPS}",
        )
    raw_ids = form.getlist("local_product_ids") if hasattr(form, "getlist") else []
    ids: list[int] = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not ids:
        raise HTTPException(status_code=400, detail="no local_product_ids selected")
    auto_activate = (form.get("auto_activate") or "1").strip() not in ("0", "off", "false", "no")
    auto_fix = (form.get("auto_fix") or "1").strip() not in ("0", "off", "false", "no")
    svc = get_tkshop_service()
    task_id = f"tkshop_batch_publish_local:{int(time.time())}"
    run_async(
        task_id,
        svc.batch_publish_local_to_shop,
        ids, target_shop,
        auto_activate=auto_activate, auto_fix=auto_fix,
        label=f"批量上架本地 → {target_shop} ({len(ids)} 选)",
    )
    logger.info(
        "batch-publish-from-local dispatched: %d masters → %s (task=%s)",
        len(ids), target_shop, task_id,
    )
    back = _safe_v2_redirect((form.get("back") or "").strip(), "/v2/local-products/")
    sep = "&" if "?" in back else "?"
    msg = f"已派发 {len(ids)} 个本地产品上架到 {target_shop}"
    return RedirectResponse(
        url=f"{back}{sep}batch_summary={msg}",
        status_code=303,
    )


def _parse_local_product_ids(form) -> list[int]:
    """Extract repeated ``local_product_ids`` form values as ints."""
    raw = form.getlist("local_product_ids") if hasattr(form, "getlist") else []
    out: list[int] = []
    for v in raw:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


@router.post("/local-products/batch-set-quantity")
async def v2_local_products_batch_set_quantity(request: Request):
    """One-click batch edit of the local default stock (本地默认库存).

    Local-only: writes ``local_products.default_quantity`` for the selected
    masters. Does NOT touch live TikTok inventory — the value is applied as the
    listing quantity on the next push. Form: ``local_product_ids`` (repeated),
    ``quantity`` (int >= 0), optional ``back``.
    """
    form = await request.form()
    ids = _parse_local_product_ids(form)
    if not ids:
        raise HTTPException(status_code=400, detail="no local_product_ids selected")
    raw_qty = (form.get("quantity") or "").strip()
    try:
        quantity = int(raw_qty)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid quantity {raw_qty!r}")
    if quantity < 0:
        raise HTTPException(status_code=400, detail="quantity must be >= 0")
    svc = get_tkshop_service()
    n = svc.batch_set_local_default_quantity(ids, quantity)
    logger.info("batch-set-quantity: qty=%d applied to %d masters", quantity, n)
    back = _safe_v2_redirect((form.get("back") or "").strip(), "/v2/local-products/")
    sep = "&" if "?" in back else "?"
    msg = f"已把 {n} 个本地产品的默认库存改为 {quantity}（下次推送生效）"
    return RedirectResponse(url=f"{back}{sep}batch_summary={msg}", status_code=303)


@router.post("/products/batch-shop-status")
async def v2_products_batch_shop_status(request: Request):
    """One-click 上架 / 下架 for one shop, driven by local_product ids.

    Toggles the live status of selected masters' listings on ``shop`` via the
    middle layer (activate/deactivate). Real platform write — fire-and-forget
    so the operator gets a redirect immediately. Form: ``local_product_ids``
    (repeated), ``shop``, ``action`` ('activate' | 'deactivate'), optional
    ``back``.
    """
    form = await request.form()
    shop = (form.get("shop") or "").strip()
    if shop not in TKSHOP_SHOPS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown shop {shop!r}; configured: {TKSHOP_SHOPS}",
        )
    action = (form.get("action") or "").strip().lower()
    if action not in ("activate", "deactivate"):
        raise HTTPException(status_code=400, detail="action must be activate/deactivate")
    ids = _parse_local_product_ids(form)
    if not ids:
        raise HTTPException(status_code=400, detail="no local_product_ids selected")
    svc = get_tkshop_service()
    verb = "上架" if action == "activate" else "下架"
    task_id = f"tkshop_batch_shop_status:{action}:{shop}:{int(time.time())}"
    if action == "activate":
        # Smart 上架: publishes first-timers (creates listing) and re-activates
        # deactivated ones — merges the old "批量发布" into 上架.
        run_async(
            task_id,
            svc.batch_shelf_on_shop,
            ids, shop,
            label=f"批量上架 {get_shop_label(shop)} ({len(ids)} 选)",
        )
        msg = f"已派发 {len(ids)} 个本地产品在「{get_shop_label(shop)}」上架（未上架的会自动发布创建）"
    else:
        run_async(
            task_id,
            svc.batch_set_shop_listing_status,
            ids, shop,
            activate=False,
            label=f"批量下架 {get_shop_label(shop)} ({len(ids)} 选)",
        )
        msg = f"已派发 {len(ids)} 个本地产品在「{get_shop_label(shop)}」下架（仅对已上架的生效）"
    logger.info(
        "batch-shop-status dispatched: %s %d masters → %s (task=%s)",
        action, len(ids), shop, task_id,
    )
    back = _safe_v2_redirect((form.get("back") or "").strip(), "/v2/local-products/")
    sep = "&" if "?" in back else "?"
    return RedirectResponse(url=f"{back}{sep}batch_summary={msg}", status_code=303)


# ──────────────────────────── Shopify 同步 ────────────────────────────

def _run_shopify_image_gen_bg(local_product_id: int) -> dict:
    """Background worker: generate the fixed Shopify image set."""
    from src.services.shopify.images import generate_shopify_images
    return generate_shopify_images(local_product_id, replace=True)


@router.post("/local-products/{local_product_id:int}/shopify-generate-images")
async def v2_local_product_shopify_generate_images(request: Request, local_product_id: int):
    """Generate the dedicated Shopify image set (fixed 5-image style, all AI
    image-to-image). Independent from TikTok's master gallery — stored in
    ``shopify_product_images``. Background; poll ``shopify-images-status``.
    """
    task_id = f"shopify_images:{local_product_id}"
    try:
        started = run_async(
            task_id,
            _run_shopify_image_gen_bg, local_product_id,
            label=f"Shopify 固定样式图 (master #{local_product_id}, 5 张)",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not started:
        return JSONResponse({"ok": True, "task_id": task_id, "started": False,
                             "message": "任务已在后台运行中（同一产品不会并行启动）"})
    return JSONResponse({"ok": True, "task_id": task_id, "started": True,
                         "message": "已在后台启动 — Shopify 固定样式 5 张图，完成后自动刷新"})


@router.get("/local-products/{local_product_id:int}/shopify-images-status")
def v2_local_product_shopify_images_status(local_product_id: int):
    """Poll endpoint for the Shopify image generation: {running, images}."""
    from src.services.shopify.images import list_shopify_images
    task_id = f"shopify_images:{local_product_id}"
    imgs = list_shopify_images(local_product_id)
    imgs_with_url = [
        {**i, "url": _versioned_local_product_image_url(i)} for i in imgs
    ]
    return JSONResponse({
        "ok": True,
        "running": is_running(task_id),
        "images": imgs_with_url,
    })


@router.get("/local-products/{local_product_id:int}/shopify-preview", response_class=HTMLResponse)
def v2_local_product_shopify_preview(request: Request, local_product_id: int):
    """Local style preview of how this master will look as a Shopify product.

    No network call — assembles the branded HTML + image gallery + fields via
    ShopifyProductSync.build_preview so the operator can review before pushing.
    """
    pv = get_shopify_sync().build_preview(local_product_id)
    # Attach browsable image URLs (the service returns local_paths only).
    for img in pv.get("images", []) or []:
        img["url"] = _versioned_local_product_image_url(img)
    return templates.TemplateResponse(
        "v2_shopify_preview.html",
        {
            "request": request,
            "page_title": f"Shopify 预览 · 本地 #{local_product_id}",
            "pv": pv,
            "back_url": f"/v2/local-products/{local_product_id}",
        },
    )


@router.post("/local-products/{local_product_id:int}/shopify-generate-images")
async def v2_local_product_shopify_generate_images(request: Request, local_product_id: int):
    """Generate the Shopify image set while PRESERVING the existing main image.

    The master gallery is shared with TikTok, so we must not clobber the TK
    main/hero. Behaviour:
      - master already has a main image -> mode='secondary', replace AI
        secondaries only (main untouched), regenerate the lifestyle set.
      - master has no main yet           -> mode='all' (generate main + set).
    Background task; poll ``auto_design_status`` (same task_id) for progress.
    """
    payload = (
        await request.json()
        if request.headers.get("content-type", "").startswith("application/json")
        else dict(await request.form())
    )
    try:
        secondary_count = max(1, min(int(payload.get("secondary_count", 4)), 7))
    except (TypeError, ValueError):
        secondary_count = 4
    conn = _open_db()
    try:
        has_main = conn.execute(
            "SELECT 1 FROM local_product_images "
            "WHERE local_product_id = ? AND role = 'main' LIMIT 1",
            (local_product_id,),
        ).fetchone()
    finally:
        conn.close()
    if has_main:
        # Keep the main; refresh only the secondary (lifestyle) set.
        mode, replace_existing_ai, sec = "secondary", True, secondary_count
        note = "保留主图，只重做副图"
    else:
        # No main yet — generate the full set including a hero.
        mode, replace_existing_ai, sec = "all", True, secondary_count
        note = "无主图，生成全套（含主图）"
    task_id = f"tkshop_auto_design_local:{local_product_id}"
    try:
        started = run_async(
            task_id,
            _run_local_auto_design_bg, local_product_id,
            secondary_count=sec,
            language="en",
            replace_existing_ai=replace_existing_ai,
            size="1024x1024",
            mode=mode,
            label=f"Shopify 全套图 (master #{local_product_id}, {note}, +{sec} 副图)",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({
        "ok": True, "task_id": task_id, "started": bool(started),
        "message": ("已在后台启动 — " + note + "。完成后本页自动刷新。")
        if started else "任务已在后台运行中（同一产品不会并行启动）",
    })


@router.post("/local-products/{local_product_id:int}/shopify-regenerate")
async def v2_local_product_shopify_regenerate(request: Request, local_product_id: int):
    """Force a fresh AI generation of the Shopify copy and persist it, then
    return to the preview. Synchronous (one AI pass) so the operator sees the
    new content immediately on redirect."""
    form = await request.form()
    get_shopify_sync().regenerate_content(local_product_id)
    back = _safe_v2_redirect(
        (form.get("back") or "").strip(),
        f"/v2/local-products/{local_product_id}/shopify-preview",
    )
    return RedirectResponse(url=back, status_code=303)


@router.post("/local-products/{local_product_id:int}/sync-shopify")
async def v2_local_product_sync_shopify(request: Request, local_product_id: int):
    """One-click sync a single master to Shopify (draft). Real external write —
    fire-and-forget so the operator gets an immediate redirect."""
    form = await request.form()
    svc = get_shopify_sync()
    # Guard: need a price first (also enforced in service, but fail fast w/ msg).
    pv = svc.build_preview(local_product_id)
    back = _safe_v2_redirect(
        (form.get("back") or "").strip(),
        f"/v2/local-products/{local_product_id}/shopify-preview",
    )
    sep = "&" if "?" in back else "?"
    if pv.get("price") is None:
        msg = "未设置价格，无法同步 — 请先在本地产品库批量改价或设置该产品价格"
        return RedirectResponse(url=f"{back}{sep}batch_summary={msg}", status_code=303)
    task_id = f"shopify_sync:{local_product_id}:{int(time.time())}"
    run_async(
        task_id,
        svc.batch_sync,
        [local_product_id],
        label=f"同步本地 #{local_product_id} → Shopify",
    )
    msg = f"已派发：本地 #{local_product_id} → Shopify（草稿）"
    return RedirectResponse(url=f"{back}{sep}batch_summary={msg}", status_code=303)


# ---- 详情页「多平台上架」卡片的行内操作(重新同步 / 下架), 均返回 JSON 供 fetch ----

@router.post("/local-products/{local_product_id:int}/etsy-resync")
def v2_lp_etsy_resync(request: Request, local_product_id: int):
    """重新同步该 master 到 Etsy —— sync_pack 是 upsert, 刷新已有 listing 不建新草稿。"""
    if not _can_see(request, "etsy"):
        return JSONResponse({"ok": False, "error": "无权限"}, status_code=403)
    from src.services.etsy.service import get_etsy_service
    lp = get_tkshop_service().get_local_product(local_product_id)
    if not lp or not lp.get("pack_id"):
        return JSONResponse({"ok": False, "error": "本地产品或卡包不存在"}, status_code=404)
    return get_etsy_service().sync_pack(lp["pack_id"])


@router.post("/local-products/{local_product_id:int}/etsy-delist")
def v2_lp_etsy_delist(request: Request, local_product_id: int):
    """下架 Etsy listing —— state=inactive, 不删除(符合 no-delete 规则)。"""
    if not _can_see(request, "etsy"):
        return JSONResponse({"ok": False, "error": "无权限"}, status_code=403)
    from src.services.etsy.service import get_etsy_service
    return get_etsy_service().set_state(local_product_id, "inactive")


@router.post("/local-products/{local_product_id:int}/shopify-resync")
def v2_lp_shopify_resync(request: Request, local_product_id: int):
    """重新同步该 master 到 Shopify —— sync_one 会删旧重建(换 product_id/handle)。"""
    if not _can_see(request, "shopify"):
        return JSONResponse({"ok": False, "error": "无权限"}, status_code=403)
    from src.services.shopify.sync import get_shopify_sync
    from src.services.tkshop.service import _price_from_default_template
    tk = get_tkshop_service()
    sh = get_shopify_sync()
    master = tk.get_local_product(local_product_id) or {}
    if not master:
        return JSONResponse({"ok": False, "error": "本地产品不存在"}, status_code=404)
    if master.get("default_price") is None:
        try:
            sale, _d = _price_from_default_template(master.get("default_template_json") or "{}")
            base = float(sale) if sale else 13.98
        except Exception:  # noqa: BLE001
            base = 13.98
        sh.batch_set_local_default_price([local_product_id], base)
    return sh.sync_one(local_product_id)


@router.post("/local-products/{local_product_id:int}/shopify-delist")
def v2_lp_shopify_delist(request: Request, local_product_id: int):
    """下架 Shopify 商品 —— status=draft, 不删除(符合 no-delete 规则)。"""
    if not _can_see(request, "shopify"):
        return JSONResponse({"ok": False, "error": "无权限"}, status_code=403)
    from src.services.shopify.sync import get_shopify_sync
    return get_shopify_sync().set_status(local_product_id, "draft")


@router.post("/products/batch-sync-shopify")
async def v2_products_batch_sync_shopify(request: Request):
    """Multi-select batch sync masters to Shopify (draft). Skips ones with no
    price. Fire-and-forget."""
    form = await request.form()
    ids = _parse_local_product_ids(form)
    if not ids:
        raise HTTPException(status_code=400, detail="no local_product_ids selected")
    svc = get_shopify_sync()
    task_id = f"shopify_batch_sync:{int(time.time())}"
    run_async(
        task_id,
        svc.batch_sync,
        ids,
        label=f"批量同步 → Shopify ({len(ids)} 选)",
    )
    logger.info("batch-sync-shopify dispatched: %d masters (task=%s)", len(ids), task_id)
    back = _safe_v2_redirect((form.get("back") or "").strip(), "/v2/local-products/")
    sep = "&" if "?" in back else "?"
    msg = f"已派发 {len(ids)} 个本地产品同步到 Shopify（草稿，未设价的会跳过）"
    return RedirectResponse(url=f"{back}{sep}batch_summary={msg}", status_code=303)


@router.post("/local-products/batch-set-price")
async def v2_local_products_batch_set_price(request: Request):
    """One-click batch edit of the local default price (Shopify variant price)."""
    form = await request.form()
    ids = _parse_local_product_ids(form)
    if not ids:
        raise HTTPException(status_code=400, detail="no local_product_ids selected")
    raw_price = (form.get("price") or "").strip()
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"invalid price {raw_price!r}")
    if price < 0:
        raise HTTPException(status_code=400, detail="price must be >= 0")
    n = get_shopify_sync().batch_set_local_default_price(ids, price)
    logger.info("batch-set-price: price=%.2f applied to %d masters", price, n)
    back = _safe_v2_redirect((form.get("back") or "").strip(), "/v2/local-products/")
    sep = "&" if "?" in back else "?"
    msg = f"已把 {n} 个本地产品的价格设为 ${price:.2f}"
    return RedirectResponse(url=f"{back}{sep}batch_summary={msg}", status_code=303)


@router.post("/products/batch-publish-to-shop")
async def v2_products_batch_publish_to_shop(request: Request):
    """Multi-select 'batch-publish to shop X' from the local-products tab.

    Form: ``product_ids`` (repeated int), ``target_shop`` (one of TKSHOP_SHOPS),
    optional ``auto_activate``/``auto_fix`` toggles, optional ``back`` URL.

    The work is fire-and-forget (a single daemon thread with internal
    concurrency cap of 2 to avoid hammering multi-channel-api / TikTok rate
    limits). Operator gets a summary toast on redirect.
    """
    form = await request.form()
    target_shop = (form.get("target_shop") or "").strip()
    if not target_shop:
        raise HTTPException(status_code=400, detail="target_shop is required")
    if target_shop not in TKSHOP_SHOPS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown shop {target_shop!r}; configured: {TKSHOP_SHOPS}",
        )
    raw_ids = form.getlist("product_ids") if hasattr(form, "getlist") else []
    product_ids: list[int] = []
    for v in raw_ids:
        try:
            product_ids.append(int(v))
        except (TypeError, ValueError):
            continue
    if not product_ids:
        raise HTTPException(status_code=400, detail="no product_ids selected")
    auto_activate = (form.get("auto_activate") or "1").strip() not in ("0", "off", "false", "no")
    auto_fix = (form.get("auto_fix") or "1").strip() not in ("0", "off", "false", "no")

    svc = get_tkshop_service()
    task_id = f"tkshop_batch_publish:{int(time.time())}"
    run_async(
        task_id,
        svc.batch_publish_to_shop,
        product_ids, target_shop,
        auto_activate=auto_activate, auto_fix=auto_fix,
        label=f"批量上架到 {target_shop} ({len(product_ids)} 选)",
    )
    logger.info(
        "batch-publish dispatched: %d products → %s (task=%s)",
        len(product_ids), target_shop, task_id,
    )
    back = _safe_v2_redirect((form.get("back") or "").strip(), "/v2/products")
    sep = "&" if "?" in back else "?"
    msg = f"已派发 {len(product_ids)} 个商品上架到 {target_shop}"
    return RedirectResponse(
        url=f"{back}{sep}batch_summary={msg}",
        status_code=303,
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
      - product_ids: list of int — tkshop_products listing ids (产品列表页)
      - local_product_ids: list of int — local_products master ids (本地产品库页)
      - fmt: 'xlsx' (default, embeds main images) or 'zip' (CSV + images folder)
    """
    form = await request.form()

    def _int_ids(field: str) -> list[int]:
        raw = form.getlist(field) if hasattr(form, "getlist") else []
        out: list[int] = []
        for v in raw:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                continue
        return out

    fmt = (form.get("fmt") or "xlsx").strip().lower()
    local_ids = _int_ids("local_product_ids")
    ids = _int_ids("product_ids")
    if not ids and not local_ids:
        raise HTTPException(status_code=400, detail="no products selected")

    svc = get_tkshop_service()
    from datetime import datetime as _dt
    stamp = _dt.now().strftime("%Y%m%d_%H%M%S")

    # Master-catalog export takes precedence when local_product_ids is given.
    rows = svc.export_local_products_rows(local_ids) if local_ids else None
    name_stub = "local_products" if local_ids else "tkshop_products"

    if fmt == "zip":
        data = svc.export_products_zip(ids, rows=rows)
        filename = f"{name_stub}_{stamp}.zip"
        media = "application/zip"
    else:
        data = svc.export_products_xlsx(ids, rows=rows)
        filename = f"{name_stub}_{stamp}.xlsx"
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
    # Land on the master page: that's the canonical editor in the master-
    # first model. The underlying tkshop_products row still exists as a
    # publish target but is invisible in the operator's normal flow.
    lp_id = svc.get_or_create_local_product(pack_id)
    return RedirectResponse(
        url=_safe_v2_redirect(form.get("return_to"), f"/v2/local-products/{lp_id}"),
        status_code=303,
    )


@router.get("/products/{product_id:int}", response_class=HTMLResponse)
def v2_product_detail(request: Request, product_id: int):
    svc = get_tkshop_service()
    p = svc.get_product(product_id)
    if not p:
        return templates.TemplateResponse(
            "v2_not_found.html",
            {
                "request": request,
                "page_title": "listing 不存在",
                "what": f"listing #{product_id}",
                "back_url": "/v2/products",
                "back_label": "← 返回产品列表",
                "hint": "可能已被删除，或链接里的 id 写错了。",
            },
            status_code=404,
        )
    p["created_human"] = _fmt_ts(p.get("created_at"))
    p["published_human"] = _fmt_ts(p.get("published_at"))
    import sqlite3 as _sql
    _fb = _sql.connect("data/ops_workbench.db")
    try:
        p["pack_cover_url"] = _resolve_listing_cover_url(
            _fb,
            product_id=product_id,
            pack_id=p.get("pack_id") or 0,
            pack_cover_path=p.get("cover_image_path") or "",
            updated_at=int(p.get("updated_at") or 0),
        )
    finally:
        _fb.close()
    for img in p.get("images", []):
        img["url"] = _versioned_product_image_url(img)
        img["created_human"] = _fmt_ts(img.get("created_at"))
    for log in p.get("publish_logs", []):
        log["created_human"] = _fmt_ts(log.get("created_at"))
    return templates.TemplateResponse(
        "v2_product_detail.html",
        {
            "request": request,
            "page_title": f"产品 #{product_id}",
            "product": p,
            "flash_ok": request.query_params.get("ok"),
            "flash_err": request.query_params.get("error"),
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
        count = max(1, min(int(payload.get("count", 5)), 8))
    except (TypeError, ValueError):
        count = 5
    language = (str(payload.get("language") or "en")).lower().strip() or "en"
    size = (str(payload.get("size") or "1024x1024")).strip() or "1024x1024"
    mode = (str(payload.get("mode") or "all")).strip().lower()
    if mode not in {"all", "main", "secondary"}:
        mode = "all"
    conn = _open_db()
    try:
        existing_images = conn.execute(
            "SELECT COUNT(*) FROM tkshop_product_images WHERE product_id = ?",
            (product_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    replace_existing_ai, secondary_count = _auto_design_mode_params(
        mode=mode, count=count, existing_images=int(existing_images or 0),
    )
    task_id = f"tkshop_auto_design:{product_id}"
    started = run_async(
        task_id,
        _run_listing_auto_design_bg, product_id,
        secondary_count=secondary_count,
        language=language,
        replace_existing_ai=replace_existing_ai,
        size=size,
        mode=mode,
        label=f"AI 主图设计 (product #{product_id}, mode={mode}, {count} 张, {language}, {size})",
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
        {**i, "url": _versioned_product_image_url(i)}
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


@router.post("/products/{product_id:int}/fix-rejection")
async def v2_product_fix_rejection(request: Request, product_id: int):
    """Pull the TikTok audit-failure reasons for this product, ask AI to
    rewrite the offending fields, then push the patched listing back.
    Background so the operator can keep working — they refresh to see
    the new status. Returns JSON when an XHR caller asks for it.
    """
    svc = get_tkshop_service()
    task_id = f"tkshop_fix_rejection:{product_id}"
    started = run_async(
        task_id,
        svc.fix_rejection_and_resubmit, product_id,
        label=f"修复审核拒绝 (product #{product_id})",
    )
    if request.headers.get("accept", "").startswith("application/json") or \
       request.headers.get("x-requested-with", "").lower() == "fetch":
        return JSONResponse({
            "ok": True, "task_id": task_id, "started": started,
            "message": "已派发后台任务：拉取审核原因 → AI 修改 → 重新提交",
        })
    return RedirectResponse(
        url=f"/v2/products/{product_id}?fix_rejection=1", status_code=303,
    )


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


@router.post("/products/{product_id:int}/apply-discount")
async def v2_product_apply_discount(request: Request, product_id: int):
    """Manually (re-)apply the standing discount to a published product.

    Used when auto-discount on publish failed (e.g. middle layer was down) or
    the operator wants to re-apply at a different percent. ``force=True`` so a
    prior applied/failed state doesn't block a retry.
    """
    form = await request.form()
    raw = (form.get("percent") or "").strip()
    percent = None
    if raw:
        try:
            percent = float(raw)
        except ValueError:
            percent = None
    svc = get_tkshop_service()
    try:
        result = svc.apply_discount(product_id, percent=percent, force=True)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if result.get("applied"):
        flash = {"ok": f"已应用 {result.get('percent')}% 折扣"}
    else:
        reason = result.get("error") or result.get("reason") or "未知原因"
        flash = {"error": f"折扣未应用：{reason}"}
    return RedirectResponse(
        url=_append_query(f"/v2/products/{product_id}", flash), status_code=303,
    )


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
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        for p in products:
            p["created_human"] = _fmt_ts(p.get("created_at"))
            p["updated_human"] = _fmt_ts(p.get("updated_at"))
            cover = ""
            if p.get("primary_pack_id"):
                rr = conn.execute("SELECT cover_image_path FROM packs WHERE id=?",
                                  (p["primary_pack_id"],)).fetchone()
                if rr and rr["cover_image_path"]:
                    cover = _path_to_v2_url(rr["cover_image_path"])
            p["cover_url"] = cover
            # needs-attention flag for the operations '待处理' filter
            st = (p.get("publish_status") or "").lower()
            p["needs_attention"] = (st in ("draft", "failed")
                                    or (st == "draft_on_platform"))
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
        active_packs = [dict(r) for r in conn.execute(
            "SELECT id, display_name, pack_uid FROM packs WHERE status='active' "
            "ORDER BY id DESC LIMIT 200"
        ).fetchall()]
    finally:
        conn.close()
    return templates.TemplateResponse(
        "v2_lab_multi_sku_list.html",
        {
            "request": request,
            "page_title": "🧪 Lab · 多 SKU 产品",
            "products": products,
            "topics": topics,
            "packs": active_packs,
            "tkshop_shops": TKSHOP_SHOPS,
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
    raw_sp = (form.get("selling_points") or "").strip()
    raw_kw = (form.get("keywords") or "").strip()
    selling_points = [l.strip().lstrip("-*•").strip() for l in raw_sp.splitlines() if l.strip()]
    keywords = [l.strip().lstrip("#").strip() for l in raw_kw.splitlines() if l.strip()]
    detail_main_raw_text = (form.get("detail_main_raw_text") or "").strip()
    shop = (form.get("shop") or "").strip()

    # Per-pack image overrides: the form posts ``pack_image_path__<pack_id>``
    # fields when the operator picked a master image for that pack. Missing
    # entries mean "use the existing pack-cover fallback at publish time".
    pack_image_paths: dict[int, str] = {}
    for k, v in form.items() if hasattr(form, "items") else []:
        if not k.startswith("pack_image_path__"):
            continue
        try:
            key_pack_id = int(k.split("__", 1)[1])
        except (TypeError, ValueError):
            continue
        path_val = (v or "").strip()
        if path_val:
            pack_image_paths[key_pack_id] = path_val

    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    try:
        pid = svc.create_from_topic(
            topic_id=topic_id, pack_ids=pack_ids,
            sales_attribute_name=sales_attribute_name,
            title=title, description_html=description_html,
            selling_points=selling_points, keywords=keywords,
            detail_main_raw_text=detail_main_raw_text,
            shop=shop,
            pack_image_paths=pack_image_paths or None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse(url=f"/v2/lab/multi-sku/{pid}", status_code=303)


@router.get("/lab/multi-sku/new", response_class=HTMLResponse)
def v2_lab_multi_sku_new(request: Request):
    """Single-page visual builder for scenario ①: pick topic + shop, select
    packs by cover, AI-generate title/description, create."""
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        topics = conn.execute(
            """SELECT ht.id, ht.topic_name,
                      (SELECT COUNT(DISTINCT pk.id)
                         FROM topic_plans tp
                    LEFT JOIN pack_series ps ON ps.plan_id = tp.id
                    LEFT JOIN packs pk       ON pk.series_id = ps.id
                        WHERE tp.topic_id = ht.id) AS pack_count
                 FROM hot_topics ht ORDER BY ht.id DESC LIMIT 200"""
        ).fetchall()
        topics = [dict(t) for t in topics if (t["pack_count"] or 0) >= 1]
    finally:
        conn.close()
    return templates.TemplateResponse(
        "v2_lab_multi_sku_new.html",
        {
            "request": request,
            "page_title": "新建多 SKU 产品",
            "topics": topics,
            "tkshop_shops": TKSHOP_SHOPS,
            "tkshop_default_shop": TKSHOP_DEFAULT_SHOP,
        },
    )


@router.post("/lab/multi-sku/ai-listing")
async def v2_lab_multi_sku_ai_listing(request: Request):
    """JSON: AI-generate {title, description_html} for the selected topic+packs."""
    form = await request.form()
    try:
        topic_id = int(form.get("topic_id") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad topic_id"}, status_code=400)
    pack_ids = []
    for v in (form.getlist("pack_ids") if hasattr(form, "getlist") else []):
        try:
            pack_ids.append(int(v))
        except (TypeError, ValueError):
            continue
    sales_attribute_name = (form.get("sales_attribute_name") or "Theme").strip() or "Theme"
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    try:
        out = get_lab_multi_sku_service().ai_generate_listing(
            topic_id, pack_ids, sales_attribute_name=sales_attribute_name,
        )
    except Exception as e:
        logger.exception("msku ai-listing failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
    return JSONResponse({"ok": True, **out})


@router.post("/lab/multi-sku/{product_id:int}/ai-listing")
async def v2_lab_multi_sku_product_ai_listing(product_id: int):
    """JSON: AI-generate {title, description_html} for an EXISTING product,
    deriving topic + packs from its own SKUs (used by the detail page)."""
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    svc = get_lab_multi_sku_service()
    p = svc.get_product(product_id)
    if not p:
        return JSONResponse({"ok": False, "error": "product not found"}, status_code=404)
    pack_ids = [s["pack_id"] for s in (p.get("skus") or []) if s.get("pack_id")]
    variant_values = [s["sales_attribute_value"] for s in (p.get("skus") or []) if s.get("pack_id")]
    try:
        out = svc.ai_generate_listing(
            p.get("topic_id") or 0,
            pack_ids,
            sales_attribute_name=p.get("sales_attribute_name") or "Theme",
            variant_values=variant_values,
        )
    except Exception as e:
        logger.exception("msku product ai-listing failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
    return JSONResponse({"ok": True, **out})


@router.get("/lab/multi-sku/topics/{topic_id:int}/packs")
def v2_lab_multi_sku_topic_packs(topic_id: int):
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        rows = conn.execute(
            """SELECT pk.id, pk.display_name, pk.pack_uid, pk.total_stickers,
                      pk.cover_image_path,
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
    packs = []
    for r in rows:
        d = dict(r)
        d["cover_url"] = _path_to_v2_url(d.get("cover_image_path") or "")
        packs.append(d)
    return JSONResponse({"ok": True, "packs": packs})


@router.get("/lab/multi-sku/packs/{pack_id:int}/master-images")
def v2_lab_multi_sku_pack_master_images(pack_id: int):
    """Return the master gallery images for ``pack_id`` so the multi-SKU
    builder can offer them as per-variant thumbnail picks. Falls back to
    the pack's cover + first ``ok`` previews when no master images exist
    yet so the picker is never empty.
    """
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        lp_row = conn.execute(
            "SELECT id FROM local_products WHERE pack_id = ?", (pack_id,),
        ).fetchone()
        images: list[dict] = []
        if lp_row:
            for r in conn.execute(
                """SELECT id, role, source, local_path, sort_order, created_at
                     FROM local_product_images
                    WHERE local_product_id = ?
                    ORDER BY role = 'main' DESC, sort_order ASC, id ASC""",
                (lp_row["id"],),
            ):
                d = dict(r)
                d["url"] = _path_to_v2_url(d.get("local_path") or "")
                images.append(d)
        # Always include the pack cover as the trailing fallback option so
        # the operator can revert to the historical default.
        cover_row = conn.execute(
            "SELECT cover_image_path FROM packs WHERE id = ?", (pack_id,),
        ).fetchone()
        if cover_row and cover_row["cover_image_path"]:
            images.append({
                "id": 0,
                "role": "pack_cover",
                "source": "pack",
                "local_path": cover_row["cover_image_path"],
                "url": _path_to_v2_url(cover_row["cover_image_path"]),
            })
    finally:
        conn.close()
    return JSONResponse({"ok": True, "pack_id": pack_id, "images": images})


# ---------------------------------------------------------------------------
# Product catalog (scenario ②): hierarchical, progressive-loading merge finder
# ---------------------------------------------------------------------------

@router.get("/lab/product-catalog", response_class=HTMLResponse)
def v2_product_catalog(request: Request):
    """Standalone catalog page: browse the major→sub→product tree; per product,
    run matching (on demand, AJAX) and do a local merge."""
    from src.services.product_catalog import get_product_catalog
    cat = get_product_catalog()
    shops_tree = []
    for shop in (cat.list_shops() or TKSHOP_SHOPS):
        tree = []
        for m in cat.load_majors(shop):
            subs = [
                {**s, "products": cat.load_products(shop, m["slug"], s["slug"])}
                for s in cat.load_subs(shop, m["slug"])
            ]
            tree.append({**m, "subs": subs})
        shops_tree.append({"shop": shop, "tree": tree})
    return templates.TemplateResponse(
        "v2_product_catalog.html",
        {
            "request": request,
            "page_title": "🗂️ 产品目录 · 分层合并",
            "shops_tree": shops_tree,
            "empty": all(not st["tree"] for st in shops_tree),
            "tkshop_shops": TKSHOP_SHOPS,
        },
    )


@router.get("/lab/product-catalog/match")
def v2_product_catalog_match(ref: str, shop: str = ""):
    """JSON: classify a product down the tree and return its merge candidates
    (the same-theme products in its sub-category, ranked)."""
    from src.services.product_catalog.matcher import get_catalog_matcher
    try:
        out = get_catalog_matcher().find_candidates_for_ref(ref, shop=shop.strip())
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    except Exception as e:
        logger.exception("catalog match failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
    return JSONResponse({"ok": True, **out})


@router.post("/lab/product-catalog/merge-local")
async def v2_product_catalog_merge_local(request: Request):
    """Sub-case 1: fold new_ref + target_refs into one multi-SKU product
    (title/description from the new product)."""
    form = await request.form()
    new_ref = (form.get("new_ref") or "").strip()
    target_refs = [
        t.strip() for t in (form.getlist("target_refs") if hasattr(form, "getlist") else [])
        if t.strip()
    ]
    if not new_ref or not target_refs:
        return JSONResponse({"ok": False, "error": "new_ref and target_refs required"}, status_code=400)
    from src.services.product_catalog.merge import get_merge_service
    try:
        res = get_merge_service().merge_local(new_ref, target_refs)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("catalog merge-local failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
    return JSONResponse(res)


@router.post("/lab/product-catalog/rebuild")
def v2_product_catalog_rebuild():
    """Re-cluster every shop's library (local + platform live) into fresh trees
    (one LLM clustering call per shop)."""
    from src.services.product_catalog.classifier import get_catalog_classifier
    try:
        res = get_catalog_classifier().rebuild_all(list(TKSHOP_SHOPS))
    except Exception as e:
        logger.exception("catalog rebuild failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
    return JSONResponse(res)


@router.post("/lab/multi-sku/from-pack/{pack_id:int}")
async def v2_lab_multi_sku_from_pack(request: Request, pack_id: int):
    """②a multi-SKU NEW: start a new multi-SKU product seeded with this pack as
    its first SKU, then open it so the user can add more SKUs / AI-fill."""
    form = await request.form()
    shop = (form.get("shop") or "").strip()
    from src.services.lab_multi_sku import get_lab_multi_sku_service
    try:
        pid = get_lab_multi_sku_service().create_from_pack(pack_id, shop=shop)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return RedirectResponse(url=f"/v2/lab/multi-sku/{pid}", status_code=303)


@router.get("/lab/multi-sku/pack-match")
def v2_lab_multi_sku_pack_match(pack_id: int, shop: str = ""):
    """②b: classify a pack down a shop's catalog tree and return same-theme
    existing products it could be added to as a new SKU."""
    from src.services.product_catalog.matcher import get_catalog_matcher
    try:
        out = get_catalog_matcher().find_candidates_for_pack(pack_id, shop.strip())
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    except Exception as e:
        logger.exception("pack-match failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
    return JSONResponse({"ok": True, **out})


def _resolve_target_tiktok(target_ref: str, shop: str) -> tuple[str, str, Optional[JSONResponse]]:
    """target_ref → (tiktok_id, shop, error_response). Platform refs carry the
    id; published single-SKU (tkshop) refs are looked up."""
    kind, _, rid = target_ref.partition(":")
    if kind == "platform":
        return rid, shop, None
    if kind == "tkshop":
        import sqlite3 as _sql
        conn = _sql.connect("data/ops_workbench.db"); conn.row_factory = _sql.Row
        try:
            r = conn.execute("SELECT tiktok_product_id, shop FROM tkshop_products WHERE id=?",
                             (int(rid),)).fetchone()
        finally:
            conn.close()
        if not r or not (r["tiktok_product_id"] or "").strip():
            return "", shop, JSONResponse(
                {"ok": False, "error": "该单SKU产品未发布到平台,无法并入;请改用「多SKU新建」"},
                status_code=400)
        return r["tiktok_product_id"].strip(), shop or (r["shop"] or ""), None
    return "", shop, JSONResponse(
        {"ok": False, "error": f"目标类型 {kind} 不支持平台合并"}, status_code=400)


@router.get("/lab/multi-sku/merge-preview")
def v2_lab_multi_sku_merge_preview(pack_id: int, target_ref: str, shop: str = ""):
    """②b preview: fetch the live target + project the post-merge result for
    review BEFORE the irreversible write. Self-heals: stale (deleted) products
    come back flagged so the UI can drop them."""
    tiktok_id, shop, err = _resolve_target_tiktok(target_ref, shop.strip())
    if err:
        return err
    from src.services.product_catalog.merge import get_merge_service
    try:
        out = get_merge_service().preview_platform_merge(pack_id, tiktok_id, shop)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    except Exception as e:
        logger.exception("merge-preview failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
    if out.get("stale") and tiktok_id:
        # self-heal: the product is gone on the platform → drop it from the catalog
        from src.services.product_catalog import get_product_catalog
        out["pruned"] = get_product_catalog().prune_product(tiktok_id)
    if out.get("ok") and out.get("new_variant"):
        ip = out["new_variant"].get("image_path") or ""
        out["new_variant"]["image_url"] = _path_to_v2_url(ip) if ip else ""
    return JSONResponse(out)


@router.post("/lab/multi-sku/add-pack-sku")
async def v2_lab_multi_sku_add_pack_sku(request: Request):
    """②b: add a pack as a new SKU to a chosen existing product.

    Local multi-SKU target (lab_msku) → safe local add (push from the detail
    page when ready). Platform/single-SKU targets need the live-merge path
    (情况2), not yet wired — returned with a clear flag."""
    form = await request.form()
    target_ref = (form.get("target_ref") or "").strip()
    try:
        pack_id = int(form.get("pack_id") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "bad pack_id"}, status_code=400)
    kind, _, rid = target_ref.partition(":")

    if kind == "lab_msku":
        from src.services.lab_multi_sku import get_lab_multi_sku_service
        svc = get_lab_multi_sku_service()
        try:
            sku_id = svc.add_sku_from_pack(int(rid), pack_id)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except Exception as e:
            logger.exception("add-pack-sku failed")
            return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
        return JSONResponse({
            "ok": True, "added": sku_id is not None,
            "already_present": sku_id is None,
            "product_id": int(rid),
            "detail_url": f"/v2/lab/multi-sku/{rid}",
            "note": "已加为本地草稿 SKU;在详情页确认后推送到平台",
        })

    # platform live / published single-SKU → 情况2: add SKU into the live link.
    shop = (form.get("shop") or "").strip()
    tiktok_id, shop, err = _resolve_target_tiktok(target_ref, shop)
    if err:
        return err
    new_variant_value = (form.get("new_variant_value") or "").strip()
    refresh_desc = (form.get("refresh_desc") or "").strip().lower() in ("1", "true", "on", "yes")
    from src.services.product_catalog.merge import get_merge_service
    try:
        res = get_merge_service().merge_into_platform(
            pack_id, tiktok_id, shop,
            new_variant_value=new_variant_value, refresh_desc=refresh_desc)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("merge_into_platform failed")
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"[:200]}, status_code=500)
    return JSONResponse(res, status_code=200 if res.get("ok") else 502)


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
    for s in (p.get("skus") or []):
        s["cover_url"] = _path_to_v2_url(s["pack_cover"]) if s.get("pack_cover") else ""
    existing_pack_ids = {s.get("pack_id") for s in (p.get("skus") or []) if s.get("pack_id")}
    import sqlite3 as _sql
    conn = _sql.connect("data/ops_workbench.db")
    conn.row_factory = _sql.Row
    try:
        rows = []
        if p.get("topic_id"):
            rows = conn.execute(
                """SELECT pk.id, pk.display_name, pk.pack_uid, pk.total_stickers, pk.cover_image_path
                     FROM topic_plans tp
                LEFT JOIN pack_series ps ON ps.plan_id = tp.id
                LEFT JOIN packs pk       ON pk.series_id = ps.id
                    WHERE tp.topic_id = ? AND pk.id IS NOT NULL
                    ORDER BY pk.id""",
                (p["topic_id"],),
            ).fetchall()
        if not rows:
            # no topic (created/merged from packs) → offer recent active packs
            rows = conn.execute(
                """SELECT id, display_name, pack_uid, total_stickers, cover_image_path
                     FROM packs WHERE status='active' ORDER BY id DESC LIMIT 36"""
            ).fetchall()
        avail_packs = []
        for r in rows:
            d = dict(r)
            d["cover_url"] = _path_to_v2_url(d["cover_image_path"]) if d.get("cover_image_path") else ""
            d["in_product"] = d["id"] in existing_pack_ids
            avail_packs.append(d)
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
        detail_main_raw_text=(form.get("detail_main_raw_text") or "").strip() or None,
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


# ----------------------------------------------------------------------
# Ads — TikTok 人群测试实验 & 优质人群库
#
# UI track (W). Backend lives in src/services/tiktok/ads_experiment_service.py
# and is built in parallel (Track B). We import those services *lazily inside
# each route* so a missing symbol during parallel dev never breaks module
# import. Read paths query the tk_ad_* tables directly (see migration 020);
# all mutations + AI audience generation go through the service per the
# docs/tiktok_ads_contract.md signatures.
# ----------------------------------------------------------------------

# Status vocabulary → friendly Chinese labels + pill class.
# Vocabulary matches the backend (ads_experiment_service) + migration 020:
#   experiments: draft / running / evaluating / done / failed / killed
#   audiences:   candidate / testing / winning / inconclusive / paused / retired
#   adgroups (local): preview / pending / running / paused / failed
_ADS_EXP_STATUS_ZH = {
    "draft": "草稿",
    "running": "投放中",
    "evaluating": "评估中",
    "done": "已结束",
    "failed": "失败",
    "killed": "已停投",
}
_ADS_EXP_STATUS_PILL = {
    "draft": "pending",
    "running": "running",
    "evaluating": "running",
    "done": "ok",
    "failed": "error",
    "killed": "error",
}
_ADS_AUDIENCE_STATUS_ZH = {
    "candidate": "候选",
    "testing": "测试中",
    "winning": "优质",
    "inconclusive": "数据不足",
    "paused": "暂停",
    "retired": "已淘汰",
}
_ADS_AUDIENCE_STATUS_PILL = {
    "candidate": "pending",
    "testing": "running",
    "winning": "ok",
    "inconclusive": "pending",
    "paused": "pending",
    "retired": "error",
}
_ADS_GROUP_STATUS_PILL = {
    "preview": "pending",
    "pending": "pending",
    "enable": "ok",
    "enabled": "ok",
    "running": "running",
    "paused": "pending",
    "disable": "error",
    "disabled": "error",
    "failed": "error",
    "killed": "error",
    "error": "error",
}


def _ads_exp_status_label(status: Any) -> str:
    s = str(status or "").strip().lower()
    return _ADS_EXP_STATUS_ZH.get(s, status or "—")


def _ads_exp_status_pill(status: Any) -> str:
    return _ADS_EXP_STATUS_PILL.get(str(status or "").strip().lower(), "pending")


def _ads_audience_status_label(status: Any) -> str:
    s = str(status or "").strip().lower()
    return _ADS_AUDIENCE_STATUS_ZH.get(s, status or "—")


def _ads_audience_status_pill(status: Any) -> str:
    return _ADS_AUDIENCE_STATUS_PILL.get(str(status or "").strip().lower(), "pending")


def _ads_group_status_pill(status: Any) -> str:
    return _ADS_GROUP_STATUS_PILL.get(str(status or "").strip().lower(), "pending")


# ----------------------------------------------------------------------
# Phase 2 — objective-aware metric columns.
#
# The visible metric columns + the 主 KPI column depend on the experiment's
# `objective` (migration 021 makes objective a first-class input). The
# leaderboard rows already carry `primary_kpi_value` + `primary_kpi_name`
# from the service; here we only describe which *raw aggregate* columns to
# show per objective and their 中文表头. Each column is
# (row_key, 中文表头, fmt) where fmt ∈ {"int", "num", "kpi"}.
#   - "int"  → thousands-grouped integer
#   - "num"  → 2-decimal float
#   - "kpi"  → primary KPI (uses primary_kpi_value/primary_kpi_name)
# The KPI column's header falls back to a per-objective default when the
# service hasn't filled `primary_kpi_name`.
_ADS_OBJECTIVE_LABELS = {
    "VIDEO_VIEWS": "视频播放",
    "ENGAGEMENT": "互动涨粉",
    "PRODUCT_SALES": "商品销售",
}

_ADS_OBJECTIVE_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "VIDEO_VIEWS": [
        ("video_views", "播放", "int"),
        ("video_6s", "6s", "int"),
        ("video_p100", "完播", "int"),
        ("primary_kpi", "CPV", "kpi"),
    ],
    "ENGAGEMENT": [
        ("follows", "涨粉", "int"),
        ("profile_visits", "主页访问", "int"),
        ("engagements", "互动", "int"),
        ("primary_kpi", "单粉成本", "kpi"),
    ],
    "PRODUCT_SALES": [
        ("orders", "成交", "int"),
        ("gmv", "GMV", "num"),
        ("primary_kpi", "ROAS", "kpi"),
    ],
}

# Generic fallback when objective is missing/unknown (current Phase-1 columns).
_ADS_GENERIC_COLUMNS: list[tuple[str, str, str]] = [
    ("orders", "订单", "int"),
    ("gmv", "GMV", "num"),
    ("roas", "ROAS", "num"),
]

# Default KPI header per objective when the service omits primary_kpi_name.
_ADS_KPI_DEFAULT_NAME = {
    "VIDEO_VIEWS": "CPV",
    "ENGAGEMENT": "单粉成本",
    "PRODUCT_SALES": "ROAS",
}


def _ads_normalize_objective(objective: Any) -> str:
    return str(objective or "").strip().upper()


def _ads_objective_label(objective: Any) -> str:
    """Friendly Chinese label for an objective, e.g. VIDEO_VIEWS → 视频播放."""
    obj = _ads_normalize_objective(objective)
    return _ADS_OBJECTIVE_LABELS.get(obj, objective or "—")


def _ads_metric_columns(objective: Any) -> list[dict[str, str]]:
    """Return the ordered metric-column descriptors for an objective.

    Falls back to the generic ROAS-centric columns when the objective is
    missing/unknown so the UI degrades gracefully (no 500, no blank table).
    Each descriptor is {"key", "label", "fmt"}.
    """
    obj = _ads_normalize_objective(objective)
    cols = _ADS_OBJECTIVE_COLUMNS.get(obj, _ADS_GENERIC_COLUMNS)
    return [{"key": k, "label": lbl, "fmt": fmt} for (k, lbl, fmt) in cols]


def _ads_kpi_label(objective: Any, primary_kpi_name: Any = None) -> str:
    """Header/label for the 主 KPI column: prefer the service-provided
    `primary_kpi_name`, else a per-objective default."""
    name = str(primary_kpi_name or "").strip()
    if name:
        return name
    obj = _ads_normalize_objective(objective)
    return _ADS_KPI_DEFAULT_NAME.get(obj, "主 KPI")


# Per-objective primary-KPI derivation from raw aggregates (used on the
# experiment-detail page, where rows are per-adgroup raw metrics without a
# precomputed primary_kpi_value). direction matters for sorting but not for
# display; here we only compute the value.
#   PRODUCT_SALES → ROAS  = gmv / spend            (higher better)
#   VIDEO_VIEWS   → CPV   = spend / video_views    (lower better)
#   ENGAGEMENT    → 单粉成本 = spend / follows       (lower better)
def _ads_derive_kpi(row_get, objective: Any) -> Optional[float]:
    obj = _ads_normalize_objective(objective)

    def _f(k: str) -> float:
        try:
            return float(row_get(k, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    spend = _f("spend")
    if obj == "PRODUCT_SALES":
        return (_f("gmv") / spend) if spend else 0.0
    if obj == "VIDEO_VIEWS":
        views = _f("video_views")
        return (spend / views) if views else 0.0
    if obj == "ENGAGEMENT":
        follows = _f("follows")
        return (spend / follows) if follows else 0.0
    # Generic fallback: roas-like.
    return (_f("gmv") / spend) if spend else 0.0


def _ads_cell_value(row: Any, col: dict[str, str], objective: Any = None) -> str:
    """Format one metric cell for `row` given a column descriptor. Used by both
    the experiment-detail and leaderboard tables so formatting stays consistent
    and missing keys degrade to 0 / —.

    For the KPI column it prefers a precomputed `primary_kpi_value` (leaderboard
    rows) and otherwise derives the KPI from raw aggregates + `objective`
    (experiment-detail adgroup rows)."""
    try:
        get = row.get  # dict-like (leaderboard rows + adgroup dicts)
    except AttributeError:
        def get(k, default=None):  # pragma: no cover - defensive
            return getattr(row, k, default)

    fmt = col.get("fmt")
    if fmt == "kpi":
        val = get("primary_kpi_value", None)
        if val is None:
            val = _ads_derive_kpi(get, objective)
        if val is None:
            return "—"
        try:
            return f"{float(val):.2f}"
        except (TypeError, ValueError):
            return str(val)

    key = col.get("key")
    val = get(key, 0)
    if fmt == "int":
        try:
            return "{:,}".format(int(val or 0))
        except (TypeError, ValueError):
            return "0"
    # default / "num"
    try:
        return f"{float(val or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _ads_advertiser_name(advertiser_id: Any) -> str:
    """Map an advertiser_id to its display name (tk_ads_accounts.name).

    Falls back to the raw id only if no name is on record. Used so the UI
    shows '广告主名称' instead of the numeric id.
    """
    aid = str(advertiser_id or "").strip()
    if not aid:
        return "—"
    try:
        with _open_db() as conn:
            row = conn.execute(
                "SELECT name FROM tk_ads_accounts WHERE advertiser_id = ?",
                (aid,),
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    name = (row["name"] if row else "") or ""
    return name.strip() or aid


templates.env.globals["ads_advertiser_name"] = _ads_advertiser_name
templates.env.globals["ads_exp_status_label"] = _ads_exp_status_label
templates.env.globals["ads_exp_status_pill"] = _ads_exp_status_pill
templates.env.globals["ads_audience_status_label"] = _ads_audience_status_label
templates.env.globals["ads_audience_status_pill"] = _ads_audience_status_pill
templates.env.globals["ads_group_status_pill"] = _ads_group_status_pill
templates.env.globals["ads_objective_label"] = _ads_objective_label
templates.env.globals["ads_metric_columns"] = _ads_metric_columns
templates.env.globals["ads_kpi_label"] = _ads_kpi_label
templates.env.globals["ads_cell_value"] = _ads_cell_value


def _ads_list_advertisers() -> tuple[list[dict[str, Any]], str]:
    """Best-effort fetch of authorized advertisers.

    Returns (advertisers, hint). On any failure (service not built yet, no
    credentials, no authorization) we degrade to an empty list + a friendly
    Chinese hint so the page never 500s.
    """
    try:
        from src.services.tiktok.tiktok_ads_service import get_tiktok_ads_service
    except Exception:
        return [], "广告投放服务尚未就绪（后端开发中）。"
    try:
        svc = get_tiktok_ads_service()
        advertisers = svc.list_advertisers() or []
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("ads list_advertisers failed: %s", e)
        return [], "连不上广告服务：请确认 multi-channel-api 正在运行（localhost:8000）。"
    if not advertisers:
        return [], "未检测到广告主：授权与 token 由 multi-channel-api 持有，请检查其 .env 的 TIKTOK_ADS_* 配置。"
    return [dict(a) for a in advertisers], ""


def _ads_list_identities() -> tuple[list[dict[str, Any]], str]:
    """Best-effort fetch of Spark ad identities (for video promotion).

    Degrades to an empty list + Chinese hint on any failure (service/method
    not built yet during parallel dev, no credentials, none configured).
    """
    try:
        from src.services.tiktok.ads_experiment_service import (
            get_ads_experiment_service,
        )
    except Exception:
        return [], "广告实验服务尚未就绪（后端开发中）。"
    try:
        svc = get_ads_experiment_service()
        fn = getattr(svc, "list_identities", None)
        if fn is None:
            return [], "身份列表接口尚未就绪（后端开发中）。"
        identities = fn() or []
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("ads list_identities failed: %s", e)
        return [], "无法获取广告身份列表，请先在中间层完成 Marketing 授权。"
    if not identities:
        return [], "尚未配置任何广告身份，投视频（Spark）前需先在 TikTok 后台准备身份。"
    return [dict(x) for x in identities], ""


def _ads_list_promotable_videos(
    pack_id: int | None = None,
    identity_id: str | None = None,
    identity_type: str | None = None,
    identity_bc_id: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Best-effort fetch of videos REALLY promotable as Spark ads.

    Source is TikTok's pull-mode catalog (the owned/linked ad identity's full
    posted-video list) — not the local tk_videos table — so every row is
    actually launchable. Each row carries `tiktok_video_id` (= promote_ref_id),
    `one_liner`, `cover_url`, and the `identity_id`/`identity_type` it belongs
    to. Degrades gracefully.
    """
    try:
        from src.services.tiktok.ads_experiment_service import (
            get_ads_experiment_service,
        )
    except Exception:
        return [], "广告实验服务尚未就绪（后端开发中）。"
    try:
        svc = get_ads_experiment_service()
        fn = getattr(svc, "list_promotable_videos", None)
        if fn is None:
            return [], "已发布视频接口尚未就绪（后端开发中）。"
        kw: dict[str, Any] = {"pack_id": pack_id}
        if identity_id:
            kw.update(identity_id=identity_id, identity_type=identity_type,
                      _identity_bc_id=identity_bc_id)
        videos = fn(**kw) or []
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("ads list_promotable_videos failed: %s", e)
        return [], "无法获取可投放视频列表，请确认 multi-channel-api 正在运行。"
    if not videos:
        return [], (
            "暂无可投放视频：广告身份需具备『可拉取视频』权限（can_pull_video），"
            "或该账号尚未发布视频。"
        )
    return [dict(v) for v in videos], ""


def _ads_advertisers_from_db(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    """Fast local fallback for the new-experiment form.

    The remote advertiser list can be slow or unavailable; the list page should
    still render immediately and use the last synced accounts if present.
    """
    try:
        rows = conn.execute(
            """
            SELECT advertiser_id, name, shop, currency, status
              FROM tk_ads_accounts
             WHERE COALESCE(advertiser_id, '') != ''
             ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                      name COLLATE NOCASE,
                      advertiser_id
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def _ads_pack_candidates(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, display_name, status FROM packs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "name": r["display_name"] or f"卡包 #{r['id']}",
            "status": r["status"],
        }
        for r in rows
    ]


def _ads_experiment_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.id, e.advertiser_id, e.promote_type, e.promote_ref_id,
               e.pack_id, e.tiktok_campaign_id, e.objective,
               e.per_adgroup_budget, e.currency, e.status,
               e.decision_summary, e.created_at, e.started_at, e.ended_at,
               p.display_name AS pack_name,
               COALESCE(gc.adgroup_count, 0) AS adgroup_count
          FROM tk_ad_experiments e
     LEFT JOIN packs p ON p.id = e.pack_id
     LEFT JOIN (
            SELECT experiment_id, COUNT(*) AS adgroup_count
              FROM tk_ad_groups
             GROUP BY experiment_id
        ) gc ON gc.experiment_id = e.id
         ORDER BY e.id DESC
        """,
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["pack_name"] = d.get("pack_name") or (f"卡包 #{d['pack_id']}" if d.get("pack_id") else "—")
        d["created_human"] = _fmt_ts(d.get("created_at"))
        d["started_human"] = _fmt_ts(d.get("started_at")) if d.get("started_at") else ""
        d["ended_human"] = _fmt_ts(d.get("ended_at")) if d.get("ended_at") else ""
        out.append(d)
    return out


@router.get("/ads/auth")
def v2_ads_auth_start(request: Request):
    """Begin TikTok Marketing OAuth: fetch the authorize URL from the middle
    layer and redirect the operator's browser to it. After consent TikTok
    redirects back to /v2/ads/auth/callback?auth_code=..."""
    try:
        from src.services.tiktok.tiktok_ads_service import get_tiktok_ads_service
        auth_url = get_tiktok_ads_service().get_auth_url()
    except Exception as e:
        logger.warning("ads get_auth_url failed: %s", e)
        return RedirectResponse(
            url=_append_query(
                "/v2/ads/experiments",
                {"error": f"获取授权链接失败：{str(e)[:160]}"},
            ),
            status_code=303,
        )
    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/ads/auth/callback")
def v2_ads_auth_callback(
    request: Request,
    auth_code: str | None = None,
    code: str | None = None,
):
    """OAuth redirect target. Forward the auth_code to the middle layer, which
    exchanges it for an advertiser token and returns the authorized
    advertisers (upserted into tk_ads_accounts)."""
    ac = (auth_code or code or "").strip()
    if not ac:
        return RedirectResponse(
            url=_append_query(
                "/v2/ads/experiments", {"error": "授权回调缺少 auth_code"},
            ),
            status_code=303,
        )
    try:
        from src.services.tiktok.tiktok_ads_service import get_tiktok_ads_service
        advertisers = get_tiktok_ads_service().handle_auth_callback(ac)
    except Exception as e:
        logger.warning("ads auth callback failed: %s", e)
        return RedirectResponse(
            url=_append_query(
                "/v2/ads/experiments", {"error": f"授权失败：{str(e)[:160]}"},
            ),
            status_code=303,
        )
    n = len(advertisers or [])
    return RedirectResponse(
        url=_append_query("/v2/ads/experiments", {"ok": f"授权成功，同步 {n} 个广告主"}),
        status_code=303,
    )


@router.get("/ads/experiments", response_class=HTMLResponse)
def v2_ads_experiments(request: Request):
    """List all audience experiments + a 新建实验 form."""
    conn = _open_db()
    try:
        try:
            experiments = _ads_experiment_rows(conn)
        except sqlite3.OperationalError:
            # Migration 020 not applied yet — degrade gracefully.
            experiments = []
        packs = _ads_pack_candidates(conn)
        advertisers = _ads_advertisers_from_db(conn)
    finally:
        conn.close()

    advertiser_hint = "" if advertisers else "广告主列表正在加载；若长时间为空，请检查 multi-channel-api。"
    identities: list[dict[str, Any]] = []
    identity_hint = "视频投放所需的 Spark 身份会在页面打开后异步加载。"
    videos: list[dict[str, Any]] = []
    video_hint = "已发布视频列表会在页面打开后异步加载。"

    flash_ok = request.query_params.get("created") or request.query_params.get("ok")
    flash_err = request.query_params.get("error")
    preview_json = request.query_params.get("preview") or ""

    return templates.TemplateResponse(
        "v2_ads_experiments.html",
        {
            "request": request,
            "page_title": "人群测试实验",
            "experiments": experiments,
            "packs": packs,
            "advertisers": advertisers,
            "advertiser_hint": advertiser_hint,
            "identities": identities,
            "identity_hint": identity_hint,
            "videos": videos,
            "video_hint": video_hint,
            "flash_ok": flash_ok,
            "flash_err": flash_err,
            "preview_json": preview_json,
        },
    )


@router.get("/ads/experiments/form-options")
def v2_ads_experiments_form_options(pack_id: int | None = None):
    """Async options for the new-experiment form.

    Keeping remote Marketing API calls out of the HTML route prevents a slow or
    unavailable middle layer from blocking the experiments list page.
    """
    advertisers, advertiser_hint = _ads_list_advertisers()
    if not advertisers:
        conn = _open_db()
        try:
            cached = _ads_advertisers_from_db(conn)
        finally:
            conn.close()
        if cached:
            advertisers = cached
            advertiser_hint = (
                f"{advertiser_hint} 已使用本地缓存。"
                if advertiser_hint else "已使用本地缓存广告主。"
            )

    identities, identity_hint = _ads_list_identities()
    # Reuse the identity we already fetched (pick the pull-capable one) so the
    # videos lookup doesn't re-call list_identities — saves a slow round-trip.
    _id = next((i for i in identities if i.get("can_pull_video")), (identities[0] if identities else None))
    if _id:
        videos, video_hint = _ads_list_promotable_videos(
            pack_id=pack_id,
            identity_id=_id.get("identity_id"),
            identity_type=_id.get("identity_type"),
            identity_bc_id=_id.get("identity_authorized_bc_id") or "",
        )
    else:
        videos, video_hint = [], "无可用广告身份，无法列出可投视频。"

    return JSONResponse(
        {
            "ok": True,
            "advertisers": advertisers,
            "advertiser_hint": advertiser_hint,
            "identities": identities,
            "identity_hint": identity_hint,
            "videos": videos,
            "video_hint": video_hint,
        }
    )


@router.get("/ads/experiments/{experiment_id:int}", response_class=HTMLResponse)
def v2_ads_experiment_detail(request: Request, experiment_id: int):
    """Experiment detail: adgroups + per-audience latest metrics."""
    conn = _open_db()
    try:
        try:
            exp_row = conn.execute(
                """
                SELECT e.*, p.display_name AS pack_name
                  FROM tk_ad_experiments e
             LEFT JOIN packs p ON p.id = e.pack_id
                 WHERE e.id = ?
                """,
                (experiment_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            raise HTTPException(status_code=404, detail="实验表尚未初始化（请先执行 migration 020）")
        if not exp_row:
            raise HTTPException(status_code=404, detail="实验不存在")
        experiment = dict(exp_row)
        experiment["pack_name"] = (
            experiment.get("pack_name")
            or (f"卡包 #{experiment['pack_id']}" if experiment.get("pack_id") else "—")
        )
        experiment["created_human"] = _fmt_ts(experiment.get("created_at"))
        experiment["started_human"] = _fmt_ts(experiment.get("started_at")) if experiment.get("started_at") else ""
        experiment["ended_human"] = _fmt_ts(experiment.get("ended_at")) if experiment.get("ended_at") else ""

        # adgroups joined with audience + the latest metrics snapshot for that
        # adgroup (max stat_date). Metrics are upserted per (adgroup, day).
        # Phase-2 metric columns (migration 021) may not exist yet on older DBs;
        # select them defensively and fall back to the Phase-1 column set.
        _base_metric_cols = (
            "m.stat_date, m.spend, m.impressions, m.clicks, m.ctr, "
            "m.conversions, m.orders, m.gmv, m.cpa, m.roas, m.currency, "
            "m.fetched_at"
        )
        _p2_metric_cols = (
            ", m.video_views, m.video_2s, m.video_6s, m.video_p100, "
            "m.avg_video_play, m.profile_visits, m.follows, m.engagements, "
            "m.reach, m.frequency"
        )

        def _group_sql(metric_cols: str) -> str:
            return f"""
            SELECT g.id, g.tiktok_adgroup_id, g.budget, g.status AS group_status,
                   a.id AS audience_id, a.name AS audience_name,
                   a.hypothesis, a.status AS audience_status,
                   {metric_cols}
              FROM tk_ad_groups g
         LEFT JOIN tk_ad_audiences a ON a.id = g.audience_id
         LEFT JOIN tk_ad_metrics_snapshots m
                ON m.tiktok_adgroup_id = g.tiktok_adgroup_id
               AND m.stat_date = (
                   SELECT MAX(stat_date) FROM tk_ad_metrics_snapshots m2
                    WHERE m2.tiktok_adgroup_id = g.tiktok_adgroup_id
               )
             WHERE g.experiment_id = ?
             ORDER BY g.id ASC
            """

        try:
            group_rows = conn.execute(
                _group_sql(_base_metric_cols + _p2_metric_cols),
                (experiment_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            # Migration 021 not applied — degrade to Phase-1 columns.
            group_rows = conn.execute(
                _group_sql(_base_metric_cols),
                (experiment_id,),
            ).fetchall()
        adgroups = []
        for r in group_rows:
            d = dict(r)
            d["audience_name"] = d.get("audience_name") or "—"
            d["fetched_human"] = _fmt_ts(d.get("fetched_at")) if d.get("fetched_at") else ""
            adgroups.append(d)
    finally:
        conn.close()

    flash_ok = request.query_params.get("ok")
    flash_err = request.query_params.get("error")

    return templates.TemplateResponse(
        "v2_ads_experiments.html",
        {
            "request": request,
            "page_title": f"实验 #{experiment_id} 详情",
            "detail_mode": True,
            "experiment": experiment,
            "adgroups": adgroups,
            "flash_ok": flash_ok,
            "flash_err": flash_err,
        },
    )


@router.post("/ads/experiments/new")
async def v2_ads_experiment_new(request: Request):
    """Generate audience candidates (if needed) then preview an experiment.

    Per the contract we always run create_experiment(dry_run=True) here so the
    operator sees the planned campaign/adgroup structure before any real spend;
    real投放 (dry_run=False) is a separate, explicit step owned by the backend.
    """
    form = await request.form()
    try:
        pack_id = int(form.get("pack_id") or 0)
    except (TypeError, ValueError):
        pack_id = 0
    advertiser_id = (form.get("advertiser_id") or "").strip()
    promote_type = (form.get("promote_type") or "shop_product").strip()
    objective = (form.get("objective") or "").strip().upper()
    identity_id = (form.get("identity_id") or "").strip()
    # promote_ref_id source depends on promote_type:
    #   video        → selected posted videos' tiktok_video_id(s) — now MULTI
    #   shop_product → the typed product id (existing behaviour)
    promote_ref_id = (form.get("promote_ref_id") or "").strip()
    # Multi-video: UI submits comma-joined ids in `video_ref_ids` (falls back to
    # the legacy single `video_ref_id`).
    _vids_raw = (form.get("video_ref_ids") or form.get("video_ref_id") or "")
    video_ref_ids = [v.strip() for v in str(_vids_raw).split(",") if v.strip()]
    try:
        budget = float(form.get("per_adgroup_budget") or 0)
    except (TypeError, ValueError):
        budget = 0.0
    try:
        n = int(form.get("audience_count") or 3)
    except (TypeError, ValueError):
        n = 3

    def _back(**extra: Any) -> RedirectResponse:
        return RedirectResponse(
            url=_append_query("/v2/ads/experiments", extra),
            status_code=303,
        )

    # Constrain objective by promote_type (and default it sensibly).
    _ALLOWED_OBJ = {
        "video": {"VIDEO_VIEWS", "ENGAGEMENT"},
        "shop_product": {"PRODUCT_SALES"},
    }
    if promote_type not in _ALLOWED_OBJ:
        return _back(error="无效的推广类型")
    if promote_type == "shop_product":
        objective = "PRODUCT_SALES"
    if objective not in _ALLOWED_OBJ[promote_type]:
        return _back(error="该推广类型不支持所选投放目标")

    if not pack_id:
        return _back(error="请选择卡包")
    if not advertiser_id:
        return _back(error="请选择广告主（需先完成授权）")
    if budget <= 0:
        return _back(error="单组预算需大于 0")

    if promote_type == "video":
        # Spark: identity + at least one posted video required.
        if not identity_id:
            return _back(error="投视频需选择广告身份（Spark）")
        if not video_ref_ids:
            return _back(error="投视频需至少选择一条已发布视频")
        promote_ref_id = video_ref_ids[0]  # primary (compat)
    else:
        # Shop product: single typed id, no Spark identity.
        identity_id = ""
        video_ref_ids = [promote_ref_id] if promote_ref_id else []

    try:
        from src.services.tiktok.ads_experiment_service import (
            get_ads_experiment_service,
        )
    except Exception as e:
        logger.warning("ads_experiment_service import failed: %s", e)
        return _back(error="广告实验服务尚未就绪（后端开发中）")

    try:
        svc = get_ads_experiment_service()
        # Generate fresh AI audience candidates for the pack, then use their ids.
        candidates = svc.generate_audience_candidates(pack_id, n=n) or []
        audience_ids = [int(c["id"]) for c in candidates if c.get("id") is not None]
        if not audience_ids:
            return _back(error="未能生成人群候选，请稍后重试")
        _create_kwargs = dict(
            advertiser_id=advertiser_id,
            promote_type=promote_type,
            promote_ref_id=promote_ref_id,
            promote_ref_ids=video_ref_ids,
            pack_id=pack_id,
            audience_ids=audience_ids,
            per_adgroup_budget=budget,
            objective=objective,
            creative={},
            identity_id=identity_id,
            dry_run=True,
        )
        try:
            result = svc.create_experiment(**_create_kwargs)
        except TypeError:
            # Track B may not have shipped the `identity_id` kwarg yet — retry
            # without it so video Spark experiments still preview during
            # parallel development.
            _create_kwargs.pop("identity_id", None)
            result = svc.create_experiment(**_create_kwargs)
    except Exception as e:
        logger.warning("create_experiment(dry_run) failed: %s", e)
        return _back(error=f"创建预演失败：{str(e)[:160]}")

    preview = result.get("preview") if isinstance(result, dict) else None
    return _back(
        created=1,
        preview=_json.dumps(preview, ensure_ascii=False)[:4000] if preview else "",
    )


def _ads_call_and_redirect(experiment_id: int, method: str) -> RedirectResponse:
    target = f"/v2/ads/experiments/{experiment_id}"
    try:
        from src.services.tiktok.ads_experiment_service import (
            get_ads_experiment_service,
        )
    except Exception as e:
        logger.warning("ads_experiment_service import failed: %s", e)
        return RedirectResponse(
            url=_append_query(target, {"error": "广告实验服务尚未就绪（后端开发中）"}),
            status_code=303,
        )
    try:
        svc = get_ads_experiment_service()
        getattr(svc, method)(experiment_id)
    except Exception as e:
        logger.warning("ads %s(%s) failed: %s", method, experiment_id, e)
        return RedirectResponse(
            url=_append_query(target, {"error": str(e)[:400]}),
            status_code=303,
        )
    return RedirectResponse(
        url=_append_query(target, {"ok": 1}),
        status_code=303,
    )


@router.post("/ads/experiments/{experiment_id:int}/refresh")
async def v2_ads_experiment_refresh(request: Request, experiment_id: int):
    return _ads_call_and_redirect(experiment_id, "refresh_metrics")


@router.post("/ads/experiments/{experiment_id:int}/evaluate")
async def v2_ads_experiment_evaluate(request: Request, experiment_id: int):
    return _ads_call_and_redirect(experiment_id, "evaluate_experiment")


@router.post("/ads/experiments/{experiment_id:int}/launch")
async def v2_ads_experiment_launch(request: Request, experiment_id: int):
    """REAL launch (dry_run=False): promote a previewed draft to live ads."""
    return _ads_call_and_redirect(experiment_id, "launch_experiment")


@router.post("/ads/experiments/{experiment_id:int}/enable")
async def v2_ads_experiment_enable(request: Request, experiment_id: int):
    """ENABLE a launched experiment's adgroups so they start delivering."""
    return _ads_call_and_redirect(experiment_id, "enable_experiment")


@router.post("/ads/experiments/{experiment_id:int}/kill")
async def v2_ads_experiment_kill(request: Request, experiment_id: int):
    return _ads_call_and_redirect(experiment_id, "kill_experiment")


@router.get("/ads/audiences", response_class=HTMLResponse)
def v2_ads_audiences(request: Request, pack_id: int | None = None):
    """Audience leaderboard ordered by roas desc; ?pack_id= filters; winning
    rows are visually highlighted."""
    rows: list[dict[str, Any]] = []
    hint = ""
    try:
        from src.services.tiktok.ads_experiment_service import (
            get_ads_experiment_service,
        )
    except Exception as e:
        logger.warning("ads_experiment_service import failed: %s", e)
        hint = "广告实验服务尚未就绪（后端开发中）。"
    else:
        try:
            svc = get_ads_experiment_service()
            rows = list(svc.audience_leaderboard(pack_id=pack_id) or [])
        except Exception as e:
            logger.warning("audience_leaderboard failed: %s", e)
            hint = f"无法加载人群库：{str(e)[:140]}"

    # The service already orders rows by each objective's KPI direction
    # (e.g. ROAS desc for PRODUCT_SALES, CPV asc for VIDEO_VIEWS). Only re-sort
    # defensively when NO row carries an objective (pre-Phase-2 service), where
    # roas-desc is the right generic order.
    if rows and not any(r.get("objective") for r in rows):
        def _roas(r: dict[str, Any]) -> float:
            try:
                return float(r.get("roas") or 0)
            except (TypeError, ValueError):
                return 0.0

        rows = sorted(rows, key=_roas, reverse=True)

    # Pack filter dropdown options.
    conn = _open_db()
    try:
        packs = _ads_pack_candidates(conn)
    finally:
        conn.close()

    return templates.TemplateResponse(
        "v2_ads_audiences.html",
        {
            "request": request,
            "page_title": "优质人群库",
            "audiences": rows,
            "packs": packs,
            "pack_id_filter": pack_id,
            "hint": hint,
        },
    )


# ======================================================================
# Amazon 上架(卡贴包线)—— 列表 / 专属预览编辑页 / 文案·图片·推送
# 内容侧:文案(两步 AI)+ 图片(本地→COS)+ 推送(中间层 store=main)。
# ======================================================================

@router.get("/amazon/packs", response_class=HTMLResponse)
def v2_amazon_packs_list(request: Request):
    """卡贴包 Amazon 列表:每个本地产品一行 + Amazon 上架状态。"""
    rows = get_amazon_service().list_all()
    return templates.TemplateResponse(
        "v2_amazon_packs_list.html",
        {
            "request": request,
            "page_title": "Amazon · 卡贴包 Listing",
            "rows": rows,
            "summary": (request.query_params.get("summary") or "").strip(),
        },
    )


@router.get("/amazon/packs/{local_product_id:int}", response_class=HTMLResponse)
def v2_amazon_pack_detail(request: Request, local_product_id: int):
    """专属预览/编辑页:文案(可编辑)/ 图片图集 / 推送。首访自动生成文案。"""
    pv = get_amazon_service().build_preview(local_product_id)
    if not pv.get("ok"):
        raise HTTPException(status_code=404, detail=pv.get("error", "not found"))
    return templates.TemplateResponse(
        "v2_amazon_pack_detail.html",
        {
            "request": request,
            "page_title": f"Amazon Listing · 本地 #{local_product_id}",
            "pv": pv,
            "back_url": "/v2/amazon/packs",
            "summary": (request.query_params.get("summary") or "").strip(),
        },
    )


def _amazon_back(local_product_id: int, msg: str) -> RedirectResponse:
    sep = "?"
    url = f"/v2/amazon/packs/{local_product_id}"
    return RedirectResponse(url=f"{url}{sep}summary={msg}", status_code=303)


@router.post("/amazon/packs/{local_product_id:int}/regenerate")
async def v2_amazon_regenerate(request: Request, local_product_id: int):
    """重新生成 Amazon 文案(同步,一次 AI),回详情页。"""
    copy = get_amazon_service().regenerate_content(local_product_id)
    msg = "文案已重新生成" if copy else "生成失败:本地产品不存在"
    return _amazon_back(local_product_id, msg)


@router.post("/amazon/packs/{local_product_id:int}/set-price")
async def v2_amazon_set_price(request: Request, local_product_id: int):
    form = await request.form()
    raw = (form.get("price") or "").strip()
    ok = get_amazon_service().set_price(local_product_id, raw)
    return _amazon_back(local_product_id, f"价格已设为 ${raw}" if ok else "价格非法")


@router.post("/amazon/packs/{local_product_id:int}/upload-images")
async def v2_amazon_upload_images(request: Request, local_product_id: int):
    """把 master 图片上传到 COS(拿公网 URL 供 Amazon 抓取)。"""
    res = get_amazon_service().upload_images_from_master(local_product_id)
    if res.get("ok"):
        msg = f"已上传 {len(res.get('uploaded', []))} 张图到 COS"
        if res.get("errors"):
            msg += f"({len(res['errors'])} 张失败)"
    else:
        msg = "图片上传失败:" + str(res.get("error") or res.get("errors"))[:120]
    return _amazon_back(local_product_id, msg)


@router.post("/amazon/packs/{local_product_id:int}/validate")
async def v2_amazon_validate(request: Request, local_product_id: int):
    """干跑校验(VALIDATION_PREVIEW,零写入)。"""
    res = amazon_sync.push_one(local_product_id, dry_run=True)
    if res.get("ok"):
        msg = "校验通过(VALID/ACCEPTED,可推送)"
    elif res.get("problems"):
        msg = "未就绪:" + "、".join(res["problems"])
    else:
        msg = "校验未过:" + str(res.get("error") or [i.get("message") for i in res.get("issues", [])])[:160]
    return _amazon_back(local_product_id, msg)


@router.post("/amazon/packs/{local_product_id:int}/push")
async def v2_amazon_push(request: Request, local_product_id: int):
    """真实创建草稿(先 GET 防覆盖)。默认库存 0=不可售草稿;勾选 publish 才带库存。"""
    form = await request.form()
    publish = (form.get("publish") or "").strip() in ("1", "true", "on")
    qty = 10 if publish else 0
    res = amazon_sync.push_one(local_product_id, dry_run=False, quantity=qty)
    if res.get("ok"):
        msg = f"已推送:status={res.get('status')}" + (f" submissionId={res.get('submission_id')}" if res.get("submission_id") else "")
    elif res.get("problems"):
        msg = "未就绪:" + "、".join(res["problems"])
    else:
        msg = "推送失败:" + str(res.get("error") or "")[:160]
    return _amazon_back(local_product_id, msg)


@router.post("/amazon/packs/{local_product_id:int}/generate-images")
async def v2_amazon_generate_images_fixed(request: Request, local_product_id: int):
    """一键生成亚马逊固定样式主副图(main 白底合规 + 4 副图),AI+COS,
    覆盖 amazon_images。长任务(5 张 AI 图),后台跑;沿用 one-click-status 轮询
    看不到图片进度,前端用 images-status 轮询。"""
    task_id = f"amazon_gen_images:{local_product_id}"
    try:
        started = run_async(
            task_id,
            get_amazon_service().regenerate_images_fixed, local_product_id,
            label=f"Amazon 固定样式主副图 (master #{local_product_id}, 5 张)",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({
        "ok": True, "task_id": task_id, "started": bool(started),
        "message": ("已在后台启动:固定样式 5 张(主图白底合规),完成后自动刷新"
                    if started else "任务已在后台运行中"),
    })


@router.get("/amazon/packs/{local_product_id:int}/images-status")
def v2_amazon_images_status(local_product_id: int):
    """固定样式主副图生成进度:{running, images(含 cos_url)}。"""
    svc = get_amazon_service()
    return JSONResponse({
        "ok": True,
        "running": is_running(f"amazon_gen_images:{local_product_id}"),
        "images": svc.get_images(local_product_id),
    })


@router.post("/amazon/packs/{local_product_id:int}/one-click")
async def v2_amazon_one_click(request: Request, local_product_id: int):
    """一键:建 listing(防覆盖)→ 轮询 ASIN → 固定样式 A+ 横幅(AI+COS)
    → 建 A+ 文档 + 绑 ASIN + 提审。长任务(ASIN 轮询 + 3 张 AI 图),后台跑,
    前端轮询 one-click-status。幂等:重跑会跳过已完成的步骤续传。"""
    form = await request.form()
    publish = (form.get("publish") or "").strip() in ("1", "true", "on")
    task_id = f"amazon_one_click:{local_product_id}"
    try:
        started = run_async(
            task_id,
            amazon_sync.one_click_amazon, local_product_id,
            publish=publish,
            label=f"Amazon 一键上传+A+ (master #{local_product_id})",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({
        "ok": True, "task_id": task_id, "started": bool(started),
        "message": ("已在后台启动:listing → ASIN → A+ 横幅 → 绑定提审(约 3-8 分钟)"
                    if started else "任务已在后台运行中"),
    })


@router.get("/amazon/packs/{local_product_id:int}/one-click-status")
def v2_amazon_one_click_status(local_product_id: int):
    """一键任务进度:running + DB 里的 listing / A+ 状态(管线边跑边回写)。"""
    svc = get_amazon_service()
    listing = svc.get_or_create(local_product_id)
    aplus_row = svc.get_aplus(local_product_id)
    return JSONResponse({
        "ok": True,
        "running": is_running(f"amazon_one_click:{local_product_id}"),
        "listing": {
            "sync_status": listing.get("sync_status"),
            "status": listing.get("status"),
            "asin": listing.get("asin") or "",
            "last_error": listing.get("last_error") or "",
        },
        "aplus": {
            "status": (aplus_row or {}).get("status") or "",
            "content_ref_key": (aplus_row or {}).get("content_ref_key") or "",
            "last_error": (aplus_row or {}).get("last_error") or "",
        },
    })


# ---- Amazon A+ 内容(展示 + AI 制作)----

@router.get("/amazon/packs/{local_product_id:int}/aplus", response_class=HTMLResponse)
def v2_amazon_aplus(request: Request, local_product_id: int):
    """A+ 内容专属页:可视化预览模块化图文,可 AI 生成/重生成。"""
    pv = get_amazon_service().build_aplus_preview(local_product_id)
    if not pv.get("ok"):
        raise HTTPException(status_code=404, detail=pv.get("error", "not found"))
    return templates.TemplateResponse(
        "v2_amazon_aplus.html",
        {
            "request": request,
            "page_title": f"Amazon A+ · 本地 #{local_product_id}",
            "pv": pv,
            "back_url": f"/v2/amazon/packs/{local_product_id}",
            "summary": (request.query_params.get("summary") or "").strip(),
        },
    )


@router.post("/amazon/packs/{local_product_id:int}/aplus/regenerate")
async def v2_amazon_aplus_regenerate(request: Request, local_product_id: int):
    """AI 生成 / 重新生成 A+ 模块(同步,一次 AI),回 A+ 页。"""
    mods = get_amazon_service().regenerate_aplus(local_product_id)
    n = len((mods or {}).get("modules", []))
    msg = f"A+ 已生成 {n} 个模块" if n else "生成失败:本地产品不存在"
    return RedirectResponse(url=f"/v2/amazon/packs/{local_product_id}/aplus?summary={msg}", status_code=303)


# ======================================================================
# 半定制产品(独立模块)—— 纯前端布局预览(假数据,后端后续接)
# ======================================================================

_AMZ_MOCK_IMG = "https://amazon-1303982902.cos.accelerate.myqcloud.com/amazon/test/main_test_1500.png"

_AMZ_CUSTOM_PRODUCTS = [
    {"id": 1, "name": "Custom Pet Stickers", "brand": "Inkelligent",
     "variants": 8, "aplus": "共享 3 + 专属 1", "price": "7.99", "status": "draft"},
    {"id": 2, "name": "Wedding Favor Labels", "brand": "Inkelligent",
     "variants": 12, "aplus": "共享 3", "price": "9.99", "status": "pushed"},
    {"id": 3, "name": "Business Logo Stickers", "brand": "Inkelligent",
     "variants": 6, "aplus": "共享 2 + 专属 2", "price": "12.99", "status": "draft"},
]

_AMZ_CUSTOM_DETAIL = {
    "id": 1, "name": "Custom Pet Stickers", "brand": "Inkelligent",
    "variation_theme": "color_name", "price": "7.99", "status": "draft",
    "img": _AMZ_MOCK_IMG,
    "progress": {"copy": True, "images": True, "aplus": "共享3+专属1", "variants": 8},
    "variants": [
        {"name": "Magical Cats", "sku": "INK-MAGICALCATS-1", "price": "7.99"},
        {"name": "Dark Academia", "sku": "INK-DARKACADEMIA-2", "price": "7.99"},
        {"name": "Cottagecore", "sku": "INK-COTTAGECORE-3", "price": "7.99"},
        {"name": "Y2K Retro", "sku": "INK-Y2KRETRO-4", "price": "8.99"},
        {"name": "Floral", "sku": "INK-FLORAL-5", "price": "7.99"},
        {"name": "Space Galaxy", "sku": "INK-SPACE-6", "price": "8.99"},
    ],
    "copy": {
        "item_name": "Inkelligent Custom Pet Stickers - 8 Designs Waterproof Vinyl Decals for Laptop Water Bottle Journal",
        "bullets": [
            "PREMIUM MATTE VINYL: waterproof, fade-resistant pet sticker designs",
            "8 DESIGN STYLES: choose your favorite from cats to galaxy themes",
            "PERFECT FOR: laptops, water bottles, notebooks, planners and phone cases",
            "EASY TO APPLY: peel and stick on any clean smooth surface",
            "GREAT GIFT: a fun present for pet lovers and students",
        ],
        "description": "Decorate everything with this customizable pet sticker series. Each design is printed on durable waterproof matte vinyl.",
        "search_terms": "pet stickers vinyl waterproof laptop water bottle cat dog aesthetic decals",
    },
    "aplus_modules": [
        {"title": "品牌横幅", "type": "header", "scope": "shared"},
        {"title": "材质对比表", "type": "comparison", "scope": "shared"},
        {"title": "本品使用场景", "type": "three_image_text", "scope": "specific"},
    ],
}

_AMZ_SHARED_LIB = [
    {"name": "品牌横幅", "type": "header", "used": 12, "checked": True},
    {"name": "材质对比表", "type": "comparison", "used": 12, "checked": True},
    {"name": "防水耐久说明", "type": "single_image_text", "used": 5, "checked": False},
    {"name": "使用场景三卡", "type": "three_image_text", "used": 9, "checked": False},
    {"name": "收尾 CTA", "type": "closing", "used": 7, "checked": False},
]


@router.get("/amazon/custom", response_class=HTMLResponse)
def v2_amazon_custom_list(request: Request):
    case = _load_amazon_case()
    products = [dict(x) for x in _AMZ_CUSTOM_PRODUCTS]
    # 第 1 个产品 = 真实案例(B0H1R7RVW8)
    products[0] = {
        "id": 1, "name": case.get("name", products[0]["name"]),
        "brand": case.get("brand", "Inkelligent"),
        "variants": len(case.get("variants", [])) or products[0]["variants"],
        "aplus": "共享 3 + 专属 1", "price": case.get("price", "8.63"),
        "status": "draft", "cover": case.get("img", _AMZ_MOCK_IMG),
        "is_case": True,
    }
    teddy = _load_teddy_case()
    if teddy and teddy.get("themes"):
        n_design = len(teddy["themes"]); n_qty = len(teddy.get("dimensions", {}).get("quantity", []))
        products.insert(1, {
            "id": 2, "name": teddy.get("title", "Kids Name Labels"),
            "brand": teddy.get("brand", "Inkelligent Kids"),
            "variants": n_design * (n_qty or 1),
            "aplus": "%d 主题各自 A+" % n_design, "price": teddy.get("price", "12.99"),
            "status": "draft", "cover": teddy["themes"][0].get("thumb", _AMZ_MOCK_IMG),
            "is_case": True,
        })
    return templates.TemplateResponse(
        "v2_amazon_custom_list.html",
        {"request": request, "page_title": "半定制产品",
         "products": products, "mock_img": _AMZ_MOCK_IMG},
    )


def _load_amazon_case() -> dict:
    """真实案例(B0H1R7RVW8 抓取)→ 详情页 mock。文件缺失时回退内置 mock。"""
    fp = Path(__file__).resolve().parent / "_amazon_case.json"
    try:
        case = _json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return _AMZ_CUSTOM_DETAIL
    variants = []
    for v in case.get("variants", []):
        variants.append({"name": v.get("name", ""), "sku": v.get("sku", ""),
                         "price": case.get("price", ""), "img": v.get("img", "")})
    return {
        "id": case.get("id", 1),
        "name": case.get("title", ""),
        "brand": case.get("brand", "Inkelligent"),
        "variation_theme": case.get("variation_theme", "color_name"),
        "price": case.get("price", ""),
        "status": case.get("status", "draft"),
        "img": (case.get("main_images") or [_AMZ_MOCK_IMG])[0],
        "main_images": case.get("main_images", []),
        "source_url": case.get("source_url", ""),
        "progress": {"copy": True, "images": True,
                     "aplus": "共享 3 + 专属 1", "variants": len(variants)},
        "variants": variants,
        "copy": {
            "item_name": case.get("title", ""),
            "bullets": case.get("bullets", []),
            "description": case.get("description", "") or "(竞品详情页无纯文本描述,内容在 A+ 中)",
            "search_terms": "custom stickers logo labels waterproof vinyl business wedding",
        },
        "aplus_modules": _AMZ_CUSTOM_DETAIL["aplus_modules"],
    }


def _load_teddy_case():
    """TeddyLabels 风格 listing(本地 amazon4 素材构建)。缺失返回 None。"""
    fp = Path(__file__).resolve().parent / "_amazon_case_teddy.json"
    try:
        return _json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.get("/amazon/custom/{product_id:int}", response_class=HTMLResponse)
def v2_amazon_custom_detail(request: Request, product_id: int):
    if product_id == 2:
        teddy = _load_teddy_case()
        if teddy:
            return templates.TemplateResponse(
                "v2_amazon_listing_teddy.html",
                {"request": request, "page_title": teddy.get("title", "半定制 listing"),
                 "p": teddy},
            )
    return templates.TemplateResponse(
        "v2_amazon_custom_detail.html",
        {"request": request, "page_title": "半定制产品详情",
         "p": _load_amazon_case(), "shared_lib": _AMZ_SHARED_LIB},
    )


@router.get("/amazon/aplus-library", response_class=HTMLResponse)
def v2_amazon_aplus_library(request: Request):
    return templates.TemplateResponse(
        "v2_amazon_aplus_library.html",
        {"request": request, "page_title": "A+ 共享库", "modules": _AMZ_SHARED_LIB},
    )
