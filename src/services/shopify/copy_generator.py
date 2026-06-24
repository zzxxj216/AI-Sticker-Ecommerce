"""Shopify product copy generator + branded body_html assembler.

Takes a "local product" master (English marketing content, already parsed —
see ``tkshop`` service ``get_local_product()``) and produces:

1. ``generate_shopify_content`` — two-step AI generation (creative
   ``text_complete`` pass → structured ``extract_json``) yielding the
   Shopify-specific copy fields. Never raises: on any AI error or empty
   output it falls back to deriving everything from the master fields.
2. ``build_body_html`` — assembles a branded, sectioned, inline-styled
   ``body_html`` string (Shopify rich-text friendly; no scripts / external
   CSS) from a content dict.

Pure functions. No DB, no network beyond the shared ``AIRouter``. Logging is
ASCII-only (this Windows host's console is GBK).
"""

from __future__ import annotations

import html
import re
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import get_router

logger = get_logger("service.shopify.copy_generator")


# ----------------------------------------------------------------------
# Constants — brand template text (fixed sections) and AI contracts
# ----------------------------------------------------------------------

MATERIAL_CARE_HTML_TEXT = (
    "Printed on premium matte waterproof vinyl with a UV-resistant finish and "
    "precision die-cut edges, so colors stay vivid and the cut stays crisp. "
    "Wipe clean with a damp cloth. For best stick, apply to clean, dry, smooth "
    "surfaces."
)

SHIPPING_GUARANTEE_HTML_TEXT = (
    "Ships fast from our small business, carefully packed so it arrives flat "
    "and undamaged. Backed by our 30-day happiness guarantee — if you're "
    "not delighted, we'll make it right."
)

_SHOPIFY_SYSTEM_PROMPT = (
    "You are a senior Shopify e-commerce copywriter for a playful but tidy "
    "sticker brand. You write warm, benefit-led product copy that converts, "
    "in clean American English. No emoji, no hype cliches, no fake scarcity. "
    "Keep it concrete and on-brand."
)

# Schema describing the structured content we want back from extract_json.
_SHOPIFY_EXTRACT_SCHEMA: dict[str, Any] = {
    "hero_tagline": "string, punchy headline, <= 70 chars",
    "hero_intro": "string, 1-2 emotional opening sentences",
    "whats_included": "string, short paragraph: number of designs, material, die-cut",
    "bullets": ["string, 4-6 Shopify-style benefit bullets"],
    "how_to_use": ["string, 3-5 short use scenes (laptop, water bottle, journal, gift)"],
    "seo_title": "string, <= 60 chars, keyword-rich",
    "seo_description": "string, <= 160 chars",
}

_SHOPIFY_EXTRACT_INSTRUCTIONS = (
    "hero_tagline must be <= 70 characters. seo_title must be <= 60 characters. "
    "seo_description must be <= 160 characters. bullets must have 4-6 items; "
    "how_to_use must have 3-5 items. Do not include the handle/slug. "
    "Keep every string plain text (no HTML, no markdown)."
)

