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


def attr_value_en(name: str, fallback: str) -> str:
    """English-only sales-attribute value (TikTok forbids non-Latin chars in
    attribute values). Pack names are bilingual '中文 / English' — take the
    English part, strip any remaining non-ASCII, cap at 100 chars."""
    s = name or ""
    if "/" in s:
        # Pack names are bilingual but the order varies ("中文 / English" AND
        # "English / 中文") — pick the side with the most ASCII letters rather
        # than assuming English comes last.
        s = max(s.split("/"),
                key=lambda p: sum(1 for c in p if c.isascii() and c.isalpha()))
    s = re.sub(r"[^\x00-\x7F]+", "", s)            # drop non-ASCII (e.g. Chinese)
    s = re.sub(r"\s+", " ", s).strip(" -/|")
    return (s or fallback)[:100]


def make_seller_sku(*, prefix: str = "INK", topic_slug: str, pack_slug: str) -> str:
    parts = [prefix, _slug(topic_slug, 8), _slug(pack_slug, 10)]
    return "-".join(p for p in parts if p)[:50]


def pack_seller_sku(pack_uid_or_name: str, pack_id: int) -> str:
    """Unique per-pack seller_sku ``INK-{name_slug}-{pack_id}`` — pack_id (PK)
    guarantees uniqueness so two same-day / similarly-named packs never collide
    (the topic+pack-slug form could)."""
    s = re.sub(r"^\d{8}_", "", pack_uid_or_name or "")
    s = re.sub(r"_[0-9a-fA-F]{4}$", "", s)
    slug = re.sub(r"[^A-Za-z0-9]", "", s).upper()[:12] or f"P{pack_id}"
    return f"INK-{slug}-{pack_id}"[:50]


def _ascii_prompt_text(text: Any, max_len: int = 500) -> str:
    s = str(text or "")
    s = re.sub(r"[^\x00-\x7F]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -/|")
    return s[:max_len]


def _clean_list(value: Any, *, limit: int, item_max: int = 90) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[\n;,]+", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = []
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        s = re.sub(r"\s+", " ", str(item or "")).strip()
        s = re.sub(r"^\s*(?:[-*#]+|\d+[.)])\s*", "", s).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s[:item_max].strip())
        if len(out) >= limit:
            break
    return out


