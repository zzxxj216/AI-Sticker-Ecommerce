"""TKShop product service — C.1 / C.2 / C.3 / C.4 of the V2 pipeline.

C.1: create product row from a pack, AI-generate detail two-step
C.2: register product images (rows + files via PackStore)
C.3: publish via the ops server at 192.168.121.1 (URL/curl-shape pending
     from operator — wrapper raises a clear NotConfigured until then)
C.4: status sync — same server
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import requests

from src.core.exceptions import APIError
from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router
from src.services.storage.pack_store import PackStore, get_pack_store
from src.services.tkshop.prompts import (
    DETAIL_EXTRACT_INSTRUCTIONS,
    DETAIL_EXTRACT_SCHEMA,
    DETAIL_MAIN_SYSTEM_PROMPT,
    build_detail_main_prompt,
)

logger = get_logger("service.tkshop")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

# Operator's local TKShop wrapper server — see CLAUDE.md / project notes.
# Endpoint shape pending operator confirmation; we POST a JSON product
# payload and read back {ok, tiktok_product_id?, error?}.
TKSHOP_SERVER_URL = os.getenv("TKSHOP_SERVER_URL", "http://192.168.121.1")
TKSHOP_SERVER_TIMEOUT = int(os.getenv("TKSHOP_SERVER_TIMEOUT", "60"))


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class TKShopService:
    def __init__(
        self,
        router: Optional[AIRouter] = None,
        store: Optional[PackStore] = None,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.router = router or get_router()
        self.store = store or get_pack_store()
        self.db_path = db_path

    # ------------------------------------------------------------------
    # C.1 create + AI generate
    # ------------------------------------------------------------------

    def create_product_from_pack(self, pack_id: int) -> int:
        """Mint a draft tkshop_products row for this pack. Idempotent —
        if a product already exists for this pack_id, return its id.
        """
        with _open_db(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id FROM tkshop_products WHERE pack_id = ? ORDER BY id DESC LIMIT 1",
                (pack_id,),
            ).fetchone()
            if existing:
                return existing["id"]
            pack = conn.execute(
                "SELECT id FROM packs WHERE id = ?", (pack_id,),
            ).fetchone()
            if not pack:
                raise ValueError(f"pack #{pack_id} not found")
            now = int(time.time())
            cur = conn.execute(
                """
                INSERT INTO tkshop_products
                    (pack_id, tiktok_product_id, detail_main_raw_text,
                     title, description_html, selling_points, keywords,
                     category_id, default_template_json, publish_status,
                     created_at, published_at)
                VALUES (?, '', '', '', '', '[]', '[]', '928016', '{}', 'draft', ?, NULL)
                """,
                (pack_id, now),
            )
            conn.commit()
            return cur.lastrowid

    def generate_detail(
        self,
        product_id: int,
        *,
        main_model: Optional[str] = None,
        extract_model: Optional[str] = None,
    ) -> dict[str, Any]:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pr.id, pr.pack_id,
                       p.display_name, p.total_stickers, p.pack_uid,
                       s.style_anchor, s.palette, s.pack_archetype,
                       s.metadata_json
                  FROM tkshop_products pr
                  JOIN packs           p ON p.id = pr.pack_id
             LEFT JOIN pack_series     s ON s.id = p.series_id
                 WHERE pr.id = ?
                """,
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"tkshop_product #{product_id} not found")

        # Pull a representative sample of sticker briefs from the series
        # metadata (preview_briefs flatten).
        briefs_sample: list[str] = []
        try:
            md = json.loads(row["metadata_json"] or "{}")
            for pb in md.get("preview_briefs", [])[:3]:
                briefs_sample.extend(pb.get("stickers", [])[:4])
        except Exception:
            pass

        prompt = build_detail_main_prompt(
            pack_display_name=row["display_name"] or "",
            pack_archetype=row["pack_archetype"] or "",
            style_anchor=row["style_anchor"] or "",
            palette=row["palette"] or "",
            total_stickers=row["total_stickers"] or 0,
            sticker_briefs_sample=briefs_sample,
        )

        main_text = self.router.text_complete(
            prompt,
            model=main_model,
            system=DETAIL_MAIN_SYSTEM_PROMPT,
            temperature=0.7,
            task="tkshop_detail:main",
            related_table="tkshop_products",
            related_id=product_id,
        )

        extract_ok = True
        extract_error = ""
        payload: dict[str, Any] = {}
        try:
            payload = self.router.extract_json(
                main_text,
                schema=DETAIL_EXTRACT_SCHEMA,
                instructions=DETAIL_EXTRACT_INSTRUCTIONS,
                model=extract_model,
                max_retries=1,
                task="tkshop_detail:extract",
                related_table="tkshop_products",
                related_id=product_id,
            )
        except Exception as e:
            logger.warning("tkshop detail extract failed for #%d: %s", product_id, e)
            extract_ok = False
            extract_error = str(e)[:500]

        with _open_db(self.db_path) as conn:
            if extract_ok:
                conn.execute(
                    """
                    UPDATE tkshop_products
                       SET detail_main_raw_text = ?, title = ?,
                           description_html = ?, selling_points = ?,
                           keywords = ?
                     WHERE id = ?
                    """,
                    (
                        main_text,
                        (payload.get("title") or "")[:200],
                        payload.get("description_html") or "",
                        json.dumps(payload.get("selling_points") or [], ensure_ascii=False),
                        json.dumps(payload.get("keywords") or [], ensure_ascii=False),
                        product_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE tkshop_products SET detail_main_raw_text = ? WHERE id = ?",
                    (main_text, product_id),
                )
            conn.commit()

        # Persist all artifacts to disk via PackStore.
        try:
            self.store.write_product_detail(
                row["pack_uid"], f"draft_{product_id}",
                title=payload.get("title") or "",
                description_html=payload.get("description_html") or "",
                selling_points=payload.get("selling_points") or [],
                keywords=payload.get("keywords") or [],
                main_raw=main_text,
            )
        except Exception as e:
            logger.warning("write_product_detail failed: %s", e)

        return {
            "product_id": product_id,
            "extract_ok": extract_ok,
            "extract_error": extract_error,
            "main_chars": len(main_text),
            "title": payload.get("title", ""),
            "selling_points_count": len(payload.get("selling_points", [])),
            "keywords_count": len(payload.get("keywords", [])),
        }

    def update_detail_manual(
        self,
        product_id: int,
        *,
        title: Optional[str] = None,
        description_html: Optional[str] = None,
        selling_points: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        category_id: Optional[str] = None,
    ) -> bool:
        sets, params = [], []
        if title is not None:
            sets.append("title = ?"); params.append(title[:200])
        if description_html is not None:
            sets.append("description_html = ?"); params.append(description_html)
        if selling_points is not None:
            sets.append("selling_points = ?"); params.append(json.dumps(selling_points, ensure_ascii=False))
        if keywords is not None:
            sets.append("keywords = ?"); params.append(json.dumps(keywords, ensure_ascii=False))
        if category_id is not None:
            sets.append("category_id = ?"); params.append(category_id)
        if not sets:
            return False
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                f"UPDATE tkshop_products SET {', '.join(sets)} WHERE id = ?",
                tuple(params) + (product_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # C.2 product images
    # ------------------------------------------------------------------

    def add_product_image(
        self,
        product_id: int,
        *,
        src_path: Path,
        role: str = "main",
        source: str = "manual",
        ai_prompt: str = "",
    ) -> int:
        """Save uploaded image into PackStore + insert tkshop_product_images row."""
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pr.id, p.pack_uid
                  FROM tkshop_products pr
                  JOIN packs           p ON p.id = pr.pack_id
                 WHERE pr.id = ?
                """,
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            sort_order = (conn.execute(
                """
                SELECT COALESCE(MAX(sort_order), 0) + 1
                  FROM tkshop_product_images
                 WHERE product_id = ? AND role = ?
                """, (product_id, role),
            ).fetchone()[0]) or 1
        ext = (src_path.suffix or ".png").lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            ext = ".png"
        filename = f"{role}_{sort_order}{ext}" if role != "main" or sort_order > 1 else f"main{ext}"
        dest = self.store.write_product_image(
            row["pack_uid"], f"draft_{product_id}", filename,
            src_path.read_bytes(),
        )
        rel = dest.as_posix()
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO tkshop_product_images
                    (product_id, role, source, local_path, tiktok_image_uri,
                     sort_order, ai_prompt, created_at)
                VALUES (?, ?, ?, ?, '', ?, ?, ?)
                """,
                (product_id, role, source, rel, sort_order, ai_prompt, int(time.time())),
            )
            conn.commit()
            return cur.lastrowid

    def list_product_images(self, product_id: int) -> list[dict]:
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, role, source, local_path, tiktok_image_uri,
                       sort_order, ai_prompt, created_at
                  FROM tkshop_product_images
                 WHERE product_id = ?
                 ORDER BY role, sort_order
                """,
                (product_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_image(self, image_id: int) -> bool:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT local_path FROM tkshop_product_images WHERE id = ?",
                (image_id,),
            ).fetchone()
            if not row:
                return False
            cur = conn.execute(
                "DELETE FROM tkshop_product_images WHERE id = ?", (image_id,),
            )
            conn.commit()
            ok = cur.rowcount > 0
        if ok and row["local_path"]:
            try:
                Path(row["local_path"]).unlink()
            except Exception:
                pass
        return ok

    # ------------------------------------------------------------------
    # C.3 publish — POST to ops server
    # ------------------------------------------------------------------

    def publish(self, product_id: int, *, dry_run: bool = False) -> dict[str, Any]:
        """POST product payload to the operator's TKShop wrapper server.

        The wrapper at ``TKSHOP_SERVER_URL`` (default 192.168.121.1) does
        all the TikTok-side signing — we just send a JSON product spec.
        Endpoint shape pending operator confirmation; current default is
        ``POST {TKSHOP_SERVER_URL}/products`` with body::

          {
            "product": {
              "title": ..., "description_html": ...,
              "selling_points": [...], "keywords": [...],
              "category_id": ..., "images": [{"url": "...", "role": "main", ...}, ...]
            }
          }

        On success, expect ``{"ok": true, "tiktok_product_id": "..."}``.
        ``dry_run=True`` builds the payload + writes the publish_log row
        but does NOT make the HTTP call (useful for verifying the shape).
        """
        product = self._build_publish_payload(product_id)

        attempt_idx = 1
        with _open_db(self.db_path) as conn:
            attempt_idx = (conn.execute(
                "SELECT COALESCE(MAX(attempt_idx), 0) + 1 FROM tkshop_publish_logs WHERE product_id = ?",
                (product_id,),
            ).fetchone()[0]) or 1

        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/products"
        if dry_run:
            self._log_publish_attempt(
                product_id, attempt_idx, endpoint, product,
                response={"_dry_run": True},
                success=True,
            )
            return {"ok": True, "dry_run": True, "endpoint": endpoint,
                    "payload": product}

        resp_json: dict[str, Any] = {}
        success = False
        error_code = ""
        error_message = ""
        tiktok_product_id = ""
        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE tkshop_products SET publish_status = 'publishing' WHERE id = ?",
                (product_id,),
            )
            conn.commit()
        try:
            resp = requests.post(endpoint, json={"product": product},
                                 timeout=TKSHOP_SERVER_TIMEOUT)
            try:
                resp_json = resp.json()
            except Exception:
                resp_json = {"_raw_text": resp.text[:500]}
            if 200 <= resp.status_code < 300 and resp_json.get("ok") is True:
                success = True
                tiktok_product_id = str(resp_json.get("tiktok_product_id") or "")
            else:
                error_code = str(resp.status_code)
                error_message = str(resp_json.get("error") or resp.text[:200])
        except requests.RequestException as e:
            error_code = "NETWORK"
            error_message = str(e)[:300]
        except Exception as e:
            error_code = "INTERNAL"
            error_message = f"{type(e).__name__}: {e}"[:300]

        self._log_publish_attempt(
            product_id, attempt_idx, endpoint, product,
            response=resp_json, success=success,
            error_code=error_code, error_message=error_message,
        )

        with _open_db(self.db_path) as conn:
            if success:
                conn.execute(
                    """
                    UPDATE tkshop_products
                       SET publish_status = 'published',
                           tiktok_product_id = ?,
                           published_at = ?
                     WHERE id = ?
                    """,
                    (tiktok_product_id, int(time.time()), product_id),
                )
            else:
                conn.execute(
                    "UPDATE tkshop_products SET publish_status = 'failed' WHERE id = ?",
                    (product_id,),
                )
            conn.commit()

        return {
            "ok": success, "tiktok_product_id": tiktok_product_id,
            "error_code": error_code, "error_message": error_message,
            "attempt_idx": attempt_idx,
        }

    def _build_publish_payload(self, product_id: int) -> dict[str, Any]:
        with _open_db(self.db_path) as conn:
            pr = conn.execute(
                """
                SELECT pr.id, pr.title, pr.description_html, pr.selling_points,
                       pr.keywords, pr.category_id, pr.default_template_json,
                       pr.tiktok_product_id,
                       p.display_name, p.pack_uid
                  FROM tkshop_products pr
                  JOIN packs p ON p.id = pr.pack_id
                 WHERE pr.id = ?
                """, (product_id,),
            ).fetchone()
            if not pr:
                raise ValueError(f"product #{product_id} not found")
            imgs = conn.execute(
                """
                SELECT role, local_path, tiktok_image_uri, sort_order
                  FROM tkshop_product_images
                 WHERE product_id = ?
                 ORDER BY role, sort_order
                """, (product_id,),
            ).fetchall()

        try:
            selling_points = json.loads(pr["selling_points"] or "[]")
        except Exception:
            selling_points = []
        try:
            keywords = json.loads(pr["keywords"] or "[]")
        except Exception:
            keywords = []
        try:
            template = json.loads(pr["default_template_json"] or "{}")
        except Exception:
            template = {}

        images = []
        for i in imgs:
            d = dict(i)
            # Wrapper server is on the same LAN as ops; serve via /v2-outputs.
            if d.get("local_path"):
                p = d["local_path"].replace("\\", "/")
                idx = p.find("output/packs/")
                if idx >= 0:
                    d["local_url_path"] = "/v2-outputs/" + p[idx + len("output/packs/"):]
            images.append(d)

        return {
            "tiktok_product_id": pr["tiktok_product_id"] or "",
            "title": pr["title"] or "",
            "description_html": pr["description_html"] or "",
            "selling_points": selling_points,
            "keywords": keywords,
            "category_id": pr["category_id"] or "",
            "template": template,
            "images": images,
            "pack_display_name": pr["display_name"],
            "pack_uid": pr["pack_uid"],
        }

    def _log_publish_attempt(
        self,
        product_id: int,
        attempt_idx: int,
        endpoint: str,
        request_payload: dict,
        *,
        response: dict,
        success: bool,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO tkshop_publish_logs
                    (product_id, attempt_idx, api_endpoint,
                     request_payload, response_payload, success,
                     error_code, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id, attempt_idx, endpoint,
                    json.dumps(request_payload, ensure_ascii=False),
                    json.dumps(response, ensure_ascii=False),
                    1 if success else 0,
                    error_code, error_message[:500],
                    int(time.time()),
                ),
            )
            conn.commit()

    def sync_statuses(self) -> dict[str, Any]:
        """C.4 — query the wrapper server for status of every published product.

        Hits ``GET {TKSHOP_SERVER_URL}/products/{tiktok_product_id}/status``
        and updates publish_status if the wrapper reports a state change
        (e.g., the product went under review or got delisted).

        Returns ``{'checked': n, 'updated': n, 'errors': [...]}``. When the
        wrapper isn't reachable we return ``checked=0`` with an error
        rather than failing — the scheduler treats this as a soft failure.
        """
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, tiktok_product_id, publish_status
                  FROM tkshop_products
                 WHERE publish_status = 'published'
                   AND tiktok_product_id != ''
                """,
            ).fetchall()
        if not rows:
            return {"checked": 0, "updated": 0, "errors": []}

        updated, errors = 0, []
        for r in rows:
            url = f"{TKSHOP_SERVER_URL.rstrip('/')}/products/{r['tiktok_product_id']}/status"
            try:
                resp = requests.get(url, timeout=TKSHOP_SERVER_TIMEOUT)
                if not resp.ok:
                    errors.append({"product_id": r["id"], "error": f"HTTP {resp.status_code}"})
                    continue
                body = resp.json() if resp.text else {}
                remote = (body.get("status") or "").strip().lower()
                if remote and remote != r["publish_status"]:
                    with _open_db(self.db_path) as conn:
                        conn.execute(
                            "UPDATE tkshop_products SET publish_status = ? WHERE id = ?",
                            (remote, r["id"]),
                        )
                        conn.commit()
                    updated += 1
            except requests.RequestException as e:
                errors.append({"product_id": r["id"], "error": f"{type(e).__name__}: {e}"[:200]})
        return {"checked": len(rows), "updated": updated, "errors": errors}

    def list_publish_logs(self, product_id: int) -> list[dict]:
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, attempt_idx, api_endpoint, success,
                       error_code, error_message, created_at
                  FROM tkshop_publish_logs
                 WHERE product_id = ?
                 ORDER BY id DESC
                """, (product_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_products(
        self,
        *,
        pack_id: Optional[int] = None,
        publish_status: Optional[str] = None,
        limit: int = 100,
    ) -> tuple[list[dict], int]:
        clauses, params = [], []
        if pack_id is not None:
            clauses.append("pr.pack_id = ?"); params.append(pack_id)
        if publish_status and publish_status != "all":
            clauses.append("pr.publish_status = ?"); params.append(publish_status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _open_db(self.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM tkshop_products pr {where}", tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT pr.id, pr.pack_id, pr.tiktok_product_id, pr.title,
                       pr.publish_status, pr.created_at, pr.published_at,
                       pr.category_id,
                       p.display_name AS pack_name, p.cover_image_path AS pack_cover
                  FROM tkshop_products pr
             LEFT JOIN packs           p ON p.id = pr.pack_id
                  {where}
                 ORDER BY pr.id DESC
                 LIMIT ?
                """,
                tuple(params) + (limit,),
            ).fetchall()
        return [dict(r) for r in rows], total

    def get_product(self, product_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                """
                SELECT pr.id, pr.pack_id, pr.tiktok_product_id,
                       pr.detail_main_raw_text, pr.title,
                       pr.description_html, pr.selling_points,
                       pr.keywords, pr.category_id,
                       pr.default_template_json, pr.publish_status,
                       pr.created_at, pr.published_at,
                       p.display_name AS pack_name, p.cover_image_path,
                       p.pack_uid, p.total_stickers
                  FROM tkshop_products pr
             LEFT JOIN packs           p ON p.id = pr.pack_id
                 WHERE pr.id = ?
                """, (product_id,),
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
            d["images"] = self.list_product_images(product_id)
            d["publish_logs"] = self.list_publish_logs(product_id)
        return d


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[TKShopService] = None


def get_tkshop_service() -> TKShopService:
    global _svc
    if _svc is None:
        _svc = TKShopService()
    return _svc