# Content keys the rest of the codebase depends on (hard interface contract).
_CONTENT_KEYS = (
    "hero_tagline",
    "hero_intro",
    "whats_included",
    "bullets",
    "how_to_use",
    "seo_title",
    "seo_description",
    "handle",
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Build a URL slug: lowercase, ``a-z0-9`` and single hyphens only."""
    text = (text or "").lower()
    # Strip accents/diacritics down to ASCII where possible.
    import unicodedata

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    # Replace any run of non-alphanumerics with a single hyphen.
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    # Collapse accidental double hyphens (defensive).
    text = re.sub(r"-{2,}", "-", text)
    return text or "sticker-pack"


def _as_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_list(value: Any) -> list[str]:
    """Coerce to a clean list of non-empty strings."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        s = item.strip() if isinstance(item, str) else ""
        if s:
            out.append(s)
    return out


def _truncate(text: str, limit: int) -> str:
    """Truncate to ``limit`` chars on a word boundary where reasonable."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    # Prefer not to slice a word in half.
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return cut


def _total_stickers(local_product: dict) -> int:
    try:
        return int(local_product.get("total_stickers") or 0)
    except (TypeError, ValueError):
        return 0


def _first_sentences(html_text: str, max_chars: int = 240) -> str:
    """Extract the first sentence(s) of plain text out of HTML paragraphs."""
    if not html_text:
        return ""
    # Strip tags, unescape entities, normalize whitespace.
    plain = re.sub(r"<[^>]+>", " ", html_text)
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return ""
    # Take up to the first two sentences.
    parts = re.split(r"(?<=[.!?])\s+", plain)
    intro = " ".join(parts[:2]).strip()
    return _truncate(intro, max_chars)


# ----------------------------------------------------------------------
# Fallback content (offline-safe; never raises)
# ----------------------------------------------------------------------

def _fallback_content(local_product: dict) -> dict:
    """Derive a complete content dict from master fields only (no AI)."""
    title = _as_str(local_product.get("title")) or _as_str(local_product.get("pack_name"))
    selling_points = _as_list(local_product.get("selling_points"))
    keywords = _as_list(local_product.get("keywords"))
    count = _total_stickers(local_product)

    hero_tagline = _truncate(title, 70) or "A Sticker Pack You'll Love"

    hero_intro = _first_sentences(_as_str(local_product.get("description_html")))
    if not hero_intro:
        hero_intro = (
            f"Add personality to everything you own with {title}."
            if title
            else "Add a little personality to everything you own."
        )

    count_phrase = f"{count} unique designs" if count else "a curated set of designs"
    whats_included = (
        f"This pack includes {count_phrase}, printed on premium matte "
        "waterproof vinyl and precision die-cut for clean, peel-and-stick edges."
    )

    bullets = selling_points[:6]
    if len(bullets) < 4:
        defaults = [
            "Waterproof, fade-resistant matte vinyl that lasts",
            "Precision die-cut for clean, easy peel-and-stick",
            "Perfect for laptops, water bottles, journals, and more",
            "A thoughtful, fun little gift for any occasion",
        ]
        for d in defaults:
            if len(bullets) >= 4:
                break
            if d not in bullets:
                bullets.append(d)

    how_to_use = [
        "Personalize your laptop or tablet",
        "Decorate a water bottle or travel mug",
        "Brighten up a journal, planner, or notebook",
        "Tuck into a card as a small surprise gift",
    ]

    seo_keywords = ", ".join(keywords[:6])
    base_seo_title = title or "Sticker Pack"
    seo_title = _truncate(base_seo_title, 60)
    if keywords:
        seo_description = _truncate(
            f"{title or 'Premium die-cut stickers'} — {seo_keywords}. "
            "Waterproof matte vinyl. Ships fast.",
            160,
        )
    else:
        seo_description = _truncate(
            f"{hero_intro} Premium waterproof vinyl, die-cut, ships fast.",
            160,
        )

    handle = _slugify(title)

    return {
        "hero_tagline": hero_tagline,
        "hero_intro": hero_intro,
        "whats_included": whats_included,
        "bullets": bullets,
        "how_to_use": how_to_use,
        "seo_title": seo_title,
        "seo_description": seo_description,
        "handle": handle,
    }


def _normalize_content(raw: dict, local_product: dict, fallback: dict) -> dict:
    """Merge AI output over the fallback, enforcing shape + length limits.

    Any field that the AI left empty/invalid is backfilled from ``fallback``,
    guaranteeing a complete dict with the contracted keys.
    """
    out = dict(fallback)

    hero_tagline = _as_str(raw.get("hero_tagline"))
    if hero_tagline:
        out["hero_tagline"] = _truncate(hero_tagline, 70)

    hero_intro = _as_str(raw.get("hero_intro"))
    if hero_intro:
        out["hero_intro"] = hero_intro

    whats_included = _as_str(raw.get("whats_included"))
    if whats_included:
        out["whats_included"] = whats_included

    bullets = _as_list(raw.get("bullets"))
    if len(bullets) >= 1:
        out["bullets"] = bullets[:6] if len(bullets) >= 4 else (bullets + fallback["bullets"])[:6]

    how_to_use = _as_list(raw.get("how_to_use"))
    if len(how_to_use) >= 1:
        out["how_to_use"] = how_to_use[:5] if len(how_to_use) >= 3 else (how_to_use + fallback["how_to_use"])[:5]

    seo_title = _as_str(raw.get("seo_title"))
    if seo_title:
        out["seo_title"] = _truncate(seo_title, 60)

    seo_description = _as_str(raw.get("seo_description"))
    if seo_description:
        out["seo_description"] = _truncate(seo_description, 160)

    # Handle is always deterministic from the master title (AI never supplies it).
    out["handle"] = _slugify(_as_str(local_product.get("title")) or _as_str(local_product.get("pack_name")))

    # Final safety: make sure every contracted key exists.
    for key in _CONTENT_KEYS:
        out.setdefault(key, fallback[key])
    return out


# ----------------------------------------------------------------------
# Prompt builder (creative pass)
# ----------------------------------------------------------------------

def _build_main_prompt(local_product: dict) -> str:
    title = _as_str(local_product.get("title")) or _as_str(local_product.get("pack_name"))
    description = _first_sentences(_as_str(local_product.get("description_html")), max_chars=600)
    selling_points = _as_list(local_product.get("selling_points"))
    keywords = _as_list(local_product.get("keywords"))
    count = _total_stickers(local_product)

    sp_block = "\n".join(f"- {p}" for p in selling_points[:8]) or "- (none provided)"
    kw_block = ", ".join(keywords[:20]) or "(none provided)"
    count_str = str(count) if count else "an unspecified number of"

    return (
        "Write Shopify product-page copy for the following sticker pack.\n\n"
        f"PRODUCT TITLE: {title or '(untitled sticker pack)'}\n"
        f"NUMBER OF DESIGNS: {count_str}\n"
        f"EXISTING DESCRIPTION: {description or '(none)'}\n\n"
        "SELLING-POINT SEEDS (rewrite into Shopify benefit bullets):\n"
        f"{sp_block}\n\n"
        f"KEYWORD SEEDS (for SEO): {kw_block}\n\n"
        "Produce, clearly labeled:\n"
        "1. HERO TAGLINE: a punchy headline, 70 characters or fewer.\n"
        "2. HERO INTRO: 1-2 warm, emotional opening sentences.\n"
        "3. WHAT'S INCLUDED: one short paragraph naming the number of designs, "
        "the premium matte waterproof vinyl material, and the die-cut finish.\n"
        "4. WHY YOU'LL LOVE IT: 4-6 benefit bullets (Shopify-ified selling points).\n"
        "5. HOW TO USE: 3-5 concrete use scenes (laptop, water bottle, journal, gift, etc.).\n"
        "6. SEO TITLE: 60 characters or fewer, keyword-rich.\n"
        "7. SEO DESCRIPTION: 160 characters or fewer, compelling and keyword-aware.\n\n"
        "Keep it playful but tidy, concrete, and conversion-focused. No emoji."
    )


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def generate_shopify_content(local_product: dict, *, router=None) -> dict:
    """Two-step AI generation of Shopify product copy.

    Returns a dict with keys: ``hero_tagline``, ``hero_intro``,
    ``whats_included``, ``bullets``, ``how_to_use``, ``seo_title``,
    ``seo_description``, ``handle``.

    Uses the shared ``AIRouter`` (``get_router()`` when ``router`` is None)
    with the established two-step pattern: a creative ``text_complete`` pass
    followed by a structured ``extract_json``. On ANY exception or empty AI
    output it falls back to deriving every field from the master product
    fields. This function never raises.
    """
    local_product = local_product or {}
    fallback = _fallback_content(local_product)

    try:
        router = router or get_router()
        prompt = _build_main_prompt(local_product)
        main_text = router.text_complete(
            prompt,
            system=_SHOPIFY_SYSTEM_PROMPT,
            temperature=0.7,
            task="shopify_copy:main",
            related_table="local_products",
            related_id=local_product.get("id"),
        )
        if not (main_text or "").strip():
            logger.warning("shopify copy: empty creative output for product #%s; using fallback",
                           local_product.get("id"))
            return fallback

        raw = router.extract_json(
            main_text,
            schema=_SHOPIFY_EXTRACT_SCHEMA,
            instructions=_SHOPIFY_EXTRACT_INSTRUCTIONS,
            max_retries=1,
            task="shopify_copy:extract",
            related_table="local_products",
            related_id=local_product.get("id"),
        )
        if not isinstance(raw, dict) or not raw:
            logger.warning("shopify copy: empty structured output for product #%s; using fallback",
                           local_product.get("id"))
            return fallback

        return _normalize_content(raw, local_product, fallback)
    except Exception as e:  # noqa: BLE001 — must never raise; fall back gracefully.
        logger.warning("shopify copy generation failed for product #%s: %s; using fallback",
                       local_product.get("id"), str(e)[:300])
        return fallback


def build_body_html(local_product: dict, content: dict, *, offer: dict | None = None) -> str:
    """Assemble a branded, sectioned, inline-styled ``body_html`` string.

    Sections, in order: (optional offer banner), Hero, What's included, Why
    you'll love it (checklist), How to use, Material & Care (fixed), Shipping &
    Guarantee (fixed). ``offer`` (e.g. ``{"discount_percent": 40,
    "free_shipping": True}``) renders a promo banner under the hero. All
    user/AI text is HTML-escaped. Returns the HTML string.
    """
    local_product = local_product or {}
    content = content or {}
    offer = offer or {}

    def esc(value: Any) -> str:
        return html.escape(_as_str(value) if isinstance(value, str) else str(value or ""))

    # Style snippets (inline; Shopify rich-text friendly).
    wrap_style = (
        "max-width:680px;margin:0 auto;font-family:-apple-system,Segoe UI,Roboto,"
        "Helvetica,Arial,sans-serif;color:#2b2b35;line-height:1.6;"
    )
    section_style = "margin:0 0 28px 0;"
    h2_style = "font-size:24px;line-height:1.25;margin:0 0 10px 0;color:#1f1f29;"
    h3_style = (
        "font-size:13px;letter-spacing:0.08em;text-transform:uppercase;"
        "margin:0 0 12px 0;color:#7a5cff;font-weight:700;"
    )
    p_style = "margin:0 0 12px 0;font-size:16px;"
    ul_style = "list-style:none;padding:0;margin:0;"
    check_li_style = "padding:6px 0 6px 28px;position:relative;font-size:16px;"
    check_glyph_style = "position:absolute;left:0;top:6px;color:#22a06b;font-weight:700;"
    plain_ul_style = "padding-left:20px;margin:0;"
    plain_li_style = "padding:4px 0;font-size:16px;"

    count = _total_stickers(local_product)
    count_note = f"{count} unique designs" if count else "Multiple unique designs"

    parts: list[str] = []
    parts.append(f'<div style="{wrap_style}">')

    # 1) Hero
    parts.append(f'<div style="{section_style}">')
    parts.append(f'<h2 style="{h2_style}">{esc(content.get("hero_tagline"))}</h2>')
    intro = esc(content.get("hero_intro"))
    if intro:
        parts.append(f'<p style="{p_style}">{intro}</p>')
    parts.append("</div>")

    # 1b) Offer banner (standing rule: free shipping + N% off)
    try:
        disc = int(offer.get("discount_percent") or 0)
    except (TypeError, ValueError):
        disc = 0
    free_ship = bool(offer.get("free_shipping"))
    if disc > 0 or free_ship:
        chips = []
        if disc > 0:
            chips.append(f"\U0001F525 {disc}% OFF today")
        if free_ship:
            chips.append("\U0001F69A Free shipping")
        banner_style = (
            "margin:0 0 28px 0;padding:12px 16px;border-radius:10px;"
            "background:#fff4ec;border:1px solid #ffd9c2;color:#b4530a;"
            "font-weight:700;font-size:15px;text-align:center;"
        )
        parts.append(f'<div style="{banner_style}">{esc("  ·  ".join(chips))}</div>')

    # 2) What's included
    parts.append(f'<div style="{section_style}">')
    parts.append(f'<h3 style="{h3_style}">What\'s Included</h3>')
    whats = esc(content.get("whats_included"))
    if whats:
        parts.append(f'<p style="{p_style}">{whats}</p>')
    parts.append(
        f'<p style="{p_style}font-weight:600;">This pack: {esc(count_note)}.</p>'
    )
    parts.append("</div>")

    # 3) Why you'll love it (checklist)
    bullets = _as_list(content.get("bullets"))
    if bullets:
        parts.append(f'<div style="{section_style}">')
        parts.append(f'<h3 style="{h3_style}">Why You\'ll Love It</h3>')
        parts.append(f'<ul style="{ul_style}">')
        for b in bullets:
            parts.append(
                f'<li style="{check_li_style}">'
                f'<span style="{check_glyph_style}">✓</span>{esc(b)}</li>'
            )
        parts.append("</ul>")
        parts.append("</div>")

    # 4) How to use
    how = _as_list(content.get("how_to_use"))
    if how:
        parts.append(f'<div style="{section_style}">')
        parts.append(f'<h3 style="{h3_style}">How To Use</h3>')
        parts.append(f'<ul style="{plain_ul_style}">')
        for h in how:
            parts.append(f'<li style="{plain_li_style}">{esc(h)}</li>')
        parts.append("</ul>")
        parts.append("</div>")

    # 5) Material & Care (fixed brand template)
    parts.append(f'<div style="{section_style}">')
    parts.append(f'<h3 style="{h3_style}">Material &amp; Care</h3>')
    parts.append(f'<p style="{p_style}">{html.escape(MATERIAL_CARE_HTML_TEXT)}</p>')
    parts.append("</div>")

    # 6) Shipping & Guarantee (fixed brand template)
    parts.append(f'<div style="{section_style}">')
    parts.append(f'<h3 style="{h3_style}">Shipping &amp; Guarantee</h3>')
    ship_text = SHIPPING_GUARANTEE_HTML_TEXT
    if free_ship:
        ship_text = "Free shipping on every order. " + ship_text
    parts.append(f'<p style="{p_style}">{html.escape(ship_text)}</p>')
    parts.append("</div>")

    parts.append("</div>")
    return "".join(parts)


# ----------------------------------------------------------------------
# Smoke test (offline-safe — forces the fallback path, no AI/API needed)
# ----------------------------------------------------------------------

if __name__ == "__main__":
    sample = {
        "id": 1,
        "pack_id": 42,
        "title": "Class of 2026 Graduation Stickers | Grad Cap Celebration Pack",
        "description_html": (
            "<p>Celebrate the big day in style. These stickers turn caps, "
            "gifts, and laptops into a graduation party.</p>"
            "<p>Made for grads, parents, and proud friends.</p>"
        ),
        "selling_points": [
            "Bold, celebratory grad-cap designs",
            "Durable waterproof finish",
            "Great for gifts and party favors",
        ],
        "keywords": [
            "graduation stickers", "class of 2026", "grad cap",
            "graduation gift", "vinyl stickers", "waterproof stickers",
        ],
        "total_stickers": 12,
        "pack_name": "Graduation 2026",
    }

    # Force the offline fallback path so the smoke test needs no API keys.
    content = _fallback_content(sample)
    body_html = build_body_html(sample, content)

    print("content keys:", sorted(content.keys()))
    print("bullets count:", len(content["bullets"]))
    print("how_to_use count:", len(content["how_to_use"]))
    print("handle:", content["handle"])
    print("seo_title len:", len(content["seo_title"]))
    print("seo_description len:", len(content["seo_description"]))
    print("body_html len:", len(body_html))
    for section in ("What's Included", "Why You'll Love It", "How To Use",
                    "Material &amp; Care", "Shipping &amp; Guarantee"):
        print(f"section present [{section}]:", section in body_html)
