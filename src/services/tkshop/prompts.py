"""Prompts for TKShop product detail two-step generation + self-heal.

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
    suggested_seller_sku: str = "",
) -> str:
    """Markdown plan: title, description (HTML), selling points, keywords, sku."""
    sample_lines = "\n".join(f"- {b}" for b in sticker_briefs_sample[:10]) or "- (no sample available)"
    sku_line = (
        f"Suggested seller_sku: `{suggested_seller_sku}` (you may keep this verbatim)."
        if suggested_seller_sku else
        "If unsure, leave seller_sku empty and the system will compute a default."
    )
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
A SEO-friendly product title in English. **HARD LIMIT: 255 characters
maximum.** Target 90-180 characters for best buyer scannability.
TikTok Shop will reject anything over 255. MUST include "Class of 2026"
or the relevant year tag if archetype implies it. Front-load the
most-searched keywords. NO emojis. Example (89 chars):
"Class of 2026 Graduation Stickers | Black Gold Pack | Waterproof Vinyl
Laptop Decals"

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

#### seller_sku
A single seller-side SKU code, max 25 characters, alphanumeric + hyphens
only (regex `^[A-Z0-9-]+$`). Format: `INK-{{SLUG}}-{{N}}` where SLUG is
the uppercased pack name (letters/digits only) and N is the total sticker
count ({total_stickers}). {sku_line}
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
- "seller_sku": pull from #### seller_sku section. Plain string, no
  backticks or surrounding code fence. If the section is missing or
  empty, use empty string.
- If a section is missing, use empty string / empty list, but still
  include the field.
""".strip()


DETAIL_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["title", "description_html", "selling_points", "keywords"],
    "properties": {
        "title":            {"type": "string", "maxLength": 255},
        "description_html": {"type": "string"},
        "selling_points":   {"type": "array", "items": {"type": "string"}},
        "keywords":         {"type": "array", "items": {"type": "string"}},
        "seller_sku": {
            "type": "string",
            "maxLength": 25,
            "description": (
                "Format: INK-{SLUG}-{N}, slug uppercased pack name, "
                "N = sticker count. Max 25 chars, alphanumeric + hyphens only."
            ),
        },
    },
}


# ---------------------------------------------------------------------------
# Self-heal: rewrite a failing publish payload based on the server's error
# ---------------------------------------------------------------------------

SELF_HEAL_SYSTEM_PROMPT = (
    "You are a TikTok Shop publishing assistant. The seller submitted a "
    "product listing that the platform rejected. You will be given the "
    "exact payload that failed plus the platform's error code, error "
    "message, and an optional list of field hints. Your job: produce a "
    "minimal corrective patch — only change the fields the error suggests "
    "are problematic. Keep all other fields IDENTICAL. Output ONLY a JSON "
    "object matching the schema; no prose, no code fences. "
    "All copy stays English (en-US)."
)

SELF_HEAL_INSTRUCTIONS = """
Common rejection causes and how to fix:
- title too long (>255 chars): trim while keeping the front-loaded keywords;
  remove trailing pipe-separated tag fragments first.
- title contains banned/restricted characters or all-caps marketing claims:
  rewrite into Title Case, strip emoji and special chars beyond `& | ,`.
- description_html invalid: ensure paragraphs use <p>, lists use <ul><li>,
  no <script>/<style>/inline event handlers; do NOT escape angle brackets.
- description has banned characters or claims: soften health/efficacy
  language, remove emojis.
- seller_sku duplicate or wrong format: change the trailing -N suffix
  (e.g. -100 -> -100A) or replace SLUG with a more unique pack code.
  Keep alphanumeric + hyphens, max 25 chars.

Rules:
- Only emit the fields you actually want to change. If a field is fine,
  OMIT it from the output entirely. Do not echo unchanged values.
- Always include a one-line "rationale" string explaining what you
  changed and why (operator-facing, English).
- Never invent product attributes (price, weight, etc.) — those belong
  to the platform-side defaults.
""".strip()


SELF_HEAL_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "title":            {"type": "string", "maxLength": 255},
        "description_html": {"type": "string"},
        "seller_sku":       {"type": "string", "maxLength": 25},
        "rationale":        {"type": "string", "maxLength": 500},
    },
}


# ---------------------------------------------------------------------------
# Image design: synthesize main + secondary product image specs from previews
# ---------------------------------------------------------------------------

IMAGE_DESIGN_SYSTEM_PROMPT = (
    "You are an art director for a TikTok Shop sticker store. Given a "
    "design context describing a sticker pack (palette, style anchor, "
    "themes, sample sticker briefs), produce concrete image-generation "
    "prompts for a main product image (cover/hero) and several supporting "
    "product images (lifestyle, flat-lay/scale, packaging mock). Each "
    "prompt should be a single English sentence-paragraph, ready to feed "
    "into an image-to-image model that uses one of the pack's stickers "
    "as a visual reference. Output ONLY a JSON object matching the "
    "schema; no prose, no code fences."
)