def _clean_description_html(value: Any) -> str:
    html = str(value or "").strip()
    html = re.sub(r"^```(?:html)?\s*|\s*```$", "", html, flags=re.I | re.S).strip()
    html = re.sub(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", "", html, flags=re.I | re.S)
    html = re.sub(r"\s+on\w+\s*=\s*(['\"]).*?\1", "", html, flags=re.I | re.S)
    html = re.sub(r"\s+style\s*=\s*(['\"]).*?\1", "", html, flags=re.I | re.S)
    return html.strip()


def _build_listing_raw(
    *,
    title: str,
    description_html: str,
    selling_points: list[str],
    keywords: list[str],
) -> str:
    return (
        "## title\n"
        f"{title}\n\n"
        "## description_html\n"
        f"{description_html}\n\n"
        "## selling_points\n"
        + "\n".join(selling_points)
        + "\n\n## keywords\n"
        + "\n".join(keywords)
    ).strip()


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
        selling_points: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        detail_main_raw_text: str = "",
        category_id: str = "928016",
        primary_pack_id: Optional[int] = None,
        shop: str = "",
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
                     shop, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', '', ?, ?, ?, ?)""",
                (
                    topic_id,
                    _clamp_title(title or f"{t['topic_name']} — Multi-Pack Bundle"),
                    description_html,
                    json.dumps(_clean_list(selling_points or [], limit=5), ensure_ascii=False),
                    json.dumps(_clean_list(keywords or [], limit=20), ensure_ascii=False),
                    category_id,
                    sales_attribute_name,
                    primary,
                    detail_main_raw_text,
                    shop,
                    n, n,
                ),
            )
            product_id = cur.lastrowid

            # Build SKUs from packs — pack_id-based seller_sku guarantees uniqueness.
            for i, p in enumerate(packs, start=1):
                seller_sku = pack_seller_sku(p["pack_uid"] or p["display_name"] or "", p["id"])
                attr_val = attr_value_en(p["display_name"] or "", f"Pack {p['id']}")
                conn.execute(
                    """INSERT INTO lab_msku_product_skus
                        (product_id, pack_id, seller_sku, sales_attribute_value,
                         price_amount_override, stock_override, sort_order,
                         tiktok_sku_id, created_at)
                       VALUES (?, ?, ?, ?, '', NULL, ?, '', ?)""",
                    (product_id, p["id"], seller_sku, attr_val, i, n),
                )
            conn.commit()
        return product_id

    def create_from_pack(self, pack_id: int, *, shop: str = "",
                         title: str = "", description_html: str = "") -> int:
        """Start a NEW multi-SKU product seeded with ONE pack as its first SKU.

        If ``title``/``description_html`` are given they win (used by merge, where
        the new product's copy should drive the listing); otherwise reuse the
        pack's existing single-product listing copy if one was designed.
        """
        with _open_db(self.db_path) as conn:
            pk = conn.execute(
                "SELECT id, display_name, pack_uid FROM packs WHERE id = ?",
                (pack_id,),
            ).fetchone()
            if not pk:
                raise ValueError(f"pack #{pack_id} not found")
            tp = conn.execute(
                "SELECT title, description_html FROM tkshop_products "
                "WHERE pack_id = ? ORDER BY id DESC LIMIT 1",
                (pack_id,),
            ).fetchone()
            title = _clamp_title(
                title or (tp["title"] if tp and tp["title"] else None)
                or pk["display_name"] or f"Pack {pack_id}")
            desc = description_html or (tp["description_html"] if tp and tp["description_html"] else "") or ""
            n = _now()
            cur = conn.execute(
                """INSERT INTO lab_msku_products
                    (topic_id, title, description_html, selling_points, keywords,
                     category_id, sales_attribute_name, primary_pack_id,
                     publish_status, tiktok_product_id, detail_main_raw_text,
                     shop, created_at, updated_at)
                   VALUES (NULL, ?, ?, '[]', '[]', '928016', 'Theme', ?, 'draft', '', '', ?, ?, ?)""",
                (title, desc, pack_id, shop, n, n),
            )
            product_id = cur.lastrowid
            conn.commit()
        self.add_sku_from_pack(product_id, pack_id)
        logger.info("lab msku product #%s created from pack #%s (shop=%s)",
                    product_id, pack_id, shop or "-")
        return product_id

    def add_sku_from_pack(
        self,
        product_id: int,
        pack_id: int,
        *,
        sales_attribute_value: str = "",
        price_amount_override: str = "",
        stock_override: Optional[int] = None,
    ) -> Optional[int]:
        """Append a pack as a new SKU: auto unique seller_sku + English-only
        attribute value + dedup (same pack never added twice; attribute value
        unique within the product). Returns the new sku id, or None if the pack
        is already a SKU of this product (idempotent for merge)."""
        with _open_db(self.db_path) as conn:
            if not conn.execute("SELECT id FROM lab_msku_products WHERE id=?",
                                 (product_id,)).fetchone():
                raise ValueError(f"product #{product_id} not found")
            pk = conn.execute(
                "SELECT id, display_name, pack_uid FROM packs WHERE id = ?",
                (pack_id,),
            ).fetchone()
            if not pk:
                raise ValueError(f"pack #{pack_id} not found")
            if conn.execute(
                "SELECT id FROM lab_msku_product_skus WHERE product_id=? AND pack_id=?",
                (product_id, pack_id),
            ).fetchone():
                return None  # already present — merge is idempotent
            existing_attrs = {
                r[0] for r in conn.execute(
                    "SELECT sales_attribute_value FROM lab_msku_product_skus WHERE product_id=?",
                    (product_id,),
                ).fetchall()
            }
        attr = attr_value_en(
            sales_attribute_value or pk["display_name"] or "", f"Pack {pack_id}")
        if attr in existing_attrs:
            attr = f"{attr} ({pack_id})"[:100]
        seller_sku = pack_seller_sku(pk["pack_uid"] or pk["display_name"] or "", pack_id)
        return self.add_sku(
            product_id, pack_id=pack_id, seller_sku=seller_sku,
            sales_attribute_value=attr,
            price_amount_override=price_amount_override,
            stock_override=stock_override,
        )

    def ai_generate_listing(
        self,
        topic_id: int,
        pack_ids: list[int],
        *,
        sales_attribute_name: str = "Theme",
        variant_values: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Generate complete English listing copy for a multi-SKU sticker
        product. This is one listing with selectable pack variants; the buyer
        chooses one option, so the copy must not imply all variants ship
        together.
        """
        from src.services.ai.router import get_router
        from src.services.ai.base import try_parse_json
        with _open_db(self.db_path) as conn:
            t = conn.execute(
                "SELECT topic_name, theme_summary FROM hot_topics WHERE id=?",
                (topic_id,),
            ).fetchone()
            packs = []
            if pack_ids:
                ph = ",".join("?" * len(pack_ids))
                packs = conn.execute(
                    f"""SELECT pk.id, pk.display_name, pk.pack_uid, pk.total_stickers,
                               ps.series_name, ps.style_anchor, ps.palette,
                               ps.pack_archetype, ps.metadata_json
                          FROM packs pk
                     LEFT JOIN pack_series ps ON ps.id = pk.series_id
                         WHERE pk.id IN ({ph}) ORDER BY pk.id""",
                    tuple(pack_ids),
                ).fetchall()
        topic_name = (t["topic_name"] if t else "") or ""
        topic_summary = (t["theme_summary"] if t else "") or ""
        variant_values = variant_values or []
        pack_lines: list[str] = []
        sample_lines: list[str] = []
        audience_notes: list[str] = []
        ip_warnings: list[str] = []
        keyword_roots: list[str] = []
        for idx, p in enumerate(packs):
            name = attr_value_en(
                p["display_name"] or p["series_name"] or "",
                p["pack_uid"] or f"Pack {p['id']}",
            )
            keyword_root = _ascii_prompt_text(name, 60).lower()
            if keyword_root and keyword_root not in keyword_roots:
                keyword_roots.append(keyword_root)
            variant = (
                _ascii_prompt_text(variant_values[idx], 80)
                if idx < len(variant_values) and variant_values[idx]
                else name
            )
            pack_lines.append(
                f"- SKU option: {variant}; pack name: {name}; "
                f"{p['total_stickers'] or 0} stickers; "
                f"style: {_ascii_prompt_text(p['style_anchor'], 260)}; "
                f"palette: {_ascii_prompt_text(p['palette'], 120)}; "
                f"type: {_ascii_prompt_text(p['pack_archetype'], 80)}"
            )
            try:
                meta = json.loads(p["metadata_json"] or "{}")
            except Exception:
                meta = {}
            platform_title = _ascii_prompt_text(meta.get("platform_title") or "", 160)
            if platform_title:
                sample_lines.append(f"- Existing single-pack title clue: {platform_title}")
            for a in meta.get("audience") or []:
                note = _ascii_prompt_text(a, 50)
                if note and note not in audience_notes:
                    audience_notes.append(note)
            warning = _ascii_prompt_text(meta.get("ip_warning") or "", 180)
            if warning:
                ip_warnings.append(warning)
            for brief in (meta.get("preview_briefs") or [])[:2]:
                if not isinstance(brief, dict):
                    continue
                brief_name = _ascii_prompt_text(brief.get("name") or brief.get("theme") or "", 80)
                stickers = [
                    _ascii_prompt_text(s, 90)
                    for s in (brief.get("stickers") or [])[:4]
                    if _ascii_prompt_text(s, 90)
                ]
                if stickers:
                    sample_lines.append(
                        f"- {name} / {brief_name}: " + "; ".join(stickers)
                    )

        pack_block = "\n".join(pack_lines) or "- (no packs selected)"
        sample_block = "\n".join(sample_lines[:18]) or "- (no sticker subject samples available)"
        audience_block = ", ".join(audience_notes[:8]) or "sticker buyers, gift shoppers, journal and laptop decorators"
        warning_block = "\n".join(f"- {w}" for w in ip_warnings[:3]) or "- No copyrighted or licensed character claims."
        variant_count = len(packs)
        prompt = (
            "Write a TikTok Shop listing for a MULTI-SKU sticker product.\n"
            "Important product model: this is ONE listing with selectable SKU "
            "variants. The buyer chooses ONE pack/style from the variant menu; "
            "do NOT imply they receive all variants together unless explicitly stated.\n\n"
            f"Topic: {_ascii_prompt_text(topic_name, 180)}\n"
            f"Topic summary: {_ascii_prompt_text(topic_summary, 300)}\n"
            f"Sales attribute name shown to buyer: {_ascii_prompt_text(sales_attribute_name, 50) or 'Theme'}\n"
            f"Number of selectable pack options: {variant_count}\n\n"
            "Variant pack facts:\n"
            f"{pack_block}\n\n"
            "Concrete sticker subject clues:\n"
            f"{sample_block}\n\n"
            f"Likely audience: {audience_block}\n"
            "Compliance / IP notes:\n"
            f"{warning_block}\n\n"
            "Output English (en-US) only. Make it specific to the variants; no "
            "generic 'decorate your life' filler. Avoid keyword stuffing and do "
            "not start the title with awkward marketplace phrasing like '50Pcs'.\n\n"
            "Return ONLY valid JSON with these fields:\n"
            "{\n"
            '  "title": "70-110 chars target, 255 hard max; mention choose/selectable pack if useful",\n'
            '  "description_html": "150-230 words, clean HTML using 2 <p> blocks and one <ul> with 3-5 <li> items; explain buyers choose one variant",\n'
            '  "selling_points": ["exactly 5 short buyer-facing bullets, 6-14 words each"],\n'
            '  "keywords": ["14-20 SEO keyword phrases, no hashtags"]\n'
            "}"
        )
        text = get_router().text_complete(
            prompt,
            system=(
                "You write concise, specific, conversion-focused e-commerce "
                "listings for sticker products. Output ONLY valid JSON."
            ),
            temperature=0.45, max_tokens=1800, task="msku:ai_listing",
            related_table="lab_msku_products",
        )
        data = try_parse_json(text) or {}
        title = _clamp_title((data.get("title") or "").strip())
        description_html = _clean_description_html(data.get("description_html"))
        selling_points = _clean_list(data.get("selling_points"), limit=5, item_max=110)
        keywords = _clean_list(data.get("keywords"), limit=20, item_max=70)
        fallback_points = [
            f"Choose from {variant_count or 'multiple'} sticker pack styles",
            "Waterproof vinyl decals for laptops and bottles",
            "Curated theme options in one easy listing",
            "Great for journals, gifts, and everyday decorating",
            "Original artwork with clean die-cut edges",
        ]
        if len(selling_points) < 5:
            selling_points = _clean_list(selling_points + fallback_points, limit=5, item_max=110)
        base_topic = _ascii_prompt_text(topic_name, 60).lower()
        fallback_keywords = [
            f"{keyword_roots[0]} stickers" if keyword_roots else (f"{base_topic} stickers" if base_topic else "sticker pack"),
            "waterproof vinyl stickers",
            "choose your sticker pack",
            "laptop stickers",
            "water bottle decals",
            "journal stickers",
            "gift stickers",
        ]
        if len(keywords) < 12:
            keywords = _clean_list(keywords + fallback_keywords, limit=20, item_max=70)
        return {
            "title": title,
            "description_html": description_html,
            "selling_points": selling_points,
            "keywords": keywords,
            "detail_main_raw_text": _build_listing_raw(
                title=title,
                description_html=description_html,
                selling_points=selling_points,
                keywords=keywords,
            ),
        }

    def update_product_meta(
        self,
        product_id: int,
        *,
        title: Optional[str] = None,
        description_html: Optional[str] = None,
        selling_points: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
        detail_main_raw_text: Optional[str] = None,
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
        if detail_main_raw_text is not None:
            sets.append("detail_main_raw_text=?"); params.append(detail_main_raw_text)
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

    def _pack_image_abs(self, pack_id: Optional[int]) -> str:
        """Absolute path to a pack's variant image: its cover, else first 'ok'
        preview. Returns '' if none on disk."""
        if not pack_id:
            return ""
        with _open_db(self.db_path) as conn:
            r = conn.execute("SELECT cover_image_path FROM packs WHERE id=?", (pack_id,)).fetchone()
            cover = (r["cover_image_path"] if r else "") or ""
            if cover:
                ap = cover if os.path.isabs(cover) else os.path.abspath(cover)
                if os.path.isfile(ap):
                    return ap
            r2 = conn.execute(
                """SELECT pp.image_path
                     FROM packs pk
                LEFT JOIN pack_previews pp ON pp.series_id = pk.series_id
                                          AND pp.generation_status='ok'
                                          AND COALESCE(pp.image_path,'') != ''
                    WHERE pk.id=? ORDER BY pp.preview_idx LIMIT 1""",
                (pack_id,),
            ).fetchone()
            prev = (r2["image_path"] if r2 else "") or ""
            if prev:
                ap = prev if os.path.isabs(prev) else os.path.abspath(prev)
                if os.path.isfile(ap):
                    return ap
        return ""

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
                # Per-variant image = this SKU's pack cover (or first preview).
                "image_path": self._pack_image_abs(s.get("pack_id")),
            })

        return {
            "title": _clamp_title(p.get("title") or ""),
            "description_html": p.get("description_html") or "",
            "category_id": p.get("category_id") or "928016",
            "sales_attribute_name": p.get("sales_attribute_name") or "Theme",
            "shop": p.get("shop") or "",
            "skus": skus_payload,
            "image_urls": [],
            "image_paths": image_paths,
            "auto_activate": True,  # caller can override before send
        }

    def publish(self, product_id: int, *, auto_activate: bool = True) -> dict[str, Any]:
        payload = self.build_publish_payload(product_id)
        payload["auto_activate"] = bool(auto_activate)
        # multi-channel-api reads the target shop from the QUERY PARAM (same as
        # the single-SKU sticker_publish path), not the body — pass it there.
        shop_q = {"shop": payload["shop"]} if payload.get("shop") else None
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
            resp = requests.post(endpoint, json=payload, params=shop_q,
                                 timeout=TKSHOP_SERVER_TIMEOUT)
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
        shop_q = {"shop": payload["shop"]} if payload.get("shop") else None
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/{tt_id}/sticker_multi_sku_update"
        try:
            resp = requests.post(endpoint, json=payload, params=shop_q, timeout=TKSHOP_SERVER_TIMEOUT)
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
                "SELECT tiktok_product_id, publish_status, shop FROM lab_msku_products WHERE id=?",
                (product_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"product #{product_id} not found")
            tt_id = (row["tiktok_product_id"] or "").strip()
        if not tt_id:
            return {"ok": False, "error_message": "no tiktok_product_id"}
        shop_q = {"shop": row["shop"]} if (row["shop"] or "").strip() else None
        endpoint = f"{TKSHOP_SERVER_URL.rstrip('/')}/api/v1/tiktok/products/{tt_id}"
        try:
            resp = requests.get(endpoint, params=shop_q, timeout=TKSHOP_SERVER_TIMEOUT)
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
