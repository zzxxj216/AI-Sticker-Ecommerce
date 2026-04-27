"""Prompts for TKShop product detail two-step generation.

All buyer-facing copy in English. Operator-facing guidance kept English
in the system prompt to prevent the model from drifting into Chinese.
"""

from __future__ import annotations


DETAIL_MAIN_SYSTEM_PROMPT = (
    "You write product listings for an overseas TikTok Shop sticker brand. "
    "All copy MUST be English (en-US), written for buyers in the US/UK/AU/CA "
    "market. Tone: clear, scannable, lightly enthusiastic — not corporate, "
    "not over-hyped. The product is physical waterproof vinyl die-cut "
    "stickers shipped from a small e-commerce seller. Output a markdown "
    "plan with the requested sections — do NOT output JSON; a separate "
    "extractor will pull the structured fields."
)


def build_detail_main_prompt(
    *,
    pack_display_name: str,
    pack_archetype: str,
    style_anchor: str,
    palette: str,
    total_stickers: int,
    sticker_briefs_sample: list[str],
) -> str:
    """Markdown plan: title, description (HTML), selling points, keywords."""
    sample_lines = "\n".join(f"- {b}" for b in sticker_briefs_sample[:10]) or "- (no sample available)"
    return f"""Plan a TikTok Shop product listing for this sticker pack.

### Pack
- name: **{pack_display_name}**
- archetype: `{pack_archetype or 'general'}`
- {total_stickers} unique die-cut waterproof vinyl stickers
- palette: {palette or 'multi-color'}
- visual style: {style_anchor[:300] if style_anchor else '(general)'}

### Sample sticker contents
{sample_lines}

### Product reality
- Physical waterproof vinyl die-cut stickers
- Common use cases: laptop, water bottle, phone case, journal, scrapbook,
  party favor, gift bag, envelope seal
- Ships from small business
- No copyrighted characters

### Output (markdown sections, no JSON)

#### title
A 60-100 character SEO-friendly product title in English. MUST include
"Class of 2026" or the relevant year tag if archetype implies it. Front-
load the most-searched keywords. NO emojis. Example shape:
"Class of 2026 Graduation Stickers | Black Gold Senior Pack | Waterproof
Vinyl Decals for Laptop & Water Bottle"

#### description_html
A 3-5 paragraph product description in clean HTML. Use:
- <p>...</p> for paragraphs
- <ul><li>...</li></ul> for the use-cases list
- <strong>...</strong> for key emphasis
NO inline styles, NO scripts. Open with a hook paragraph, then a "What's
included" line with the count, then a use-cases list, then a small
shipping/quality reassurance line. Keep total length 250-450 words.

#### selling_points
Exactly 5 short selling-point bullets (one line each, plain text, no
markdown bullet character). Each 6-12 words. Punchy, scannable.
Examples: "60+ unique designs, no duplicates", "Waterproof vinyl, fade
resistant", "Perfect for laptop, water bottle, scrapbook".

#### keywords
12-20 SEO keyword phrases (one per line, plain text, no #). Mix:
- 3-5 core ("graduation stickers", "class of 2026 stickers")
- 4-6 niche/style ("black gold grad stickers", "aesthetic sticker pack")
- 3-5 use-case ("laptop stickers", "water bottle decals")
- 2-4 occasion/audience ("grad gift", "senior year stickers")
"""


DETAIL_EXTRACT_INSTRUCTIONS = """
Mapping rules:
- "title": pull from #### title section. Plain text. Trim whitespace.
- "description_html": pull from #### description_html section. Preserve
  HTML tags AS-IS. Do NOT escape angle brackets — store the raw HTML
  string so it can be rendered by the client.
- "selling_points": pull from #### selling_points section. One string
  per non-empty line. Strip any leading bullet markers ("- ", "* ", "1. ").
- "keywords": pull from #### keywords section. One string per non-empty
  line, no leading "#" or "- ".
- If a section is missing, use empty string / empty list, but still
  include the field.
""".strip()


DETAIL_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["title", "description_html", "selling_points", "keywords"],
    "properties": {
        "title":            {"type": "string", "maxLength": 200},
        "description_html": {"type": "string"},
        "selling_points":   {"type": "array", "items": {"type": "string"}},
        "keywords":         {"type": "array", "items": {"type": "string"}},
    },
}
