"""Etsy product copy generator.

Takes a "local product" master (English marketing content, already parsed —
see ``tkshop`` service ``get_local_product()``) and produces Etsy-specific SEO
copy: a long-tail keyword title, a benefit-led description, exactly 13 tags,
and a materials list.

Mirrors the Shopify generator's two-step pattern (creative ``text_complete``
pass → structured ``extract_json`` via the shared ``AIRouter``) and its
never-raises contract: on any AI error or empty output it falls back to
deriving every field from the master. Pure function, ASCII-only logging.

Etsy constraints baked into the schema:
  - title    <= 140 chars, natural language, long-tail keywords up front
  - tags     EXACTLY 13, each <= 20 chars, multi-word phrases, no '#'
  - materials 1-5 short terms
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import get_router

logger = get_logger("service.etsy.copy_generator")

MAX_TITLE = 140
MAX_TAG = 20
N_TAGS = 13

_ETSY_SYSTEM_PROMPT = (
    "You are a senior Etsy SEO copywriter for a playful but tidy sticker brand. "
    "Etsy shoppers search with long-tail, natural-language phrases, so you write "
    "keyword-rich but human titles and warm, benefit-led descriptions in clean "
    "American English. No emoji, no hype cliches, no fake scarcity. You know Etsy "
    "tags must be short multi-word phrases (<= 20 chars each) and that a listing "
    "gets exactly 13 of them."
)

_ETSY_EXTRACT_SCHEMA: dict[str, Any] = {
    "title": "string, <= 140 chars, Etsy SEO title: front-load long-tail keywords, "
             "still natural language, comma-separated phrase style is fine",
    "description": "string, 2-4 short paragraphs: an emotional hook, a 'What you get' "
                   "line (number of designs, material, die-cut), key benefits, and a "
                   "brief care/shipping note. Plain text with line breaks, no HTML.",
    "tags": ["string, EXACTLY 13 Etsy tags, each <= 20 characters, multi-word "
             "long-tail phrases, lowercase, no '#', no duplicates"],
    "materials": ["string, 1-5 short material terms, e.g. vinyl, laminate"],
}

_ETSY_EXTRACT_INSTRUCTIONS = (
    f"title must be <= {MAX_TITLE} characters. tags must have EXACTLY {N_TAGS} items, "
    f"each <= {MAX_TAG} characters, all lowercase, no '#', no duplicates. "
    "materials must have 1-5 items. Keep every string plain text (no HTML, no markdown)."
)

_CONTENT_KEYS = ("title", "description", "tags", "materials")


def _clean(v: Any) -> str:
    return re.sub(r"[ \t]+", " ", str(v or "")).strip()


def _build_main_prompt(master: dict) -> str:
    title = _clean(master.get("title")) or _clean(master.get("pack_name"))
    desc = _clean(master.get("description_html"))
    sps = master.get("selling_points") or []
    if isinstance(sps, str):
        sps = [sps]
    kws = master.get("keywords") or []
    if isinstance(kws, str):
        kws = [kws]
    n = master.get("total_stickers") or ""

    sp_block = "\n".join(f"- {_clean(s)}" for s in sps if _clean(s)) or "(none)"
    kw_block = ", ".join(_clean(k) for k in kws if _clean(k)) or "(none)"

    return (
        "Write Etsy SEO copy for this sticker pack.\n\n"
        f"WORKING TITLE: {title or '(untitled sticker pack)'}\n"
        f"NUMBER OF DESIGNS: {n or 'a set of'}\n"
        f"EXISTING DESCRIPTION: {desc[:600] or '(none)'}\n"
        f"SELLING POINTS:\n{sp_block}\n\n"
        f"KEYWORD SEEDS (for SEO): {kw_block}\n\n"
        "Produce, clearly labeled:\n"
        f"1. TITLE: an Etsy-optimized title, {MAX_TITLE} characters or fewer, "
        "front-loading the strongest long-tail search phrases (e.g. 'Custom Vinyl "
        "Sticker Pack', 'Waterproof Laptop Stickers') while staying readable.\n"
        "2. DESCRIPTION: 2-4 short paragraphs — an emotional hook, a 'What you get' "
        "line (number of designs + premium matte waterproof vinyl + die-cut), 3-5 "
        "benefits, and a short care/shipping note.\n"
        f"3. TAGS: exactly {N_TAGS} Etsy tags, each {MAX_TAG} characters or fewer, "
        "lowercase multi-word long-tail phrases buyers actually search, no '#'.\n"
        "4. MATERIALS: 1-5 short material terms.\n\n"
        "Keep it playful but tidy, concrete, conversion-focused. No emoji."
    )


def _norm_tags(raw: Any, master: dict) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    src = raw if isinstance(raw, list) else []
    # backfill from keywords if the model under-delivered
    src = list(src) + [k for k in (master.get("keywords") or []) if k]
    for t in src:
        # Etsy tags 只允许字母/数字/空格/连字符; 去掉逗号/'/# 等非法字符
        tag = re.sub(r"[^a-z0-9 -]", "", _clean(t).lower()).strip()
        if not tag or len(tag) > MAX_TAG:
            tag = tag[:MAX_TAG].strip()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
        if len(out) >= N_TAGS:
            break
    # pad to exactly 13 with generic sticker tags if still short
    for filler in ("sticker pack", "vinyl stickers", "waterproof sticker",
                   "laptop sticker", "water bottle", "die cut sticker",
                   "gift for her", "cute stickers", "planner sticker",
                   "journal sticker", "small business", "handmade sticker",
                   "sticker set"):
        if len(out) >= N_TAGS:
            break
        if filler not in seen:
            seen.add(filler)
            out.append(filler)
    return out[:N_TAGS]


def _fallback_content(master: dict) -> dict:
    title = (_clean(master.get("title")) or _clean(master.get("pack_name"))
             or "Custom Vinyl Sticker Pack")[:MAX_TITLE]
    desc = _clean(master.get("description_html"))
    if not desc:
        sps = master.get("selling_points") or []
        if isinstance(sps, str):
            sps = [sps]
        bullets = "\n".join(f"- {_clean(s)}" for s in sps if _clean(s))
        n = master.get("total_stickers") or ""
        desc = (
            f"{title}.\n\n"
            f"What you get: {n or 'a set of'} original die-cut designs printed on "
            "premium matte waterproof vinyl.\n\n"
            f"{bullets}\n\n"
            "Ships fast, carefully packed. Perfect for laptops, water bottles, "
            "journals and gifts."
        ).strip()
    return {
        "title": title,
        "description": desc,
        "tags": _norm_tags(None, master),
        "materials": ["vinyl", "laminate"],
    }


def _normalize_content(raw: dict, master: dict, fallback: dict) -> dict:
    title = _clean(raw.get("title"))[:MAX_TITLE] or fallback["title"]
    desc = _clean(raw.get("description")) or fallback["description"]
    tags = _norm_tags(raw.get("tags"), master)
    # Etsy materials 只允许字母/数字/空格(不含标点/连字符), 每个 <=45 字符, 最多 13 个
    mats: list[str] = []
    for m in (raw.get("materials") or []):
        c = re.sub(r"[^a-z0-9 ]", "", _clean(m).lower()).strip()
        if c:
            mats.append(c[:45])
    mats = mats[:5] or ["vinyl", "laminate"]
    return {"title": title, "description": desc, "tags": tags, "materials": mats}


def generate_etsy_content(local_product: dict, *, router=None) -> dict:
    """Two-step AI generation of Etsy copy. Returns
    ``{title, description, tags(13), materials}``. Never raises — falls back to
    deriving everything from the master on any error / empty output.
    """
    local_product = local_product or {}
    fallback = _fallback_content(local_product)
    try:
        router = router or get_router()
        main_text = router.text_complete(
            _build_main_prompt(local_product),
            system=_ETSY_SYSTEM_PROMPT,
            temperature=0.7,
            task="etsy_copy:main",
            related_table="local_products",
            related_id=local_product.get("id"),
        )
        if not (main_text or "").strip():
            logger.warning("etsy copy: empty creative output for product #%s; fallback",
                           local_product.get("id"))
            return fallback
        raw = router.extract_json(
            main_text,
            schema=_ETSY_EXTRACT_SCHEMA,
            instructions=_ETSY_EXTRACT_INSTRUCTIONS,
            max_retries=1,
            task="etsy_copy:extract",
            related_table="local_products",
            related_id=local_product.get("id"),
        )
        if not isinstance(raw, dict) or not raw:
            logger.warning("etsy copy: empty structured output for product #%s; fallback",
                           local_product.get("id"))
            return fallback
        return _normalize_content(raw, local_product, fallback)
    except Exception as e:  # noqa: BLE001 — must never raise.
        logger.warning("etsy copy generation failed for product #%s: %s; fallback",
                       local_product.get("id"), str(e)[:300])
        return fallback
