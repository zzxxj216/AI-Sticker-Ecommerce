"""TKShop product service — C.1 / C.2 / C.3 / C.4 of the V2 pipeline.

C.1: create product row from a pack, AI-generate detail two-step
C.2: register product images (rows + files via PackStore)
C.3: publish via the multi-channel-api FastAPI service at
     ``POST {TKSHOP_SERVER_URL}/api/v1/tiktok/products/sticker_publish``
C.4: status sync via
     ``GET  {TKSHOP_SERVER_URL}/api/v1/tiktok/products/{tiktok_product_id}``

The wrapper at multi-channel-api owns price / warehouse / weight defaults
(see its sticker_field_config). We only send the listing copy + image URLs
+ category + seller_sku + quantity. multi-channel-api fills the rest.
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

from src.core.exceptions import APIError
from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router
from src.services.storage.pack_store import PackStore, get_pack_store
from src.services.tkshop.prompts import (
    DETAIL_EXTRACT_INSTRUCTIONS,
    DETAIL_EXTRACT_SCHEMA,
    DETAIL_MAIN_SYSTEM_PROMPT,
    IMAGE_DESIGN_EXTRACT_SCHEMA,
    IMAGE_DESIGN_INSTRUCTIONS,
    IMAGE_DESIGN_SYSTEM_PROMPT,
    SELF_HEAL_EXTRACT_SCHEMA,
    SELF_HEAL_INSTRUCTIONS,
    SELF_HEAL_SYSTEM_PROMPT,
    build_detail_main_prompt,
    build_image_design_prompt,
    build_self_heal_prompt,
)

logger = get_logger("service.tkshop")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

# multi-channel-api FastAPI service. Defaults to localhost:8000 because
# both processes run on the same box during development.
TKSHOP_SERVER_URL = os.getenv("TKSHOP_SERVER_URL", "http://localhost:8000")
# 300s default: sticker_publish does N image fetches + N TikTok uploads +
# create + activate, easily exceeds 60s for 4-6 images. Override via env.
TKSHOP_SERVER_TIMEOUT = int(os.getenv("TKSHOP_SERVER_TIMEOUT", "300"))

# Public URL the multi-channel-api server uses to fetch our local product
# images. It must be reachable from that server's perspective. Both run
# on localhost so the existing /v2-outputs static mount works.
TKSHOP_IMAGE_PUBLIC_BASE = os.getenv(
    "TKSHOP_IMAGE_PUBLIC_BASE", "http://localhost:5000"
).rstrip("/")

# Self-heal toggles
TKSHOP_PUBLISH_AUTO_FIX_DEFAULT = (
    os.getenv("TKSHOP_PUBLISH_AUTO_FIX", "true").strip().lower()
    in ("1", "true", "yes", "on")
)
TKSHOP_PUBLISH_MAX_RETRIES_DEFAULT = int(
    os.getenv("TKSHOP_PUBLISH_MAX_RETRIES", "3")
)


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKU_VALID_RE = re.compile(r"^[A-Z0-9-]{1,25}$")

# TikTok Shop hard limit on product title.
TKSHOP_TITLE_MAX = 255


def _clamp_title(title: str) -> str:
    """Trim title to TKSHOP_TITLE_MAX. Strip trailing separators so we
    don't end on a dangling ``|`` or comma.
    """
    t = (title or "").strip()
    if len(t) <= TKSHOP_TITLE_MAX:
        return t
    return t[:TKSHOP_TITLE_MAX].rstrip(" |,-—·•")


def _friendly_platform_error(action: str, raw: str) -> str:
    """Map opaque TikTok error text into a Chinese hint for the operator."""
    raw_low = (raw or "").lower()
    expected_state = {
        "activate":   "FAILED / INACTIVE / DRAFT (审核通过后)",
        "deactivate": "LIVE",
        "recover":    "DELETED",
    }.get(action, "—")
    # 12052901 = product status invalid for this action
    if "12052901" in raw or "product status invalid" in raw_low or "status invalid" in raw_low:
        return (
            f"商品当前状态不允许「{action}」操作（TikTok 要求状态: {expected_state}）。"
            f"请先点🔄 同步平台状态查看真实状态 — 例如已是 DRAFT/PENDING 不能下架，已是 SUSPENDED 不能再下架。"
            f"\n原始错误: {raw[:200]}"
        )
    if "11017002" in raw or "category" in raw_low and "audit" in raw_low:
        return f"分类资质审核未通过 — TikTok 卖家中心查看商品审核状态。原始: {raw[:200]}"
    if "12017004" in raw or "permission" in raw_low:
        return f"无权限执行此操作 — token scope 可能缺失。原始: {raw[:200]}"
    return f"TikTok 拒绝: {raw[:300]}"


def _slugify_for_sku(name: str) -> str:
    """Pack name -> 1-10 char uppercased alphanumeric slug."""
    return re.sub(r"[^A-Za-z0-9]", "", name or "").upper()[:10]


def compute_default_seller_sku(
    *, pack_uid: str, total_stickers: int, pack_id: int
) -> str:
    """Default seller_sku = ``INK-{slug10}-{total_stickers}``.

    slug10 is the uppercased alphanumeric pack_uid truncated to 10
    characters. If pack_uid yields an empty slug (all punctuation), fall
    back to ``P{pack_id}`` so we still emit a valid SKU.
    """
    slug = _slugify_for_sku(pack_uid)
    if not slug:
        slug = f"P{pack_id}"
    sku = f"INK-{slug}-{int(total_stickers or 0)}"
    return sku[:25]


def _is_valid_seller_sku(sku: str) -> bool:
    return bool(sku) and bool(_SKU_VALID_RE.match(sku))


def _local_path_to_public_url(local_path: str) -> str:
    """Turn ``output/packs/<uid>/...`` into the public ``/v2-outputs/...`` URL.

    The multi-channel-api server is running on the same host (localhost),
    so it hits our FastAPI app's static mount at ``/v2-outputs/`` to fetch
    the image. If the path doesn't contain the canonical ``output/packs/``
    marker we return an empty string (caller should skip).
    """
    if not local_path:
        return ""
    p = local_path.replace("\\", "/")
    marker = "output/packs/"
    idx = p.find(marker)
    if idx < 0:
        return ""
    rel = p[idx + len(marker):]
    return f"{TKSHOP_IMAGE_PUBLIC_BASE}/v2-outputs/{rel}"


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
        # Lock product copywriting to gpt-5.4 unless operator overrides
        # via TKSHOP_DETAIL_MODEL. The router's text_complete default
        # follows OPENAI_MODEL which can drift; pin it here.
        if not main_model:
            main_model = os.getenv("TKSHOP_DETAIL_MODEL", "gpt-5.4")
        if not extract_model:
            extract_model = os.getenv("TKSHOP_EXTRACT_MODEL", "gpt-5.4")
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pr.id, pr.pack_id, pr.seller_sku AS existing_seller_sku,
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

        # Pre-compute the default seller_sku so the AI can mirror it back.
        default_sku = compute_default_seller_sku(
            pack_uid=row["pack_uid"] or "",
            total_stickers=row["total_stickers"] or 0,
            pack_id=row["pack_id"],
        )

        prompt = build_detail_main_prompt(
            pack_display_name=row["display_name"] or "",
            pack_archetype=row["pack_archetype"] or "",
            style_anchor=row["style_anchor"] or "",
            palette=row["palette"] or "",
            total_stickers=row["total_stickers"] or 0,
            sticker_briefs_sample=briefs_sample,
            suggested_seller_sku=default_sku,
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

        # Decide which seller_sku to persist. Prefer the AI's value if it's
        # valid; otherwise fall back to the deterministic default.
        ai_sku = (payload.get("seller_sku") or "").strip().upper()
        if _is_valid_seller_sku(ai_sku):
            chosen_sku = ai_sku
        else:
            chosen_sku = default_sku

        with _open_db(self.db_path) as conn:
            if extract_ok:
                conn.execute(
                    """
                    UPDATE tkshop_products
                       SET detail_main_raw_text = ?, title = ?,
                           description_html = ?, selling_points = ?,
                           keywords = ?, seller_sku = ?
                     WHERE id = ?
                    """,
                    (
                        main_text,
                        _clamp_title(payload.get("title") or ""),
                        payload.get("description_html") or "",
                        json.dumps(payload.get("selling_points") or [], ensure_ascii=False),
                        json.dumps(payload.get("keywords") or [], ensure_ascii=False),
                        chosen_sku,
                        product_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE tkshop_products
                       SET detail_main_raw_text = ?, seller_sku = ?
                     WHERE id = ?
                    """,
                    (main_text, chosen_sku, product_id),
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
            "seller_sku": chosen_sku,
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
        seller_sku: Optional[str] = None,
    ) -> bool:
        sets, params = [], []
        if title is not None:
            sets.append("title = ?"); params.append(_clamp_title(title))
        if description_html is not None:
            sets.append("description_html = ?"); params.append(description_html)
        if selling_points is not None:
            sets.append("selling_points = ?"); params.append(json.dumps(selling_points, ensure_ascii=False))
        if keywords is not None:
            sets.append("keywords = ?"); params.append(json.dumps(keywords, ensure_ascii=False))
        if category_id is not None:
            sets.append("category_id = ?"); params.append(category_id)
        if seller_sku is not None:
            sets.append("seller_sku = ?"); params.append(seller_sku[:25])
        if not sets:
            return False
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                f"UPDATE tkshop_products SET {', '.join(sets)} WHERE id = ?",
                tuple(params) + (product_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_seller_sku(self, product_id: int, seller_sku: str) -> bool:
        """Tiny helper used by self-heal to update only the SKU."""
        clean = (seller_sku or "").strip().upper()[:25]
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE tkshop_products SET seller_sku = ? WHERE id = ?",
                (clean, product_id),
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

    def update_image(
        self,
        image_id: int,
        *,
        role: Optional[str] = None,
        sort_order: Optional[int] = None,
    ) -> dict[str, Any]:
        """Mutate role and/or sort_order of an existing product image.

        If ``role='main'`` is requested and another image already holds main
        for the same product, that other image is demoted to 'secondary'
        (TikTok allows only one main).
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT product_id, role, sort_order FROM tkshop_product_images WHERE id = ?",
                (image_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"image #{image_id} not found")

            sets, params = [], []
            if role is not None and role != row["role"]:
                if role == "main":
                    # Demote any existing main for this product.
                    conn.execute(
                        "UPDATE tkshop_product_images "
                        "SET role = 'secondary' WHERE product_id = ? AND role = 'main' AND id != ?",
                        (row["product_id"], image_id),
                    )
                sets.append("role = ?"); params.append(role)
            if sort_order is not None and int(sort_order) != row["sort_order"]:
                sets.append("sort_order = ?"); params.append(int(sort_order))
            if not sets:
                conn.commit()
                return {"changed": False, "image_id": image_id,
                        "role": row["role"], "sort_order": row["sort_order"]}
            conn.execute(
                f"UPDATE tkshop_product_images SET {', '.join(sets)} WHERE id = ?",
                tuple(params) + (image_id,),
            )
            conn.commit()
            new_row = conn.execute(
                "SELECT role, sort_order FROM tkshop_product_images WHERE id = ?",
                (image_id,),
            ).fetchone()
        return {"changed": True, "image_id": image_id,
                "role": new_row["role"], "sort_order": new_row["sort_order"]}

    def delete_product(self, product_id: int) -> dict[str, Any]:
        """Delete a tkshop_product row and all its dependents:
        product images (rows + on-disk files), publish logs, and the
        product subdir under PackStore. Returns a small summary.

        NOTE: Does NOT touch TikTok-side data. If the product was already
        published, the listing on TikTok Shop remains live — operator
        should deactivate it via the TikTok seller center separately.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pr.id, pr.publish_status, pr.tiktok_product_id,
                       p.pack_uid
                  FROM tkshop_products pr
                  LEFT JOIN packs p ON p.id = pr.pack_id
                 WHERE pr.id = ?
                """,
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            imgs = conn.execute(
                "SELECT id, local_path FROM tkshop_product_images WHERE product_id = ?",
                (product_id,),
            ).fetchall()

        # Remove image files first (best-effort; row-cascade-by-hand below).
        for i in imgs:
            if i["local_path"]:
                try:
                    Path(i["local_path"]).unlink()
                except Exception:
                    pass

        # Remove on-disk product subfolder if the PackStore knows the pack.
        pack_uid = row["pack_uid"] or ""
        if pack_uid:
            try:
                pdir = self.store.product_dir(pack_uid, f"draft_{product_id}")
                if pdir.is_dir():
                    import shutil as _sh
                    _sh.rmtree(pdir, ignore_errors=True)
                    logger.info("Removed product dir %s", pdir)
            except Exception as e:
                logger.warning("could not remove product dir: %s", e)

        with _open_db(self.db_path) as conn:
            conn.execute("DELETE FROM tkshop_product_images WHERE product_id = ?",
                         (product_id,))
            conn.execute("DELETE FROM tkshop_publish_logs WHERE product_id = ?",
                         (product_id,))
            cur = conn.execute("DELETE FROM tkshop_products WHERE id = ?",
                               (product_id,))
            conn.commit()

        return {
            "deleted": cur.rowcount > 0,
            "product_id": product_id,
            "images_removed": len(imgs),
            "was_published": bool(row["tiktok_product_id"]),
            "tiktok_product_id": row["tiktok_product_id"] or "",
        }

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
    # C.2.b batch upload + pack-preview import
    # ------------------------------------------------------------------

    def add_product_images_batch(
        self,
        product_id: int,
        files: list[tuple[str, bytes]],
    ) -> dict[str, Any]:
        """Save many uploaded files at once. First file becomes 'main'
        if no main exists; remaining files are 'secondary'.
        ``files`` = list of (filename, bytes).
        """
        if not files:
            return {"created": 0, "image_ids": []}
        with _open_db(self.db_path) as conn:
            has_main = conn.execute(
                "SELECT 1 FROM tkshop_product_images WHERE product_id = ? AND role = 'main' LIMIT 1",
                (product_id,),
            ).fetchone() is not None
        created_ids: list[int] = []
        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        try:
            for i, (name, raw) in enumerate(files):
                if not raw:
                    continue
                role = "secondary" if (has_main or i > 0) else "main"
                if not has_main and i == 0:
                    has_main = True  # claimed by first file
                ext = Path(name).suffix.lower() or ".png"
                if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                    ext = ".png"
                tmp = tmpdir / f"upload_{i}{ext}"
                tmp.write_bytes(raw)
                img_id = self.add_product_image(
                    product_id, src_path=tmp, role=role, source="manual",
                )
                created_ids.append(img_id)
        finally:
            try:
                for f in tmpdir.iterdir():
                    f.unlink()
                tmpdir.rmdir()
            except Exception:
                pass
        return {"created": len(created_ids), "image_ids": created_ids}

    def import_pack_previews(self, product_id: int) -> dict[str, Any]:
        """One-click: copy each ok preview from the pack's series into
        product images. First imported = 'main' if none yet, rest =
        'secondary'. Skips previews whose image_path is missing.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pr.id AS product_id, p.id AS pack_id, p.series_id, p.pack_uid
                  FROM tkshop_products pr
                  JOIN packs p ON p.id = pr.pack_id
                 WHERE pr.id = ?
                """,
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            previews = conn.execute(
                """
                SELECT id, preview_idx, image_path
                  FROM pack_previews
                 WHERE series_id = ? AND generation_status = 'ok'
                       AND COALESCE(image_path, '') != ''
                 ORDER BY preview_idx
                """,
                (row["series_id"],),
            ).fetchall() if row["series_id"] else []
        if not previews:
            return {"imported": 0, "skipped": 0, "reason": "no ok previews"}
        files: list[tuple[str, bytes]] = []
        skipped = 0
        for p in previews:
            src = Path(p["image_path"])
            if not src.is_absolute():
                src = Path(p["image_path"])
            try:
                raw = src.read_bytes()
                files.append((f"preview_{p['preview_idx']}{src.suffix or '.png'}", raw))
            except Exception as e:
                logger.warning("import_pack_previews: skip preview #%d (%s): %s",
                               p["id"], src, e)
                skipped += 1
        result = self.add_product_images_batch(product_id, files)
        return {
            "imported": result["created"],
            "skipped": skipped,
            "image_ids": result["image_ids"],
        }

    # ------------------------------------------------------------------
    # C.2.c AI-driven product image design + image-to-image generation
    # ------------------------------------------------------------------

    def collect_pack_design_context(self, product_id: int) -> dict[str, Any]:
        """Gather pack metadata + sample sticker briefs + sample preview
        prompts into a single design-context dict ready for AI synthesis.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pr.id, pr.pack_id,
                       p.display_name, p.total_stickers, p.pack_uid, p.series_id,
                       s.style_anchor, s.palette, s.pack_archetype, s.metadata_json
                  FROM tkshop_products pr
                  JOIN packs p ON p.id = pr.pack_id
             LEFT JOIN pack_series s ON s.id = p.series_id
                 WHERE pr.id = ?
                """, (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            previews = conn.execute(
                """
                SELECT image_path, prompt_text
                  FROM pack_previews
                 WHERE series_id = ? AND generation_status = 'ok'
                 ORDER BY preview_idx
                 LIMIT 6
                """,
                (row["series_id"],),
            ).fetchall() if row["series_id"] else []

        briefs: list[str] = []
        try:
            md = json.loads(row["metadata_json"] or "{}")
            for pb in md.get("preview_briefs", [])[:3]:
                briefs.extend(pb.get("stickers", [])[:6])
        except Exception:
            pass

        return {
            "product_id": product_id,
            "pack_uid": row["pack_uid"] or "",
            "display_name": row["display_name"] or "",
            "pack_archetype": row["pack_archetype"] or "",
            "style_anchor": row["style_anchor"] or "",
            "palette": row["palette"] or "",
            "total_stickers": row["total_stickers"] or 0,
            "briefs_sample": briefs,
            "preview_prompts": [p["prompt_text"] or "" for p in previews],
            "preview_image_paths": [p["image_path"] for p in previews if p["image_path"]],
        }

    def synthesize_image_specs(
        self,
        product_id: int,
        *,
        secondary_count: int = 3,
        language: str = "en",
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run gpt-5.4 (or override) on the design context to produce a
        structured ``{main, secondary[]}`` image-prompt spec.
        """
        ctx = self.collect_pack_design_context(product_id)
        prompt = build_image_design_prompt(
            pack_display_name=ctx["display_name"],
            pack_archetype=ctx["pack_archetype"],
            style_anchor=ctx["style_anchor"],
            palette=ctx["palette"],
            total_stickers=ctx["total_stickers"],
            sticker_briefs_sample=ctx["briefs_sample"],
            preview_prompt_samples=ctx["preview_prompts"],
            secondary_count=secondary_count,
            language=language,
        )
        chosen_model = model or os.getenv("TKSHOP_IMAGE_DESIGN_MODEL", "gpt-5.4")
        main_text = self.router.text_complete(
            prompt,
            model=chosen_model,
            system=IMAGE_DESIGN_SYSTEM_PROMPT,
            temperature=0.6,
            task="tkshop_image_design:main",
            related_table="tkshop_products",
            related_id=product_id,
        )
        spec = self.router.extract_json(
            main_text,
            schema=IMAGE_DESIGN_EXTRACT_SCHEMA,
            instructions=IMAGE_DESIGN_INSTRUCTIONS,
            model=chosen_model,
            max_retries=1,
            task="tkshop_image_design:extract",
            related_table="tkshop_products",
            related_id=product_id,
        )
        return {
            "spec": spec,
            "context": ctx,
            "raw": main_text,
        }

    def auto_design_images(
        self,
        product_id: int,
        *,
        secondary_count: int = 3,
        language: str = "en",
        replace_existing_ai: bool = True,
        size: str = "1024x1024",
    ) -> dict[str, Any]:
        """End-to-end: design context → AI spec → image_edit per concept
        → save as tkshop_product_images rows (source='ai').

        Uses the first available preview image as the visual reference
        for image-to-image. If ``replace_existing_ai`` is True, deletes
        prior source='ai' images for this product before generating new
        ones (manual uploads are kept).
        """
        synth = self.synthesize_image_specs(
            product_id, secondary_count=secondary_count, language=language,
        )
        spec = synth["spec"]
        ctx = synth["context"]

        ref_path = next(
            (Path(p) for p in ctx["preview_image_paths"] if Path(p).exists()),
            None,
        )
        if ref_path is None:
            raise ValueError(
                "no preview image available to use as reference; "
                "generate at least one pack preview first"
            )
        ref_bytes = ref_path.read_bytes()

        if replace_existing_ai:
            with _open_db(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT id, local_path FROM tkshop_product_images "
                    "WHERE product_id = ? AND source = 'ai'",
                    (product_id,),
                ).fetchall()
            for r in rows:
                self.delete_image(r["id"])

        results: list[dict[str, Any]] = []
        concepts: list[tuple[str, dict[str, str]]] = []
        if isinstance(spec.get("main"), dict):
            concepts.append(("main", spec["main"]))
        for sec in (spec.get("secondary") or [])[:secondary_count]:
            if isinstance(sec, dict):
                concepts.append(("secondary", sec))

        # Concurrency: JieKou /v3/gpt-image-2-edit drops connections when
        # >= 3 simultaneous calls hit it (verified during W3.3 smoke).
        # Default to 2; configurable via env.
        max_workers = max(1, min(
            int(os.getenv("TKSHOP_IMAGE_GEN_CONCURRENCY", "2")),
            len(concepts) or 1,
        ))

        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        tmpdir = Path(tempfile.mkdtemp())

        def _gen_one(idx_role_concept: tuple[int, str, dict[str, str]]) -> dict[str, Any]:
            idx, role, c = idx_role_concept
            concept_label = (c.get("concept") or "").strip()[:80]
            image_prompt = (c.get("image_prompt") or "").strip()
            if not image_prompt:
                return {"idx": idx, "role": role, "ok": False,
                        "error": "empty image_prompt", "concept": concept_label}
            try:
                out_bytes = self.router.image_edit(
                    ref_bytes,
                    image_prompt,
                    size=size,
                    task="tkshop_image_design:edit",
                    related_table="tkshop_products",
                    related_id=product_id,
                )
            except APIError as e:
                return {"idx": idx, "role": role, "ok": False,
                        "error": str(e)[:300], "concept": concept_label,
                        "image_prompt": image_prompt}
            tmp = tmpdir / f"ai_{role}_{idx}_{int(time.time()*1000)}.png"
            tmp.write_bytes(out_bytes)
            return {"idx": idx, "role": role, "ok": True,
                    "tmp_path": tmp, "concept": concept_label,
                    "image_prompt": image_prompt}

        try:
            t0 = time.time()
            tasks = list(enumerate(concepts))  # [(idx, role, concept), ...]
            tasks_packed = [(i, r, c) for i, (r, c) in tasks]
            logger.info(
                "auto_design_images: dispatching %d concepts with workers=%d",
                len(tasks_packed), max_workers,
            )
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                # Preserve input order in results for stable sort_order.
                gen_results = list(ex.map(_gen_one, tasks_packed))
            logger.info(
                "auto_design_images: parallel gen done in %.1fs", time.time() - t0,
            )

            # DB writes sequential to keep sort_order monotonic per role.
            for r in gen_results:
                if not r.get("ok"):
                    results.append({k: v for k, v in r.items()
                                    if k not in ("tmp_path", "image_prompt")})
                    continue
                img_id = self.add_product_image(
                    product_id, src_path=r["tmp_path"],
                    role=r["role"], source="ai",
                    ai_prompt=r["image_prompt"],
                )
                results.append({"role": r["role"], "ok": True,
                                "image_id": img_id, "concept": r["concept"]})
        finally:
            try:
                for f in tmpdir.iterdir():
                    f.unlink()
                tmpdir.rmdir()
            except Exception:
                pass

        return {
            "product_id": product_id,
            "generated": sum(1 for r in results if r.get("ok")),
            "failed": sum(1 for r in results if not r.get("ok")),
            "results": results,
            "reference_preview": ref_path.as_posix(),
            "spec": spec,
        }

    # ------------------------------------------------------------------
    # C.3 publish — POST to multi-channel-api sticker_publish endpoint
    # ------------------------------------------------------------------

    def publish(
        self,
        product_id: int,
        *,
        dry_run: bool = False,
        auto_activate: bool = True,
    ) -> dict[str, Any]:
        """POST a sticker_publish payload to multi-channel-api.

        Endpoint: ``POST {TKSHOP_SERVER_URL}/api/v1/tiktok/products/sticker_publish``

        Request shape (FIXED contract — see the multi-channel-api repo)::

          {
            "title": str,
            "description_html": str,
            "category_id": str,
            "seller_sku": str,
            "quantity": int,
            "image_urls": [str, ...]
          }

        Success response::
            {"ok": true, "product_id": "...", "sku_id": "...",
             "seller_sku": "...", "status": "LIVE"}

        Failure response::
            {"ok": false, "error_code": "TT_xxxx",
             "error_message": "...", "field_hints": ["title", ...]}

        ``dry_run=True`` builds the payload + writes the publish_log row
        but does NOT make the HTTP call.
        """
        product = self._build_publish_payload(product_id)
        product["auto_activate"] = bool(auto_activate)

        attempt_idx = 1
        with _open_db(self.db_path) as conn:
            attempt_idx = (conn.execute(
                "SELECT COALESCE(MAX(attempt_idx), 0) + 1 FROM tkshop_publish_logs WHERE product_id = ?",
                (product_id,),
            ).fetchone()[0]) or 1

        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/sticker_publish"
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
        field_hints: list[str] = []
        tiktok_product_id = ""
        tiktok_sku_id = ""
        tiktok_status = ""
        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE tkshop_products SET publish_status = 'publishing' WHERE id = ?",
                (product_id,),
            )
            conn.commit()
        try:
            resp = requests.post(endpoint, json=product,
                                 timeout=TKSHOP_SERVER_TIMEOUT)
            try:
                resp_json = resp.json()
            except Exception:
                resp_json = {"_raw_text": resp.text[:500]}
            # multi-channel-api wraps business payload in
            # {success, message, data: {...}}. Unwrap so downstream code
            # reads the inner contract directly.
            if isinstance(resp_json, dict) and "data" in resp_json and isinstance(resp_json["data"], dict) and "ok" in resp_json["data"]:
                resp_json = resp_json["data"]
            if 200 <= resp.status_code < 300 and resp_json.get("ok") is True:
                success = True
                tiktok_product_id = str(resp_json.get("product_id") or "")
                tiktok_sku_id = str(resp_json.get("sku_id") or "")
                tiktok_status = str(resp_json.get("status") or "").strip()
                # multi-channel-api now returns ok=true even when activate
                # fails (product was created → not a payload bug → DON'T
                # let self-heal duplicate the listing). Surface the activate
                # error via error_message so the UI can show it.
                ae = resp_json.get("activate_error") or {}
                if isinstance(ae, dict) and ae:
                    error_code = str(ae.get("code") or "TT_ACTIVATE_FAILED")
                    error_message = (
                        f"商品已创建（id={tiktok_product_id}）但 activate 失败 — "
                        f"{ae.get('note') or ''} 详情：{ae.get('message') or ''}"
                    )[:500]
            else:
                # Either non-2xx HTTP or {ok:false} body. Surface whichever
                # error info we have.
                error_code = str(resp_json.get("error_code") or resp.status_code)
                error_message = str(
                    resp_json.get("error_message")
                    or resp_json.get("error")
                    or resp.text[:200]
                )
                hints_raw = resp_json.get("field_hints") or []
                if isinstance(hints_raw, list):
                    field_hints = [str(h) for h in hints_raw]
        except requests.RequestException as e:
            error_code = "NETWORK"
            error_message = str(e)[:300]
        except Exception as e:
            error_code = "INTERNAL"
            error_message = f"{type(e).__name__}: {e}"[:300]

        # Persist any field_hints into the response body so the publish
        # log row carries them through (response_payload is queried by UI).
        if not success and field_hints and isinstance(resp_json, dict):
            resp_json.setdefault("field_hints", field_hints)

        self._log_publish_attempt(
            product_id, attempt_idx, endpoint, product,
            response=resp_json, success=success,
            error_code=error_code, error_message=error_message,
        )

        with _open_db(self.db_path) as conn:
            if success:
                # Honor TikTok-side status: LIVE → 'published', DRAFT → 'draft_on_platform'.
                ts = (tiktok_status or "").upper()
                local_status = (
                    "draft_on_platform" if ts == "DRAFT"
                    else "pending"      if ts == "PENDING"
                    else "published"
                )
                conn.execute(
                    """
                    UPDATE tkshop_products
                       SET publish_status = ?,
                           tiktok_product_id = ?,
                           tiktok_sku_id = ?,
                           published_at = ?
                     WHERE id = ?
                    """,
                    (local_status, tiktok_product_id, tiktok_sku_id,
                     int(time.time()), product_id),
                )
            else:
                conn.execute(
                    "UPDATE tkshop_products SET publish_status = 'failed' WHERE id = ?",
                    (product_id,),
                )
            conn.commit()

        return {
            "ok": success,
            "tiktok_product_id": tiktok_product_id,
            "tiktok_sku_id": tiktok_sku_id,
            "tiktok_status": tiktok_status,
            "error_code": error_code,
            "error_message": error_message,
            "field_hints": field_hints,
            "attempt_idx": attempt_idx,
        }

    def publish_with_self_heal(
        self,
        product_id: int,
        *,
        max_retries: Optional[int] = None,
        dry_run: bool = False,
        auto_activate: bool = True,
    ) -> dict[str, Any]:
        """Publish, and if it fails ask the AI to fix the payload + retry.

        ``max_retries`` is the total attempt cap (default
        ``TKSHOP_PUBLISH_MAX_RETRIES``). The first attempt counts as 1, so
        the AI fix loop runs at most ``max_retries - 1`` times.

        Returns the last attempt's result dict, plus an ``attempts`` list
        carrying every individual try (so the UI can show what AI changed
        and why).
        """
        if not TKSHOP_PUBLISH_AUTO_FIX_DEFAULT and max_retries is None:
            # Auto-fix disabled at the env layer and caller didn't override.
            # Behave exactly like a single publish().
            single = self.publish(product_id, dry_run=dry_run, auto_activate=auto_activate)
            single["attempts"] = [single]
            return single

        cap = max_retries if max_retries is not None else TKSHOP_PUBLISH_MAX_RETRIES_DEFAULT
        cap = max(1, int(cap))

        # Guard: if this product already has a tiktok_product_id, the
        # listing exists on TikTok — never let self-heal POST sticker_publish
        # again, that would create a duplicate. Use update_on_platform / activate
        # instead.
        with _open_db(self.db_path) as conn:
            existing_tt = (conn.execute(
                "SELECT tiktok_product_id FROM tkshop_products WHERE id = ?",
                (product_id,),
            ).fetchone() or [""])[0] or ""
        if existing_tt and not dry_run:
            logger.warning(
                "publish_with_self_heal: product #%d already has tiktok_product_id=%s; "
                "skipping create-loop. Use /update_on_platform or /activate instead.",
                product_id, existing_tt,
            )
            return {
                "ok": True,
                "tiktok_product_id": existing_tt,
                "message": "product already exists on TikTok — no new create issued",
                "attempts": [],
                "skipped_create": True,
            }

        attempts: list[dict[str, Any]] = []
        last: dict[str, Any] = {}
        for i in range(cap):
            last = self.publish(product_id, dry_run=dry_run, auto_activate=auto_activate)
            attempts.append(last)
            if last.get("ok"):
                break
            # If publish() returned a tiktok_product_id (rare, but possible if
            # the response shape carried it on a "soft" failure), treat as
            # done — we've created the listing, don't recreate.
            if last.get("tiktok_product_id"):
                logger.info(
                    "publish_with_self_heal: stopping retry — product was created "
                    "(tiktok_product_id=%s) even though ok=false",
                    last.get("tiktok_product_id"),
                )
                last["skipped_further_retries"] = True
                break
            # NETWORK / timeout / non-payload errors → DO NOT retry. The
            # product may have been created server-side; recreating would
            # duplicate. Operator should sync platform status manually.
            ec = (last.get("error_code") or "").upper()
            if ec in ("NETWORK", "INTERNAL", "TT_IMAGE_UPLOAD") or ec.isdigit() and int(ec) >= 500:
                logger.warning(
                    "publish_with_self_heal: stopping retry — error_code=%s "
                    "is not a payload-fixable issue (likely transport / upstream).",
                    ec,
                )
                last["skipped_further_retries"] = True
                last["skip_reason"] = f"non-retryable error_code={ec}"
                break
            if i == cap - 1:
                # No retry budget left.
                break
            # Ask the AI to patch the payload, then loop.
            try:
                fix = self._ai_fix_payload(
                    product_id,
                    error_code=str(last.get("error_code") or ""),
                    error_message=str(last.get("error_message") or ""),
                    field_hints=list(last.get("field_hints") or []),
                )
            except Exception as e:
                logger.warning(
                    "self-heal AI fix failed for product #%d attempt %d: %s",
                    product_id, i + 1, e,
                )
                last["self_heal_error"] = str(e)[:300]
                break
            if not fix.get("changed_fields"):
                # AI didn't propose any change; pointless to retry the
                # exact same payload.
                last["self_heal_error"] = "AI returned no changes"
                break

        last["attempts"] = attempts
        return last

    def _ai_fix_payload(
        self,
        product_id: int,
        *,
        error_code: str,
        error_message: str,
        field_hints: list[str],
    ) -> dict[str, Any]:
        """Call the AI to rewrite the failing fields, then apply the patch.

        Returns ``{"changed_fields": [...], "rationale": "..."}``.
        """
        failed_payload = self._build_publish_payload(product_id)
        prompt = build_self_heal_prompt(
            failed_payload=failed_payload,
            error_code=error_code,
            error_message=error_message,
            field_hints=field_hints,
        )
        # extract_json is happy to do a one-shot JSON-mode call; the
        # "source text" is just the prompt itself (the AI rewrites the
        # fields based on the failure context).
        patch = self.router.extract_json(
            prompt,
            schema=SELF_HEAL_EXTRACT_SCHEMA,
            instructions=SELF_HEAL_INSTRUCTIONS + "\n\n" + SELF_HEAL_SYSTEM_PROMPT,
            max_retries=1,
            task="tkshop_publish:self_heal",
            related_table="tkshop_products",
            related_id=product_id,
        )

        new_title = (patch.get("title") or "").strip()
        new_desc = patch.get("description_html") or ""
        new_sku = (patch.get("seller_sku") or "").strip().upper()
        rationale = (patch.get("rationale") or "").strip()

        changed: list[str] = []
        if new_title:
            self.update_detail_manual(product_id, title=_clamp_title(new_title))
            changed.append("title")
        if new_desc:
            self.update_detail_manual(product_id, description_html=new_desc)
            changed.append("description_html")
        if new_sku and _is_valid_seller_sku(new_sku):
            self.set_seller_sku(product_id, new_sku)
            changed.append("seller_sku")

        # Append a one-line diff entry so operators can audit what AI did.
        diff_line = (
            f"[{int(time.time())}] code={error_code or '?'} "
            f"changed={','.join(changed) or '(none)'} :: {rationale[:200]}"
        )
        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE tkshop_products
                   SET auto_fix_attempts = COALESCE(auto_fix_attempts, 0) + 1,
                       last_fix_diff = CASE
                           WHEN COALESCE(last_fix_diff, '') = '' THEN ?
                           ELSE last_fix_diff || char(10) || ?
                       END
                 WHERE id = ?
                """,
                (diff_line, diff_line, product_id),
            )
            conn.commit()

        return {
            "changed_fields": changed,
            "rationale": rationale,
            "diff_line": diff_line,
        }

    def _build_publish_payload(self, product_id: int) -> dict[str, Any]:
        """Build the multi-channel-api ``sticker_publish`` request body.

        Shape (do not change without the multi-channel-api side):
            {
              "title": str,
              "description_html": str,
              "category_id": str,
              "seller_sku": str,
              "quantity": int,
              "image_urls": [str, ...]
            }
        """
        with _open_db(self.db_path) as conn:
            pr = conn.execute(
                """
                SELECT pr.id, pr.title, pr.description_html, pr.category_id,
                       pr.seller_sku, pr.tiktok_product_id, pr.tiktok_sku_id,
                       p.display_name, p.pack_uid, p.total_stickers
                  FROM tkshop_products pr
                  JOIN packs p ON p.id = pr.pack_id
                 WHERE pr.id = ?
                """, (product_id,),
            ).fetchone()
            if not pr:
                raise ValueError(f"product #{product_id} not found")
            imgs = conn.execute(
                """
                SELECT role, local_path, sort_order
                  FROM tkshop_product_images
                 WHERE product_id = ?
                 ORDER BY role = 'main' DESC, sort_order ASC, id ASC
                """, (product_id,),
            ).fetchall()

        # Build image_urls AND image_paths. Same-host deployment: mca prefers
        # image_paths (zero HTTP roundtrip — no ReadError on busy sticker app).
        # image_urls are kept as fallback for off-box deployments.
        image_urls: list[str] = []
        image_paths: list[str] = []
        for i in imgs:
            local = i["local_path"] or ""
            if not local:
                continue
            url = _local_path_to_public_url(local)
            if url:
                image_urls.append(url)
            # Absolute path so mca can resolve regardless of its own cwd.
            abs_path = local if os.path.isabs(local) else os.path.abspath(local)
            if os.path.isfile(abs_path):
                image_paths.append(abs_path)

        # Quantity: ship 100 per SKU by default. Operator can tune via
        # TKSHOP_DEFAULT_QUANTITY without touching code.
        quantity = int(os.getenv("TKSHOP_DEFAULT_QUANTITY", "100"))

        seller_sku = pr["seller_sku"] or ""
        if not seller_sku:
            # Fallback if AI/extract step never populated it.
            seller_sku = compute_default_seller_sku(
                pack_uid=pr["pack_uid"] or "",
                total_stickers=pr["total_stickers"] or 0,
                pack_id=product_id,
            )

        # Defensive clamp: TikTok / multi-channel-api enforce 100-char title.
        return {
            "title": _clamp_title(pr["title"] or ""),
            "description_html": pr["description_html"] or "",
            "category_id": pr["category_id"] or "928016",
            "seller_sku": seller_sku,
            "quantity": quantity,
            "image_urls": image_urls,
            "image_paths": image_paths,
            "auto_activate": True,  # caller may override
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

    # ------------------------------------------------------------------
    # Platform browse + push update (already-published products)
    # ------------------------------------------------------------------

    def list_platform_products(
        self,
        *,
        page_size: int = 20,
        page_token: str = "",
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        """Search products that exist on TikTok Shop via multi-channel-api
        ``POST /api/v1/tiktok/products/search``. Returns unwrapped payload.

        The multi-channel-api response is the ``ApiResponse`` envelope
        ``{success, message, data, detail?}``. ``success=false`` typically
        means the upstream TikTok call failed (e.g. expired access token).
        We surface ``message`` + ``detail`` as ``error`` so the UI shows
        a real reason instead of "未知错误".
        """
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/search"
        body: dict[str, Any] = {"page_size": int(page_size), "page_token": page_token or ""}
        if status:
            body["status"] = status
        try:
            resp = requests.post(endpoint, json=body, timeout=TKSHOP_SERVER_TIMEOUT)
            data = resp.json() if resp.text else {}
        except requests.RequestException as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "products": [], "endpoint": endpoint}

        envelope_success = bool(data.get("success", True)) if isinstance(data, dict) else True
        message = (data.get("message") or "") if isinstance(data, dict) else ""
        detail  = (data.get("detail")  or "") if isinstance(data, dict) else ""

        # Envelope-level failure (HTTP 200 but success=false, often expired token)
        if not envelope_success or not resp.ok:
            err_parts = [message or f"HTTP {resp.status_code}"]
            if detail:
                err_parts.append(detail[:300])
            return {
                "ok": False,
                "error": " | ".join(err_parts),
                "products": [],
                "endpoint": endpoint,
            }

        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            inner = data if isinstance(data, dict) else {}
        return {
            "ok": True,
            "raw": inner,
            "products": inner.get("products") or inner.get("product_list") or [],
            "next_page_token": inner.get("next_page_token") or "",
            "total_count": inner.get("total_count") or 0,
            "endpoint": endpoint,
        }

    def get_platform_product(self, tiktok_product_id: str) -> dict[str, Any]:
        endpoint = (
            f"{TKSHOP_SERVER_URL.rstrip('/')}"
            f"/api/v1/tiktok/products/{tiktok_product_id}"
        )
        try:
            resp = requests.get(endpoint, timeout=TKSHOP_SERVER_TIMEOUT)
            data = resp.json() if resp.text else {}
        except requests.RequestException as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict):
            inner = data if isinstance(data, dict) else {}
        return {"ok": resp.ok, "data": inner, "endpoint": endpoint}

    def update_on_platform(self, product_id: int) -> dict[str, Any]:
        """Push local edits to TikTok for an already-published product.
        Reuses the same payload builder as ``publish``.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT tiktok_product_id FROM tkshop_products WHERE id = ?",
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            tt_id = (row["tiktok_product_id"] or "").strip()
        if not tt_id:
            return {"ok": False, "error_code": "NOT_PUBLISHED",
                    "error_message": "product has no tiktok_product_id; publish first"}

        payload = self._build_publish_payload(product_id)
        endpoint = (
            f"{TKSHOP_SERVER_URL.rstrip('/')}"
            f"/api/v1/tiktok/products/{tt_id}/sticker_update"
        )

        attempt_idx = 1
        with _open_db(self.db_path) as conn:
            attempt_idx = (conn.execute(
                "SELECT COALESCE(MAX(attempt_idx), 0) + 1 FROM tkshop_publish_logs WHERE product_id = ?",
                (product_id,),
            ).fetchone()[0]) or 1

        resp_json: dict[str, Any] = {}
        success = False
        error_code = ""
        error_message = ""
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
            else:
                error_code = str(resp_json.get("error_code") or resp.status_code)
                error_message = str(resp_json.get("error_message") or resp.text[:200])
        except requests.RequestException as e:
            error_code = "NETWORK"; error_message = str(e)[:300]
        except Exception as e:
            error_code = "INTERNAL"; error_message = f"{type(e).__name__}: {e}"[:300]

        self._log_publish_attempt(
            product_id, attempt_idx, endpoint, payload,
            response=resp_json, success=success,
            error_code=error_code, error_message=error_message,
        )
        return {
            "ok": success,
            "tiktok_product_id": tt_id,
            "error_code": error_code,
            "error_message": error_message,
            "attempt_idx": attempt_idx,
        }

    def _post_platform_action(self, product_id: int, action: str) -> dict[str, Any]:
        """Call POST /api/v1/tiktok/products/{tt_id}/{action} (activate /
        deactivate / recover). Returns ``{ok, error_code?, error_message?}``.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT tiktok_product_id FROM tkshop_products WHERE id = ?",
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            tt_id = (row["tiktok_product_id"] or "").strip()
        if not tt_id:
            return {"ok": False, "error_code": "NOT_PUBLISHED",
                    "error_message": "product has no tiktok_product_id"}
        endpoint = (
            f"{TKSHOP_SERVER_URL.rstrip('/')}"
            f"/api/v1/tiktok/products/{tt_id}/{action}"
        )
        try:
            resp = requests.post(endpoint, timeout=TKSHOP_SERVER_TIMEOUT)
            data = resp.json() if resp.text else {}
        except requests.RequestException as e:
            return {"ok": False, "error_code": "NETWORK",
                    "error_message": f"{type(e).__name__}: {e}"}
        env_ok = bool(data.get("success", True)) if isinstance(data, dict) else True
        msg = (data.get("message") or "") if isinstance(data, dict) else ""
        det = (data.get("detail")  or "") if isinstance(data, dict) else ""
        if not env_ok or not resp.ok:
            raw = (msg or "") + (f" | {det}" if det else "")
            friendly = _friendly_platform_error(action, raw)
            return {"ok": False,
                    "error_code": str(resp.status_code),
                    "error_message": friendly,
                    "raw_error": raw}
        return {"ok": True, "data": data.get("data") or {}}

    def activate_on_platform(self, product_id: int) -> dict[str, Any]:
        result = self._post_platform_action(product_id, "activate")
        if result.get("ok"):
            with _open_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE tkshop_products SET publish_status = 'published' WHERE id = ?",
                    (product_id,),
                )
                conn.commit()
        return result

    def deactivate_on_platform(self, product_id: int) -> dict[str, Any]:
        result = self._post_platform_action(product_id, "deactivate")
        if result.get("ok"):
            with _open_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE tkshop_products SET publish_status = 'suspended' WHERE id = ?",
                    (product_id,),
                )
                conn.commit()
        return result

    def sync_one_status(self, product_id: int) -> dict[str, Any]:
        """Refresh the publish_status for a single product from TikTok."""
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT tiktok_product_id, publish_status FROM tkshop_products WHERE id = ?",
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            tt_id = (row["tiktok_product_id"] or "").strip()
        if not tt_id:
            return {"ok": False, "error_message": "no tiktok_product_id"}
        result = self.get_platform_product(tt_id)
        if not result.get("ok"):
            return {"ok": False, "error_message": result.get("error", "fetch failed")}
        data = result.get("data") or {}
        remote = (data.get("status") or "").strip().lower()
        if remote and remote != (row["publish_status"] or "").lower():
            with _open_db(self.db_path) as conn:
                conn.execute(
                    "UPDATE tkshop_products SET publish_status = ? WHERE id = ?",
                    (remote, product_id),
                )
                conn.commit()
        return {"ok": True, "remote_status": remote, "previous": row["publish_status"]}

    def sync_statuses(self) -> dict[str, Any]:
        """C.4 — query multi-channel-api for status of every published product.

        Hits ``GET {TKSHOP_SERVER_URL}/api/v1/tiktok/products/{tiktok_product_id}``
        which returns an ``ApiResponse`` envelope ``{success, message, data}``.
        TikTok status (``data.status``) values: DRAFT, PENDING, LIVE,
        SUSPENDED, DELETED. We normalize to lowercase and write to
        ``publish_status``.

        Returns ``{'checked': n, 'updated': n, 'errors': [...]}``.
        """
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, tiktok_product_id, publish_status
                  FROM tkshop_products
                 WHERE publish_status NOT IN ('draft', 'failed')
                   AND tiktok_product_id != ''
                """,
            ).fetchall()
        if not rows:
            return {"checked": 0, "updated": 0, "errors": []}

        updated, errors = 0, []
        for r in rows:
            url = (
                f"{TKSHOP_SERVER_URL.rstrip('/')}"
                f"/api/v1/tiktok/products/{r['tiktok_product_id']}"
            )
            try:
                resp = requests.get(url, timeout=TKSHOP_SERVER_TIMEOUT)
                if not resp.ok:
                    errors.append({"product_id": r["id"], "error": f"HTTP {resp.status_code}"})
                    continue
                body = resp.json() if resp.text else {}
                # ApiResponse envelope: {success, message, data: {...}}
                data = body.get("data") if isinstance(body, dict) else None
                if not isinstance(data, dict):
                    data = body if isinstance(body, dict) else {}
                remote = (data.get("status") or "").strip().lower()
                if remote and remote != (r["publish_status"] or "").lower():
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
                       error_code, error_message, response_payload, created_at
                  FROM tkshop_publish_logs
                 WHERE product_id = ?
                 ORDER BY id DESC
                """, (product_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # Best-effort parse of response_payload so the UI can read field_hints.
            try:
                d["response_obj"] = json.loads(d.get("response_payload") or "{}")
            except Exception:
                d["response_obj"] = {}
            out.append(d)
        return out

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_products(
        self,
        *,
        pack_id: Optional[int] = None,
        publish_status: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 100,
    ) -> tuple[list[dict], int]:
        clauses, params = [], []
        if pack_id is not None:
            clauses.append("pr.pack_id = ?"); params.append(pack_id)
        if publish_status and publish_status != "all":
            clauses.append("pr.publish_status = ?"); params.append(publish_status)
        if q:
            kw = f"%{q.strip()}%"
            clauses.append(
                "(pr.title LIKE ? OR p.display_name LIKE ? "
                "OR pr.seller_sku LIKE ? OR pr.tiktok_product_id LIKE ?)"
            )
            params.extend([kw, kw, kw, kw])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _open_db(self.db_path) as conn:
            # COUNT must JOIN too because the keyword filter references p.display_name.
            total = conn.execute(
                f"""SELECT COUNT(*) FROM tkshop_products pr
                    LEFT JOIN packs p ON p.id = pr.pack_id
                    {where}""",
                tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT pr.id, pr.pack_id, pr.tiktok_product_id, pr.tiktok_sku_id,
                       pr.seller_sku, pr.title,
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

    def export_products_rows(self, product_ids: list[int]) -> list[dict]:
        """Pull export-ready dicts for the given product ids. One row per
        product, with images joined as a list of local_path strings.
        Returns rows in input order (skips ids that don't exist).
        """
        if not product_ids:
            return []
        ph = ",".join(["?"] * len(product_ids))
        with _open_db(self.db_path) as conn:
            prods = conn.execute(
                f"""
                SELECT pr.id, pr.pack_id, pr.tiktok_product_id, pr.tiktok_sku_id,
                       pr.seller_sku, pr.title, pr.description_html,
                       pr.selling_points, pr.keywords, pr.category_id,
                       pr.publish_status, pr.created_at, pr.published_at,
                       p.display_name AS pack_name, p.pack_uid, p.total_stickers
                  FROM tkshop_products pr
             LEFT JOIN packs           p ON p.id = pr.pack_id
                 WHERE pr.id IN ({ph})
                """,
                tuple(product_ids),
            ).fetchall()
            order = {pid: i for i, pid in enumerate(product_ids)}
            prods = sorted([dict(r) for r in prods], key=lambda r: order.get(r["id"], 1e9))
            for pr in prods:
                imgs = conn.execute(
                    """SELECT id, role, source, local_path, sort_order, ai_prompt
                         FROM tkshop_product_images
                        WHERE product_id = ?
                        ORDER BY role = 'main' DESC, sort_order ASC, id ASC""",
                    (pr["id"],),
                ).fetchall()
                pr["images"] = [dict(i) for i in imgs]
                # Decode JSON columns for spreadsheet display
                try:
                    pr["selling_points_list"] = json.loads(pr["selling_points"] or "[]")
                except Exception:
                    pr["selling_points_list"] = []
                try:
                    pr["keywords_list"] = json.loads(pr["keywords"] or "[]")
                except Exception:
                    pr["keywords_list"] = []
        return prods

    def export_products_xlsx(self, product_ids: list[int]) -> bytes:
        """XLSX with embedded main image + all metadata. One row per product."""
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.drawing.image import Image as XLImage

        rows = self.export_products_rows(product_ids)
        wb = Workbook()
        ws = wb.active
        ws.title = "TKShop Products"

        headers = [
            "ID", "Main Image", "Title", "Seller SKU",
            "TikTok Product ID", "TikTok SKU ID", "Status",
            "Pack Name", "Pack UID", "Total Stickers",
            "Category ID", "Selling Points", "Keywords",
            "Image Count", "All Image Paths",
            "Created", "Published",
        ]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="4F46E5")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 22

        # Reasonable column widths
        widths = [6, 22, 60, 22, 22, 22, 14, 32, 32, 12, 12, 60, 50, 8, 60, 18, 18]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[chr(64 + i) if i <= 26 else "A" + chr(64 + i - 26)].width = w

        for r_idx, pr in enumerate(rows, start=2):
            ws.row_dimensions[r_idx].height = 110
            ws.cell(row=r_idx, column=1, value=pr["id"])
            ws.cell(row=r_idx, column=3, value=pr.get("title") or "")
            ws.cell(row=r_idx, column=4, value=pr.get("seller_sku") or "")
            ws.cell(row=r_idx, column=5, value=pr.get("tiktok_product_id") or "")
            ws.cell(row=r_idx, column=6, value=pr.get("tiktok_sku_id") or "")
            ws.cell(row=r_idx, column=7, value=pr.get("publish_status") or "")
            ws.cell(row=r_idx, column=8, value=pr.get("pack_name") or "")
            ws.cell(row=r_idx, column=9, value=pr.get("pack_uid") or "")
            ws.cell(row=r_idx, column=10, value=pr.get("total_stickers") or 0)
            ws.cell(row=r_idx, column=11, value=pr.get("category_id") or "")
            ws.cell(row=r_idx, column=12,
                    value="\n".join(pr.get("selling_points_list") or []))
            ws.cell(row=r_idx, column=13,
                    value=", ".join(pr.get("keywords_list") or []))
            imgs = pr.get("images") or []
            ws.cell(row=r_idx, column=14, value=len(imgs))
            ws.cell(row=r_idx, column=15,
                    value="\n".join(i.get("local_path") or "" for i in imgs))

            # Created / Published timestamps as readable strings
            from datetime import datetime as _dt
            ts = pr.get("created_at")
            ws.cell(row=r_idx, column=16,
                    value=_dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "")
            ts = pr.get("published_at")
            ws.cell(row=r_idx, column=17,
                    value=_dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "")

            # Wrap text cells
            for col in (3, 12, 13, 15):
                ws.cell(row=r_idx, column=col).alignment = Alignment(
                    wrap_text=True, vertical="top",
                )

            # Embed main image (column B = 2)
            main_img = next((i for i in imgs if (i.get("role") or "") == "main"),
                            imgs[0] if imgs else None)
            if main_img and main_img.get("local_path"):
                ip = Path(main_img["local_path"])
                if ip.is_file():
                    try:
                        xl_img = XLImage(str(ip))
                        # Resize to fit within ~140x140 px in cell
                        target = 140
                        xl_img.width = target
                        xl_img.height = target
                        anchor_cell = f"B{r_idx}"
                        ws.add_image(xl_img, anchor_cell)
                    except Exception as e:  # noqa: BLE001
                        ws.cell(row=r_idx, column=2,
                                value=f"(image embed failed: {type(e).__name__})")
                else:
                    ws.cell(row=r_idx, column=2,
                            value=f"(missing: {main_img['local_path']})")
            else:
                ws.cell(row=r_idx, column=2, value="(no image)")

        # Header freeze
        ws.freeze_panes = "C2"

        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def export_products_zip(self, product_ids: list[int]) -> bytes:
        """ZIP containing products.csv + images/ folder with all product images.
        Image filenames: ``{seller_sku or product_id}__{role}_{sort_order}{ext}``.
        """
        import csv
        from io import BytesIO, StringIO
        import zipfile

        rows = self.export_products_rows(product_ids)
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # CSV
            sio = StringIO()
            writer = csv.writer(sio)
            writer.writerow([
                "id", "title", "seller_sku", "tiktok_product_id", "tiktok_sku_id",
                "publish_status", "pack_name", "pack_uid", "total_stickers",
                "category_id", "selling_points", "keywords",
                "image_files", "created_at", "published_at",
            ])
            for pr in rows:
                imgs = pr.get("images") or []
                key = (pr.get("seller_sku") or f"P{pr['id']}").strip()
                file_names = []
                for i in imgs:
                    src = i.get("local_path") or ""
                    if not src:
                        continue
                    sp = Path(src)
                    if not sp.is_file():
                        continue
                    ext = sp.suffix or ".png"
                    role = i.get("role") or "img"
                    so = i.get("sort_order") or 0
                    out_name = f"images/{key}__{role}_{so}{ext}"
                    try:
                        zf.write(str(sp), arcname=out_name)
                        file_names.append(out_name)
                    except Exception:  # noqa: BLE001
                        pass
                writer.writerow([
                    pr["id"], pr.get("title") or "", pr.get("seller_sku") or "",
                    pr.get("tiktok_product_id") or "", pr.get("tiktok_sku_id") or "",
                    pr.get("publish_status") or "", pr.get("pack_name") or "",
                    pr.get("pack_uid") or "", pr.get("total_stickers") or 0,
                    pr.get("category_id") or "",
                    " | ".join(pr.get("selling_points_list") or []),
                    ", ".join(pr.get("keywords_list") or []),
                    " | ".join(file_names),
                    pr.get("created_at") or "", pr.get("published_at") or "",
                ])
            zf.writestr("products.csv", sio.getvalue())
        return buf.getvalue()

    def get_product(self, product_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                """
                SELECT pr.id, pr.pack_id, pr.tiktok_product_id, pr.tiktok_sku_id,
                       pr.seller_sku, pr.auto_fix_attempts, pr.last_fix_diff,
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
            d["auto_fix_default"] = TKSHOP_PUBLISH_AUTO_FIX_DEFAULT
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
