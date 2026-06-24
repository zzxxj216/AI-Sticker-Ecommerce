"""Shopify product sync service.

Turns a local product master (assembled by the tkshop service) into a Shopify
product and syncs it to the platform via the middle-layer HTTP API
(``multi-channel-api`` at ``TKSHOP_SERVER_URL``).

Three jobs:

1. ``build_preview`` — NO network. Produces the full preview payload a local
   style-preview page renders (content, SEO, body_html, variant + image
   metadata, sync tracking). Calls the (guarded) copy generator.
2. ``sync_one`` / ``batch_sync`` — POST a ShopifyProductCreate body to the
   middle layer, unwrap the ``{success, message, data}`` envelope, and track
   the result in the ``shopify_products`` table.
3. ``batch_set_local_default_price`` — bulk-set ``local_products.default_price``.

Safety: status always defaults to ``draft`` (never auto-activate). UI-facing
methods return ``{ok: False, error: ...}`` instead of raising (except
``_build_payload``). Logging is ASCII-only (this Windows host's console is GBK).
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import requests

from src.core.logger import get_logger

logger = get_logger("service.shopify.sync")


# ---------------------------------------------------------------------------
# Config (env-driven; mirrors tkshop service)
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = "data/ops_workbench.db"

# Same middle-layer FastAPI service the tkshop publish path talks to.
TKSHOP_SERVER_URL = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000")
TKSHOP_SERVER_TIMEOUT = int(os.getenv("TKSHOP_SERVER_TIMEOUT", "300"))

# Brand constants for the Shopify product.
SHOPIFY_VENDOR = "Inkelligent"
SHOPIFY_PRODUCT_TYPE = "Stickers"
SHOPIFY_VARIANT_WEIGHT_G = 30
SHOPIFY_DEFAULT_QUANTITY = 16
SHOPIFY_MAX_IMAGES = 8


# ---------------------------------------------------------------------------
# DB helper (mirrors tkshop service._open_db; intentionally decoupled)
# ---------------------------------------------------------------------------

def _open_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ShopifyProductSync:
    """Local-product -> Shopify product sync via the middle layer."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path

    # -- internal data fetch ------------------------------------------------

    def _fetch_commerce_fields(self, local_product_id: int) -> Optional[dict]:
        """Pull the price/quantity/sticker-count fields directly.

        ``get_local_product`` already joins these (``lp.*`` + ``p.total_stickers``)
        but we query them ourselves so the service stays robust to that shape
        and so we can read them without re-running the heavier assembly when we
        only need the numbers. Returns None if the master row is missing.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT lp.id, lp.seller_sku, lp.title, lp.description_html,
                       lp.default_price, lp.default_quantity,
                       p.total_stickers
                  FROM local_products lp
                  LEFT JOIN packs p ON p.id = lp.pack_id
                 WHERE lp.id = ?
                """,
                (local_product_id,),
            ).fetchone()
        return dict(row) if row else None

    # -- preview (no network) ----------------------------------------------

    # -- generated-content cache -------------------------------------------

    def _load_cached_content(self, local_product_id: int) -> Optional[dict]:
        """Return the cached AI content dict, or None if not generated yet."""
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT shopify_content_json FROM local_products WHERE id = ?",
                (local_product_id,),
            ).fetchone()
        if not row or not (row["shopify_content_json"] or "").strip():
            return None
        try:
            data = json.loads(row["shopify_content_json"])
            return data if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None

    def _save_cached_content(self, local_product_id: int, content: dict) -> None:
        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE local_products SET shopify_content_json = ? WHERE id = ?",
                (json.dumps(content, ensure_ascii=False), local_product_id),
            )
            conn.commit()

    def regenerate_content(self, local_product_id: int) -> dict:
        """Force a fresh AI generation and persist it. Returns {ok, ...}.

        Use this from the "重新生成文案" action; ``build_preview`` then reads the
        cached result so the page loads instantly with stable content.
        """
        preview = self.build_preview(local_product_id, force_regen=True)
        return {"ok": preview.get("ok", False),
                "error": preview.get("error"),
                "local_product_id": local_product_id}

    def build_preview(self, local_product_id: int, *, force_regen: bool = False) -> dict:
        """Assemble the full preview payload for the style-preview page.

        AI content is CACHED on ``local_products.shopify_content_json``: the
        first call (or ``force_regen=True``) generates it (one slow AI pass) and
        persists it; later calls read the cache so the page is instant and the
        content is stable across reloads. ``body_html`` is rebuilt from the
        cached content on every call (a fast pure function).
        On a missing master, returns a minimal ``{ok: False, error: ...}`` dict.
        """
        # Lazy import so this module imports even if tkshop has heavy deps.
        try:
            from src.services.tkshop.service import get_tkshop_service
            local_product = get_tkshop_service().get_local_product(local_product_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("build_preview: get_local_product failed for #%s: %s",
                           local_product_id, str(e)[:200])
            local_product = None

        if not local_product:
            return {
                "ok": False,
                "error": "本地产品不存在",
                "local_product_id": local_product_id,
            }

        # Commerce numbers (price / quantity / total_stickers). Prefer values
        # already on the assembled product; backfill from a direct query.
        commerce = self._fetch_commerce_fields(local_product_id) or {}

        def _pick(key: str) -> Any:
            val = local_product.get(key)
            if val is None:
                val = commerce.get(key)
            return val

        title = (local_product.get("title") or "").strip()
        sku = (local_product.get("seller_sku") or commerce.get("seller_sku") or "").strip()

        raw_price = _pick("default_price")
        price: Optional[float]
        try:
            price = float(raw_price) if raw_price is not None else None
        except (TypeError, ValueError):
            price = None

        raw_qty = _pick("default_quantity")
        try:
            quantity = int(raw_qty) if raw_qty is not None else SHOPIFY_DEFAULT_QUANTITY
        except (TypeError, ValueError):
            quantity = SHOPIFY_DEFAULT_QUANTITY

        keywords = local_product.get("keywords")
        if not isinstance(keywords, list):
            keywords = []
        tags = [str(k).strip() for k in keywords if str(k).strip()]

        # Make total_stickers available to the copy generator's helpers.
        if local_product.get("total_stickers") is None and commerce.get("total_stickers") is not None:
            local_product = dict(local_product)
            local_product["total_stickers"] = commerce.get("total_stickers")

        # Copy generation with caching. Use the cached content unless a regen
        # is forced or nothing is cached yet — generating is a slow AI pass.
        content: dict
        body_html: str
        seo: dict
        cached = None if force_regen else self._load_cached_content(local_product_id)
        try:
            from src.services.shopify.copy_generator import (
                generate_shopify_content,
                build_body_html,
            )
            if cached is not None:
                content = cached
            else:
                content = generate_shopify_content(local_product)
                # Persist so subsequent loads are instant + stable.
                try:
                    self._save_cached_content(local_product_id, content)
                except Exception as e:  # noqa: BLE001
                    logger.warning("build_preview: cache save failed for #%s: %s",
                                   local_product_id, str(e)[:150])
            body_html = build_body_html(local_product, content)
        except Exception as e:  # noqa: BLE001
            logger.warning("build_preview: copy generation failed for #%s: %s; "
                           "falling back to local fields", local_product_id, str(e)[:200])
            content = cached or {}
            body_html = (local_product.get("description_html") or "").strip()

        content = content or {}
        seo = {
            "title": (content.get("seo_title") or title or "").strip(),
            "description": (content.get("seo_description") or "").strip(),
            "handle": (content.get("handle") or "").strip(),
        }

        preview_content = {
            "hero_tagline": content.get("hero_tagline") or "",
            "hero_intro": content.get("hero_intro") or "",
            "whats_included": content.get("whats_included") or "",
            "bullets": content.get("bullets") or [],
            "how_to_use": content.get("how_to_use") or [],
        }

        # Images (metadata only; route adds 'url'). Cap to keep payload sane.
        images: list[dict] = []
        for img in (local_product.get("images") or []):
            lp = (img.get("local_path") or "").strip()
            if not lp:
                continue
            images.append({
                "local_path": lp,
                "role": img.get("role") or "",
                "source": img.get("source") or "",
            })
            if len(images) >= SHOPIFY_MAX_IMAGES:
                break

        sync = self.get_sync_status(local_product_id)
        sync_summary = None
        if sync:
            sync_summary = {
                "sync_status": sync.get("sync_status"),
                "shopify_product_id": sync.get("shopify_product_id"),
                "handle": sync.get("handle"),
                "synced_at": sync.get("synced_at"),
            }

        return {
            "ok": True,
            "error": None,
            "local_product_id": local_product_id,
            "title": title,
            "vendor": SHOPIFY_VENDOR,
            "product_type": SHOPIFY_PRODUCT_TYPE,
            "status": "draft",
            "price": price,
            "sku": sku,
            "quantity": quantity,
            "weight_g": SHOPIFY_VARIANT_WEIGHT_G,
            "tags": tags,
            "content": preview_content,
            "seo": seo,
            "body_html": body_html,
            "images": images,
            "sync": sync_summary,
        }

    # -- payload assembly ---------------------------------------------------

    def _build_payload(
        self,
        local_product_id: int,
        *,
        price: float,
        status: str = "draft",
    ) -> dict:
        """Build a ShopifyProductCreate dict, with base64-encoded images.

        Reuses ``build_preview`` for the content/title/tags, then adds the
        variant and base64 images. Raises ``ValueError`` if ``price`` is None.
        """
        if price is None:
            raise ValueError("price is required to build a Shopify payload")

        preview = self.build_preview(local_product_id)
        if not preview.get("ok"):
            raise ValueError(preview.get("error") or "本地产品不存在")

        title = preview.get("title") or ""
        sku = preview.get("sku") or ""
        quantity = int(preview.get("quantity") or SHOPIFY_DEFAULT_QUANTITY)
        tags = preview.get("tags") or []
        body_html = preview.get("body_html") or ""

        variant = {
            "price": f"{float(price):.2f}",
            "sku": sku,
            "inventory_quantity": quantity,
            "weight": SHOPIFY_VARIANT_WEIGHT_G,
            "weight_unit": "g",
            "requires_shipping": True,
            "taxable": True,
        }

        images: list[dict] = []
        position = 1
        for img in preview.get("images") or []:
            local_path = (img.get("local_path") or "").strip()
            if not local_path:
                continue
            try:
                raw = Path(local_path).read_bytes()
            except OSError as e:
                logger.warning("_build_payload: skip missing image %s: %s",
                               local_path, str(e)[:150])
                continue
            attachment = base64.b64encode(raw).decode("ascii")
            images.append({
                "attachment": attachment,
                "alt": title,
                "position": position,
            })
            position += 1
            if len(images) >= SHOPIFY_MAX_IMAGES:
                break

        return {
            "title": title,
            "body_html": body_html,
            "vendor": SHOPIFY_VENDOR,
            "product_type": SHOPIFY_PRODUCT_TYPE,
            "tags": ",".join(tags),
            "status": status or "draft",
            "variants": [variant],
            "images": images,
        }

    # -- tracking table -----------------------------------------------------

    def get_sync_status(self, local_product_id: int) -> Optional[dict]:
        """Return the ``shopify_products`` tracking row as a dict, or None."""
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM shopify_products WHERE local_product_id = ?",
                (local_product_id,),
            ).fetchone()
        return dict(row) if row else None

    def _upsert_syncing(self, local_product_id: int, sku: str) -> None:
        """Mark the tracking row as 'syncing' (insert if absent)."""
        now = int(time.time())
        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO shopify_products
                    (local_product_id, seller_sku, sync_status, last_error, created_at)
                VALUES (?, ?, 'syncing', '', ?)
                ON CONFLICT(local_product_id) DO UPDATE SET
                    sync_status = 'syncing',
                    last_error  = '',
                    seller_sku  = excluded.seller_sku
                """,
                (local_product_id, sku or "", now),
            )
            conn.commit()

    def _mark_synced(
        self,
        local_product_id: int,
        *,
        shopify_product_id: str,
        handle: str,
        status: str,
    ) -> None:
        now = int(time.time())
        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE shopify_products
                   SET shopify_product_id = ?,
                       handle             = ?,
                       status             = ?,
                       sync_status        = 'synced',
                       last_error         = '',
                       synced_at          = ?
                 WHERE local_product_id = ?
                """,
                (shopify_product_id or "", handle or "", status or "draft",
                 now, local_product_id),
            )
            conn.commit()

    def _mark_failed(self, local_product_id: int, error: str) -> None:
        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE shopify_products
                   SET sync_status = 'failed',
                       last_error  = ?
                 WHERE local_product_id = ?
                """,
                (str(error or "")[:500], local_product_id),
            )
            conn.commit()

    # -- sync (network) -----------------------------------------------------

    def sync_one(self, local_product_id: int, *, status: str = "draft") -> dict:
        """Sync one local product to Shopify via the middle layer.

        Returns ``{ok, ...}``. Returns ``{ok: False, error: '未设置价格'}`` (no
        raise) when ``default_price`` is missing. Handles request exceptions,
        non-2xx responses, and ``success=false`` envelopes by recording a
        failed tracking row.
        """
        commerce = self._fetch_commerce_fields(local_product_id)
        if not commerce:
            return {"ok": False, "error": "本地产品不存在",
                    "local_product_id": local_product_id}

        raw_price = commerce.get("default_price")
        if raw_price is None:
            return {"ok": False, "error": "未设置价格",
                    "local_product_id": local_product_id}
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            return {"ok": False, "error": "未设置价格",
                    "local_product_id": local_product_id}

        sku = (commerce.get("seller_sku") or "").strip()

        # Build the payload before flipping the row to 'syncing' so a payload
        # error doesn't leave a stuck 'syncing' state.
        try:
            payload = self._build_payload(local_product_id, price=price, status=status)
        except ValueError as e:
            return {"ok": False, "error": str(e),
                    "local_product_id": local_product_id}

        self._upsert_syncing(local_product_id, sku)

        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/shopify/products"
        try:
            resp = requests.post(endpoint, json=payload, timeout=TKSHOP_SERVER_TIMEOUT)
        except requests.RequestException as e:
            err = f"NETWORK: {str(e)[:300]}"
            self._mark_failed(local_product_id, err)
            logger.warning("sync_one #%s network error: %s",
                           local_product_id, str(e)[:200])
            return {"ok": False, "error": err, "local_product_id": local_product_id}

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"_raw_text": resp.text[:500]}

        # Middle-layer envelope: {success, message, data:{id, handle, ...}}.
        if not (200 <= resp.status_code < 300):
            msg = ""
            if isinstance(resp_json, dict):
                msg = str(resp_json.get("message") or resp_json.get("_raw_text") or "")
            err = f"HTTP {resp.status_code}: {msg or resp.text[:200]}"
            self._mark_failed(local_product_id, err)
            logger.warning("sync_one #%s http %s", local_product_id, resp.status_code)
            return {"ok": False, "error": err, "local_product_id": local_product_id}

        success = bool(isinstance(resp_json, dict) and resp_json.get("success") is True)
        if not success:
            msg = ""
            if isinstance(resp_json, dict):
                msg = str(resp_json.get("message") or resp_json.get("_raw_text") or "")
            err = msg or "middle layer returned success=false"
            self._mark_failed(local_product_id, err)
            logger.warning("sync_one #%s failed: %s", local_product_id, err[:200])
            return {"ok": False, "error": err, "local_product_id": local_product_id}

        data = resp_json.get("data") if isinstance(resp_json.get("data"), dict) else {}
        shopify_product_id = str(data.get("id") or "")
        handle = str(data.get("handle") or "")

        self._mark_synced(
            local_product_id,
            shopify_product_id=shopify_product_id,
            handle=handle,
            status=status or "draft",
        )
        logger.info("sync_one #%s synced product_id=%s", local_product_id,
                    shopify_product_id or "?")
        return {
            "ok": True,
            "error": None,
            "local_product_id": local_product_id,
            "shopify_product_id": shopify_product_id,
            "handle": handle,
            "status": status or "draft",
        }

    def batch_sync(self, local_product_ids: list[int], *, status: str = "draft") -> dict:
        """Sync many products. Skips ids with no price; never raises."""
        done: list[dict] = []
        skipped: list[dict] = []
        failed: list[dict] = []

        for lp_id in local_product_ids or []:
            try:
                result = self.sync_one(lp_id, status=status)
            except Exception as e:  # noqa: BLE001 — defensive; keep the loop alive.
                logger.warning("batch_sync #%s unexpected error: %s",
                               lp_id, str(e)[:200])
                failed.append({"local_product_id": lp_id,
                               "error": f"INTERNAL: {str(e)[:200]}"})
                continue

            if result.get("ok"):
                done.append(result)
            elif result.get("error") == "未设置价格":
                skipped.append(result)
            else:
                failed.append(result)

        return {
            "ok": len(failed) == 0,
            "done": done,
            "skipped": skipped,
            "failed": failed,
        }

    # -- price setter -------------------------------------------------------

    def batch_set_local_default_price(
        self,
        local_product_ids: list[int],
        price: float,
    ) -> int:
        """Set ``local_products.default_price`` for the given ids.

        Bumps ``updated_at``. Returns the number of rows updated. Returns 0 for
        an empty id list or a negative price (does not raise).
        """
        if not local_product_ids:
            return 0
        try:
            price_val = float(price)
        except (TypeError, ValueError):
            return 0
        if price_val < 0:
            return 0

        ids = [int(i) for i in local_product_ids]
        now = int(time.time())
        placeholders = ",".join(["?"] * len(ids))
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                f"""
                UPDATE local_products
                   SET default_price = ?,
                       updated_at    = ?
                 WHERE id IN ({placeholders})
                """,
                tuple([price_val, now] + ids),
            )
            conn.commit()
            return cur.rowcount


# ---------------------------------------------------------------------------
# Singleton accessor (mirrors get_tkshop_service)
# ---------------------------------------------------------------------------

_svc: Optional[ShopifyProductSync] = None


def get_shopify_sync() -> ShopifyProductSync:
    global _svc
    if _svc is None:
        _svc = ShopifyProductSync()
    return _svc