IMAGE_DESIGN_INSTRUCTIONS = """
Design rules:
- main: a clean hero shot. The main sticker(s) sit on a soft, on-brand
  background. Studio lighting. Slight angle or flat-on. NO text overlay,
  NO watermarks, NO logos. Photorealistic e-commerce listing aesthetic.
- secondary[0] = lifestyle: stickers applied on a real-world surface
  (laptop lid, water bottle, journal cover, phone case). Hands or human
  presence is fine. Natural daylight or warm indoor light.
- secondary[1] = flat-lay/scale: top-down view of multiple stickers on a
  textured surface (desk, paper, fabric) with a coin or ruler-implied
  scale cue. Shows variety of designs.
- secondary[2] = packaging/giftable: stickers next to or inside small
  kraft packaging, or fanned out alongside an envelope/journal. Implies
  small-business gifting.

Each "image_prompt" must:
- Be 1-3 English sentences, < 350 characters.
- Reference the palette, style anchor, and 1-2 sample sticker subjects.
- Specify camera angle + lighting + surface/background.
- End with: "Photorealistic product photography, no text overlay, no
  watermark, no logo."

Each "concept" is a 5-12 word internal label for the operator (e.g.
"hero on cream paper", "lifestyle laptop lid").
""".strip()


IMAGE_DESIGN_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["main", "secondary"],
    "properties": {
        "main": {
            "type": "object",
            "required": ["concept", "image_prompt"],
            "properties": {
                "concept":      {"type": "string", "maxLength": 100},
                "image_prompt": {"type": "string", "maxLength": 800},
            },
        },
        "secondary": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["concept", "image_prompt"],
                "properties": {
                    "concept":      {"type": "string", "maxLength": 100},
                    "image_prompt": {"type": "string", "maxLength": 800},
                },
            },
        },
    },
}


def build_image_design_prompt(
    *,
    pack_display_name: str,
    pack_archetype: str,
    style_anchor: str,
    palette: str,
    total_stickers: int,
    sticker_briefs_sample: list[str],
    preview_prompt_samples: list[str],
    secondary_count: int = 3,
    language: str = "en",
) -> str:
    sample_lines = "\n".join(f"- {b}" for b in sticker_briefs_sample[:12]) or "- (no brief)"
    preview_lines = "\n".join(f"- {p[:200]}" for p in preview_prompt_samples[:6]) or "- (no preview prompt)"
    lang_lookup = {
        "en": "Western (US/UK/AU/CA) e-commerce aesthetic — clean studio, "
              "natural daylight, common Western household surfaces.",
        "zh": "Asian (CN/JP/KR) e-commerce aesthetic — soft pastel, paper "
              "textures, common Asian household surfaces and props.",
        "ja": "Japanese kawaii aesthetic — pastel, soft shadows, minimal "
              "props, slightly oversaturated.",
    }
    locale_hint = lang_lookup.get(language.lower(), lang_lookup["en"])
    return f"""Design product images for this sticker pack listing.

### Pack
- name: **{pack_display_name}**
- archetype: `{pack_archetype or 'general'}`
- {total_stickers} unique die-cut waterproof vinyl stickers
- palette: {palette or 'multi-color'}
- visual style: {style_anchor[:400] if style_anchor else '(general)'}

### Target market / aesthetic locale
- code: `{language}`
- direction: {locale_hint}

### Sticker subjects (sample)
{sample_lines}

### Existing preview prompts (used to render the stickers themselves)
{preview_lines}

### Output (JSON only)
Produce one "main" image spec and {secondary_count} "secondary" image specs
following the design rules. Each spec has:
- "concept": short label
- "image_prompt": the actual image-generation prompt — ALWAYS in English
  regardless of locale code, because the downstream image-to-image model
  expects English. Use the locale to influence STAGING / PROPS, not the
  prompt language itself.

The prompts will be fed into an image-to-image model that takes ONE of
the pack's existing stickers as the visual reference, so the prompts
should describe the SCENE / STAGING / CAMERA / LIGHTING — not the
sticker artwork itself.
"""


def build_self_heal_prompt(
    *,
    failed_payload: dict,
    error_code: str,
    error_message: str,
    field_hints: list[str],
) -> str:
    import json as _json
    hints_str = ", ".join(field_hints) if field_hints else "(none)"
    payload_str = _json.dumps(failed_payload, ensure_ascii=False, indent=2)
    return f"""The following sticker_publish payload was rejected by TikTok Shop.

ERROR_CODE: {error_code or '(none)'}
ERROR_MESSAGE: {error_message or '(none)'}
FIELD_HINTS: {hints_str}

FAILED_PAYLOAD:
```json
{payload_str}
```

Produce a minimal patch as a JSON object. Only include fields you want
to change (title, description_html, seller_sku). Always include a short
"rationale" describing the fix. Output ONLY the JSON object.
"""
