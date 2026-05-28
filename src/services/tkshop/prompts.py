"""Prompts for TKShop product detail two-step generation + self-heal.

All buyer-facing copy in English. Operator-facing guidance kept English
in the system prompt to prevent the model from drifting into Chinese.
"""

from __future__ import annotations


DETAIL_MAIN_SYSTEM_PROMPT = (
    "You write product listings for an overseas TikTok Shop sticker brand. "
    "All copy MUST be English (en-US), written for buyers in the US/UK/AU/CA "
    "market. Tone: specific, buyer-facing, scannable, and natural — never "
    "keyword-stuffed, never corporate, never over-hyped. The product is a "
    "physical waterproof vinyl die-cut sticker pack shipped from a small "
    "e-commerce seller. Output a markdown plan with the requested sections "
    "— do NOT output JSON; a separate extractor will pull the structured "
    "fields."
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
    sample_lines = "\n".join(f"- {b}" for b in sticker_briefs_sample[:14]) or "- (no sample available)"
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
- Common use cases, choose only the most relevant 3-5 in buyer copy:
  laptop, water bottle, phone case, journal, scrapbook, party favor,
  gift bag, envelope seal
- Ships from small business
- No copyrighted characters

### Output (markdown sections, no JSON)

#### title
Write one buyer-readable TikTok Shop title. **HARD LIMIT: 255 characters,
but target 65-100 characters.** Use at most one separator (`|` or comma).
Do not stack every keyword or every use case. Do not start with awkward
marketplace phrasing like "50Pcs"; use "50 Waterproof Vinyl Stickers" or
"50-Piece Sticker Pack" if the count matters. MUST include "Class of 2026"
or the relevant year tag if archetype implies it. NO emojis. Strong shape:
main search phrase + specific style/occasion + material/use case.
Example:
"Class of 2026 Graduation Stickers | Black Gold Waterproof Vinyl Decals"

#### description_html
A concise product description in clean HTML, written like a polished shop
listing, not a generic template. Target 130-220 words. Use:
- <p>...</p> for paragraphs
- <ul><li>...</li></ul> for 3-5 specific highlights
- <strong>...</strong> for key emphasis
NO inline styles, NO scripts. Open with a concrete hook tied to THIS pack's
theme and sample stickers. Mention the count once. Include specific design
details from the sample sticker contents. Avoid empty openers like "Bring
style to your everyday items" unless the pack truly needs that wording.
Avoid repeating "waterproof vinyl" more than twice. End with a short,
practical quality/use line; do not over-focus on shipping.

#### selling_points
Exactly 5 short selling-point bullets (one line each, plain text, no
markdown bullet character). Each 6-12 words. Punchy, scannable.
At least 3 bullets must be specific to this pack's theme, art style, or
sticker subjects. Avoid generic repeats across products.
Examples: "60 unique retro travel-poster designs", "Waterproof vinyl for
laptops and bottles", "Black-gold grad labels for party favors".

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

# Fidelity clause appended to every image_prompt — the #1 lever against
# the image-to-image model silently redrawing / blurring the real sticker
# artwork. The reference handed to the model is an actual die-cut sticker
# (or sticker sheet), so staging must PRESERVE it, never reinterpret it.
IMAGE_DESIGN_FIDELITY_CLAUSE = (
    "Preserve the exact sticker artwork, colors and text from the reference "
    "image — do not redraw, restyle, recolor, blur, or add any new text, "
    "watermark or logo. Photorealistic product photography."
)

# Menu of secondary-image angles the art-director model picks from, tuned to
# the pack instead of a rigid lifestyle/flat-lay/packaging triplet. Keep the
# enum in sync with IMAGE_DESIGN_EXTRACT_SCHEMA.role_type.
IMAGE_DESIGN_ROLE_MENU = """
- "hero": the cover/bundle shot. ALL the pack's stickers clustered together
  as one dense, slightly-overlapping collage that fills the frame on a pure
  plain white background — no props, no surface, no text. Reserved for "main".
- "lifestyle": stickers applied on a real-world surface that fits the theme
  (laptop lid, water bottle, journal, phone case, helmet, guitar case…).
  Hands / human presence fine. Natural daylight or warm indoor light.
- "in_use": close, tactile shot — a hand peeling one sticker off its backing
  or pressing it onto a surface. Shows it is a real die-cut vinyl sticker.
- "flat_lay": top-down view of several stickers on a textured surface with a
  coin / ruler / hand for implied scale. Shows the variety of designs.
- "full_set": neat overview of the whole set so buyers see everything they
  get — stickers arranged in a tidy grid or fan on a plain on-brand surface.
- "packaging": stickers next to or inside small kraft / glassine packaging,
  or fanned beside an envelope/journal. Implies small-business gifting.
- "scene": a themed contextual still-life that matches the pack subject
  (e.g. desert dunes for a van-life pack, a night desk for a celestial pack)
  with the stickers placed naturally within it.
""".strip()

IMAGE_DESIGN_SYSTEM_PROMPT = (
    "You are an art director for a TikTok Shop sticker store. Given a "
    "design context describing a sticker pack (palette, style anchor, "
    "themes, sample sticker briefs), produce concrete image-generation "
    "prompts for a main product image (cover/hero) and several supporting "
    "product images. Each prompt is a single English sentence-paragraph, "
    "fed into an image-to-image model whose reference is one of the pack's "
    "ACTUAL die-cut stickers — so describe the SCENE / STAGING / CAMERA / "
    "LIGHTING and insist the artwork is preserved, never the sticker design "
    "itself. Pick supporting angles that genuinely fit THIS pack's theme "
    "(not a fixed template). Output ONLY a JSON object matching the schema; "
    "no prose, no code fences."
)

IMAGE_DESIGN_INSTRUCTIONS = f"""
Design rules:
- "main" is always role_type "hero": a clean, scroll-stopping cover shot.
  Photorealistic e-commerce listing aesthetic.
- "secondary" angles are chosen from this menu to suit the pack — vary them,
  do NOT just repeat lifestyle/flat-lay/packaging every time:
{IMAGE_DESIGN_ROLE_MENU}

Pick secondary angles that best sell THIS specific pack and its current
product state. Do not force a fixed set of angles. Choose lifestyle, in_use,
flat_lay, full_set, packaging, or scene in whatever mix makes the listing more
convincing for this product.

Each "image_prompt" must:
- Be 1-3 English sentences, < 320 characters (before the fidelity clause).
- Reference the palette, style anchor, and 1-2 sample sticker subjects.
- Specify camera angle + lighting + surface/background.
- ALWAYS in English regardless of locale (the image model expects English);
  use the locale only to influence staging / props.
- End with exactly this sentence: "{IMAGE_DESIGN_FIDELITY_CLAUSE}"

Each "concept" is a 5-12 word internal label for the operator (e.g.
"hero on cream paper", "lifestyle laptop lid"). Set "role_type" to the
menu key you chose.
""".strip()


# role_type enum — keep in sync with IMAGE_DESIGN_ROLE_MENU keys.
IMAGE_DESIGN_ROLE_TYPES = [
    "hero", "lifestyle", "in_use", "flat_lay", "full_set", "packaging", "scene",
]

# Fixed prompt for the main "bundle" hero — the operator-approved style: every
# sticker densely clustered on pure white. It is fed a MERGED reference grid of
# the pack's preview sheets (built locally in image_utils.compose_reference_grid),
# so the model only recomposes real artwork rather than inventing a scene. This
# style is deterministic, so we don't leave it to the art-director model.
MAIN_BUNDLE_PROMPT = (
    "Studio e-commerce main image for a large sticker/card pack on a pure plain "
    "white background. Use the reference image as the complete visual source "
    "for the pack. If the reference contains multiple panels, sheets, or source "
    "images, sample visible sticker/card designs from EVERY panel/source, not "
    "just the first or most prominent one. Recompose those designs into one "
    "dense overlapping round or oval product collage, similar to marketplace "
    "bundle photos where dozens of cards fan and stack into a clean circular "
    "pile. Fill most of the frame, varied sizes and rotations, layered edges, "
    "subtle studio shadows, no props, no surface, no text overlay. Do not copy "
    "the reference grid layout or panel boundaries, and do not output a single "
    "sheet or one enlarged sticker. Preserve the source artwork style, colors, "
    "text, and quantity impression; do not invent unrelated new designs."
)

IMAGE_DESIGN_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["main", "secondary"],
    "properties": {
        "main": {
            "type": "object",
            "required": ["concept", "image_prompt"],
            "properties": {
                "concept":      {"type": "string", "maxLength": 100},
                "image_prompt": {"type": "string", "maxLength": 900},
                "role_type":    {"type": "string", "enum": IMAGE_DESIGN_ROLE_TYPES},
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
                    "image_prompt": {"type": "string", "maxLength": 900},
                    "role_type":    {"type": "string", "enum": IMAGE_DESIGN_ROLE_TYPES},
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
    selected_sticker_subjects: list[str] | None = None,
) -> str:
    sample_lines = "\n".join(f"- {b}" for b in sticker_briefs_sample[:12]) or "- (no brief)"
    preview_lines = "\n".join(f"- {p[:200]}" for p in preview_prompt_samples[:6]) or "- (no preview prompt)"
    selected = selected_sticker_subjects or []
    selected_block = (
        "\n### Hero candidates (operator-selected stickers — favor these)\n"
        + ("\n".join(f"- {s}" for s in selected[:8]))
        if selected else ""
    )
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
{selected_block}

### Existing preview prompts (used to render the stickers themselves)
{preview_lines}

### Output (JSON only)
Produce one "main" image spec (role_type "hero") and {secondary_count}
"secondary" image specs, each with a "concept" label, an English
"image_prompt", and a "role_type" from the menu in the instructions.

The prompts will be fed into an image-to-image model that takes ONE of the
pack's ACTUAL die-cut stickers as the visual reference, so the prompts must
describe the SCENE / STAGING / CAMERA / LIGHTING and insist the sticker
artwork is preserved — never describe or redraw the sticker design itself.
Choose supporting angles that genuinely fit this pack; vary them.
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
