"""Lab: multi-SKU sticker product service.

Self-contained — does NOT touch tkshop_products / tkshop_product_skus
(no such legacy table) / pack_store / publish flow. Reads from packs
& hot_topics for source data; writes to lab_msku_products /
lab_msku_product_skus only.

Publish flow: POSTs to multi-channel-api's NEW
``/api/v1/tiktok/products/sticker_multi_sku_publish`` endpoint. Update
goes to ``/sticker_multi_sku_update``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import requests

from src.core.logger import get_logger

logger = get_logger("service.lab_multi_sku")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

# Reuse the same env var as the legacy single-SKU flow.
TKSHOP_SERVER_URL = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000")
TKSHOP_SERVER_TIMEOUT = int(os.getenv("TKSHOP_SERVER_TIMEOUT", "300"))


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> int:
    return int(time.time())


_TITLE_MAX = 255


def _clamp_title(title: str) -> str:
    t = (title or "").strip()
    if len(t) <= _TITLE_MAX:
        return t
    return t[:_TITLE_MAX].rstrip(" |,-—·•")


_SKU_VALID_RE = re.compile(r"^[A-Z0-9._-]{1,50}$")


def _slug(s: str, max_len: int = 10) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()[:max_len]


def make_seller_sku(*, prefix: str = "INK", topic_slug: str, pack_slug: str) -> str:
    parts = [prefix, _slug(topic_slug, 8), _slug(pack_slug, 10)]
    return "-".join(p for p in parts if p)[:50]


class LabMultiSkuService:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    # ------------------------------------------------------------------
    # CRUD: products
    # ------------------------------------------------------------------

    def list_products(self, *, limit: int = 100) -> list[dict]:
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """SELECT p.*,
                          (SELECT COUNT(*) FROM lab_msku_product_skus s WHERE s.product_id = p.id) AS sku_count,
                          ht.topic_name AS topic_name
                     FROM lab_msku_products p
                LEFT JOIN hot_topics ht ON ht.id = p.topic_id
                    ORDER BY p.id DESC
                    LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_product(self, product_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                """SELECT p.*, ht.topic_name AS topic_name
                     FROM lab_msku_products p
                LEFT JOIN hot_topics ht ON ht.id = p.topic_id
                    WHERE p.id = ?""",
                (product_id,),
            ).fetchone()
            if not r:
                return None
            d = dict(r)
            try:
                d["selling_points"] = json.loads(d.get("selling_points") or "[]")
            except Exception:
                d["selling_points"] = []
            try:
                d["keywords"] = json.loads(d.get("keywords") or "[]")
            except Exception:
                d["keywords"] = []
            sks = conn.execute(
                """SELECT s.*, p2.display_name AS pack_name, p2.pack_uid AS pack_uid,
                          p2.cover_image_path AS pack_cover
                     FROM lab_msku_product_skus s
                LEFT JOIN packs p2 ON p2.id = s.pack_id
                    WHERE s.product_id = ?
                    ORDER BY s.sort_order, s.id""",
                (product_id,),
            ).fetchall()
            d["skus"] = [dict(r) for r in sks]
        return d

    def create_from_topic(
        self,
        *,
        topic_id: int,
        pack_ids: list[int],
        sales_attribute_name: str = "Theme",
        title: str = "",
        description_html: str = "",
        category_id: str = "928016",
        primary_pack_id: Optional[int] = None,
    ) -> int:
        if not pack_ids:
            raise ValueError("pack_ids required")
        with _open_db(self.db_path) as conn:
            # Validate topic + packs
            t = conn.execute("SELECT id, topic_name FROM hot_topics WHERE id=?", (topic_id,)).fetchone()
            if not t:
                raise ValueError(f"hot_topic #{topic_id} not found")
            ph = ",".join("?" * len(pack_ids))
            packs = conn.execute(
                f"""SELECT id, display_name, pack_uid, total_stickers
                      FROM packs WHERE id IN ({ph}) ORDER BY id""",
                tuple(pack_ids),
            ).fetchall()
            if len(packs) != len(pack_ids):
                raise ValueError(
                    f"some pack_ids missing: requested {pack_ids}, found {[p['id'] for p in packs]}"
                )

            primary = primary_pack_id or packs[0]["id"]
            n = _now()
            cur = conn.execute(
                """INSERT INTO lab_msku_products
                    (topic_id, title, description_html, selling_points, keywords,
                     category_id, sales_attribute_name, primary_pack_id,
                     publish_status, tiktok_product_id, detail_main_raw_text,
                     created_at, updated_at)
                   VALUES (?, ?, ?, '[]', '[]', ?, ?, ?, 'draft', '', '', ?, ?)""",
                (
                    topic_id,
                    _clamp_title(title or f"{t['topic_name']} — Multi-Pack Bundle"),
                    description_html,
                    category_id,
                    sales_attribute_name,
                    primary,
                    n, n,
                ),
            )
            product_id = cur.lastrowid

            # Build SKUs from packs
            topic_slug = _slug(t["topic_name"] or "", 8) or f"T{topic_id}"
            for i, p in enumerate(packs, start=1):
                pack_slug = _slug(p["display_name"] or p["pack_uid"] or "", 10) or f"P{p['id']}"
                seller_sku = make_seller_sku(prefix="INK", topic_slug=topic_slug, pack_slug=pack_slug)
                conn.execute(
                    """INSERT INTO lab_msku_product_skus
                        (product_id, pack_id, seller_sku, sales_attribute_value,
                         price_amount_override, stock_override, sort_order,
                         tiktok_sku_id, created_at)
                       VALUES (?, ?, ?, ?, '', NULL, ?, '', ?)""",
                    (product_id, p["id"], seller_sku,
                     p["display_name"] or f"Pack {p['id']}", i, n),
                )
            conn.commit()
        return product_id

    def update_product_meta(
        self,
        product_id: int,
        *,
        title: Optional[str] = None,
        description_html: Optional[str] = None,
        selling_points: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        category_id: Optional[str] = None,
        sales_attribute_name: Optional[str] = None,
        primary_pack_id: Optional[int] = None,
    ) -> bool:
        sets, params = [], []
        if title is not None:
            sets.append("title=?"); params.append(_clamp_title(title))
        if description_html is not None:
            sets.append("description_html=?"); params.append(description_html)
        if selling_points is not None:
            sets.append("selling_points=?"); params.append(json.dumps(selling_points, ensure_ascii=False))
        if keywords is not None:
            sets.append("keywords=?"); params.append(json.dumps(keywords, ensure_ascii=False))
        if category_id is not None:
            sets.append("category_id=?"); params.append(category_id)
        if sales_attribute_name is not None:
            sets.append("sales_attribute_name=?"); params.append(sales_attribute_name[:50])
        if primary_pack_id is not None:
            sets.append("primary_pack_id=?"); params.append(primary_pack_id)
        if not sets:
            return False
        sets.append("updated_at=?"); params.append(_now())
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                f"UPDATE lab_msku_products SET {', '.join(sets)} WHERE id=?",
                tuple(params) + (product_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_product(self, product_id: int) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute("DELETE FROM lab_msku_products WHERE id=?", (product_id,))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # CRUD: SKU children
    # ------------------------------------------------------------------

    def add_sku(
        self,
        product_id: int,
        *,
        pack_id: Optional[int],
        seller_sku: str,
        sales_attribute_value: str,
        price_amount_override: str = "",
        stock_override: Optional[int] = None,
    ) -> int:
        seller_sku = (seller_sku or "").strip().upper()
        if not _SKU_VALID_RE.match(seller_sku):
            raise ValueError(f"invalid seller_sku: {seller_sku}")
        with _open_db(self.db_path) as conn:
            sort_order = (conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM lab_msku_product_skus WHERE product_id=?",
                (product_id,),
            ).fetchone()[0]) or 1
            cur = conn.execute(
                """INSERT INTO lab_msku_product_skus
                    (product_id, pack_id, seller_sku, sales_attribute_value,
                     price_amount_override, stock_override, sort_order,
                     tiktok_sku_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)""",
                (product_id, pack_id, seller_sku, sales_attribute_value,
                 price_amount_override or "", stock_override,
                 sort_order, _now()),
            )
            conn.commit()
            return cur.lastrowid

    def update_sku(
        self,
        sku_id: int,
        *,
        seller_sku: Optional[str] = None,
        sales_attribute_value: Optional[str] = None,
        price_amount_override: Optional[str] = None,
        stock_override: Any = ...,
        sort_order: Optional[int] = None,
        pack_id: Any = ...,
    ) -> bool:
        sets, params = [], []
        if seller_sku is not None:
            v = (seller_sku or "").strip().upper()
            if v and not _SKU_VALID_RE.match(v):
                raise ValueError(f"invalid seller_sku: {v}")
            sets.append("seller_sku=?"); params.append(v)
        if sales_attribute_value is not None:
            sets.append("sales_attribute_value=?"); params.append(sales_attribute_value[:100])
        if price_amount_override is not None:
            sets.append("price_amount_override=?"); params.append(price_amount_override)
        if stock_override is not ...:
            sets.append("stock_override=?"); params.append(stock_override)
        if sort_order is not None:
            sets.append("sort_order=?"); params.append(int(sort_order))
        if pack_id is not ...:
            sets.append("pack_id=?"); params.append(pack_id)
        if not sets:
            return False
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                f"UPDATE lab_msku_product_skus SET {', '.join(sets)} WHERE id=?",
                tuple(params) + (sku_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_sku(self, sku_id: int) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute("DELETE FROM lab_msku_product_skus WHERE id=?", (sku_id,))
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Publish / update / sync (talks to multi-channel-api)
    # ------------------------------------------------------------------

    def _gather_image_paths_for_primary_pack(self, primary_pack_id: int) -> list[str]:
        """Use AI-generated product images from the legacy tkshop_products
        of the primary pack. If none exist, fall back to pack previews.
        """
        paths: list[str] = []
        with _open_db(self.db_path) as conn:
            # First try: tkshop_products linked to this pack (already polished
            # images uploaded by operator / auto_design_images)
            row = conn.execute(
                "SELECT id FROM tkshop_products WHERE pack_id=? ORDER BY id DESC LIMIT 1",
                (primary_pack_id,),
            ).fetchone()
            if row:
                imgs = conn.execute(
                    """SELECT local_path FROM tkshop_product_images
                       WHERE product_id=?
                       ORDER BY role='main' DESC, sort_order ASC""",
                    (row["id"],),
                ).fetchall()
                for i in imgs:
                    if i["local_path"]:
                        ap = i["local_path"] if os.path.isabs(i["local_path"]) else os.path.abspath(i["local_path"])
                        if os.path.isfile(ap):
                            paths.append(ap)
            if paths:
                return paths
            # Fallback: pack previews
            r2 = conn.execute(
                """SELECT pp.image_path
                     FROM packs pk
                LEFT JOIN pack_previews pp ON pp.series_id = pk.series_id
                                          AND pp.generation_status='ok'
                                          AND COALESCE(pp.image_path,'') != ''
                    WHERE pk.id=?
                    ORDER BY pp.preview_idx
                    LIMIT 8""",
                (primary_pack_id,),
            ).fetchall()
            for i in r2:
                if i["image_path"]:
                    ap = i["image_path"] if os.path.isabs(i["image_path"]) else os.path.abspath(i["image_path"])
                    if os.path.isfile(ap):
                        paths.append(ap)
        return paths

    def build_publish_payload(self, product_id: int) -> dict[str, Any]:
        p = self.get_product(product_id)
        if not p:
            raise ValueError(f"lab_msku_product #{product_id} not found")
        if not p.get("skus"):
            raise ValueError("no SKUs configured")
        primary = p.get("primary_pack_id") or (p["skus"][0].get("pack_id") if p["skus"] else None)
        image_paths = self._gather_image_paths_for_primary_pack(primary) if primary else []

        skus_payload = []
        for s in p["skus"]:
            stock = s.get("stock_override")
            try:
                stock_int = int(stock) if stock not in (None, "") else None
            except (TypeError, ValueError):
                stock_int = None
            skus_payload.append({
                "seller_sku": s["seller_sku"],
                "attribute_value": s.get("sales_attribute_value") or "",
                "price_amount": (s.get("price_amount_override") or "").strip(),
                "stock": stock_int,
            })

        return {
            "title": _clamp_title(p.get("title") or ""),
            "description_html": p.get("description_html") or "",
            "category_id": p.get("category_id") or "928016",
            "sales_attribute_name": p.get("sales_attribute_name") or "Theme",
            "skus": skus_payload,
            "image_urls": [],
            "image_paths": image_paths,
            "auto_activate": True,  # caller can override before send
        }

    def publish(self, product_id: int, *, auto_activate: bool = True) -> dict[str, Any]:
        payload = self.build_publish_payload(product_id)
        payload["auto_activate"] = bool(auto_activate)
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/sticker_multi_sku_publish"

        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE lab_msku_products SET publish_status='publishing', updated_at=? WHERE id=?",
                (_now(), product_id),
            )
            conn.commit()

        resp_json: dict[str, Any] = {}
        success = False
        error_code = ""
        error_message = ""
        tiktok_product_id = ""
        sku_id_map: dict[str, str] = {}
        try:
            resp = requests.post(endpoint, json=payload, timeout=TKSHOP_SERVER_TIMEOUT)
            try:
                resp_json = resp.json()
            except Exception:
                resp_json = {"_raw_text": resp.text[:500]}
            if isinstance(resp_json, dict) and "data" in resp_json and isinstance(resp_json["data"], dict) and "ok" in resp_json["data"]:
                resp_json = resp_json["data"]
            if 200 <= resp.status_code < 300 and resp_json.get("ok") is True:
                success = True
                tiktok_product_id = str(resp_json.get("product_id") or "")
                tt_status = (resp_json.get("status") or "").upper()
                for s in (resp_json.get("skus") or []):
                    if s.get("seller_sku"):
                        sku_id_map[s["seller_sku"]] = str(s.get("sku_id") or "")
            else:
                error_code = str(resp_json.get("error_code") or resp.status_code)
                error_message = str(
                    resp_json.get("error_message")
                    or resp_json.get("error")
                    or resp.text[:200]
                )
        except requests.RequestException as e:
            error_code = "NETWORK"; error_message = str(e)[:300]
        except Exception as e:
            error_code = "INTERNAL"; error_message = f"{type(e).__name__}: {e}"[:300]

        with _open_db(self.db_path) as conn:
            if success:
                local_status = "draft_on_platform" if not auto_activate else "published"
                conn.execute(
                    """UPDATE lab_msku_products
                          SET publish_status=?, tiktok_product_id=?,
                              published_at=?, updated_at=?
                        WHERE id=?""",
                    (local_status, tiktok_product_id, _now(), _now(), product_id),
                )
                # write back tiktok_sku_id per child
                for seller_sku, sku_id in sku_id_map.items():
                    conn.execute(
                        "UPDATE lab_msku_product_skus SET tiktok_sku_id=? WHERE product_id=? AND seller_sku=?",
                        (sku_id, product_id, seller_sku),
                    )
            else:
                conn.execute(
                    "UPDATE lab_msku_products SET publish_status='failed', updated_at=? WHERE id=?",
                    (_now(), product_id),
                )
            conn.commit()

        return {
            "ok": success,
            "tiktok_product_id": tiktok_product_id,
            "sku_id_map": sku_id_map,
            "error_code": error_code,
            "error_message": error_message,
            "endpoint": endpoint,
        }

    def update_on_platform(self, product_id: int) -> dict[str, Any]:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT tiktok_product_id FROM lab_msku_products WHERE id=?",
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            tt_id = (row["tiktok_product_id"] or "").strip()
        if not tt_id:
            return {"ok": False, "error_code": "NOT_PUBLISHED",
                    "error_message": "no tiktok_product_id; publish first"}
        payload = self.build_publish_payload(product_id)
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/{tt_id}/sticker_multi_sku_update"
        try:
            resp = requests.post(endpoint, json=payload, timeout=TKSHOP_SERVER_TIMEOUT)
            data = resp.json() if resp.text else {}
        except requests.RequestException as e:
            return {"ok": False, "error_code": "NETWORK",
                    "error_message": f"{type(e).__name__}: {e}"}
        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            inner = data if isinstance(data, dict) else {}
        if not (resp.ok and inner.get("ok") is True):
            return {"ok": False,
                    "error_code": str(inner.get("error_code") or resp.status_code),
                    "error_message": inner.get("error_message") or "update failed"}
        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE lab_msku_products SET updated_at=? WHERE id=?",
                (_now(), product_id),
            )
            for s in (inner.get("skus") or []):
                if s.get("seller_sku") and s.get("sku_id"):
                    conn.execute(
                        "UPDATE lab_msku_product_skus SET tiktok_sku_id=? WHERE product_id=? AND seller_sku=?",
                        (s["sku_id"], product_id, s["seller_sku"]),
                    )
            conn.commit()
        return {"ok": True, "tiktok_product_id": tt_id, "skus": inner.get("skus") or []}

    def sync_status(self, product_id: int) -> dict[str, Any]:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT tiktok_product_id, publish_status FROM lab_msku_products WHERE id=?",
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            tt_id = (row["tiktok_product_id"] or "").strip()
        if not tt_id:
            return {"ok": False, "error_message": "no tiktok_product_id"}
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/{tt_id}"
        try:
            resp = requests.get(endpoint, timeout=TKSHOP_SERVER_TIMEOUT)
            data = resp.json() if resp.text else {}
        except requests.RequestException as e:
            return {"ok": False, "error_message": f"{type(e).__name__}: {e}"}
        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            inner = data if isinstance(data, dict) else {}
        remote = (inner.get("status") or inner.get("product_status") or "").strip().lower()
        if remote and remote != (row["publish_status"] or "").lower():
            with _open_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE lab_msku_products SET publish_status=?, updated_at=? WHERE id=?",
                    (remote, _now(), product_id),
                )
                conn.commit()
        return {"ok": True, "remote_status": remote, "previous": row["publish_status"]}


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[LabMultiSkuService] = None


def get_lab_multi_sku_service() -> LabMultiSkuService:
    global _svc
    if _svc is None:
        _svc = LabMultiSkuService()
    return _svc
