"""Shopify product image style — a FIXED set of AI image-to-image prompts.

Unlike the TikTok pipeline (which lets GPT pick secondary roles), Shopify gets a
deterministic, consistent 5-image style so every product looks on-brand:

  1. overview  (main)      — all designs on a clean pure-white background
  2. laptop    (secondary) — stickers applied on a laptop / notebook
  3. bottle    (secondary) — stickers applied on a stainless water bottle
  4. handhold  (secondary) — a hand peeling one die-cut sticker, pile behind
  5. flatlay   (secondary) — top-down arrangement with a coin/ruler for scale

All prompts are image-to-image: the reference is the pack's ACTUAL stickers, so
each prompt describes the SCENE / STAGING / CAMERA / LIGHTING and insists the
artwork is preserved. ``ref`` picks the reference: "grid" = a merged grid of all
stickers (whole-pack shots); "single" = one die-cut sticker (single-subject).

Backgrounds/lighting are kept consistent (pure white #FFFFFF for the hero, soft
even daylight everywhere) per e-commerce best practice (clean hero + lifestyle).
"""

from __future__ import annotations

# Preserve-the-artwork clause appended to every prompt (mirrors the TikTok one).
FIDELITY = (
    " Preserve the exact sticker artwork, colors and text from the reference "
    "image — do not redraw, restyle, recolor, blur, or add any new text, "
    "watermark or logo. Photorealistic e-commerce product photography, soft "
    "even daylight, sharp focus."
)

# Each item: style_key, role, ref strategy, and the fixed scene prompt.
SHOPIFY_IMAGE_STYLE: list[dict] = [
    {
        "key": "overview",
        "role": "main",
        "ref": "grid",
        "prompt": (
            "Studio e-commerce hero image of a vinyl sticker pack on a pure "
            "plain white background (#FFFFFF), no surface, no props, no shadow "
            "clutter. Use the reference as the complete source of designs and "
            "lay every distinct sticker out in a tidy, evenly-spaced fan / loose "
            "grid that fills most of the frame — each design clearly visible and "
            "separated, slight overlap only, clean die-cut white borders. Bright "
            "even studio light, crisp and high-resolution, marketplace 'buy-me' "
            "look. Do not output a single enlarged sticker or copy the reference "
            "grid lines."
        ),
    },
    {
        "key": "laptop",
        "role": "secondary",
        "ref": "single",
        "prompt": (
            "Photorealistic lifestyle photo: a few die-cut vinyl stickers from "
            "the reference applied on the lid of a modern silver laptop, resting "
            "on a light wood / light grey desk with soft natural window light "
            "from the left. Slight angle, shallow depth of field, cozy creator "
            "workspace. The stickers sit flat and realistic on the surface."
        ),
    },
    {
        "key": "bottle",
        "role": "secondary",
        "ref": "single",
        "prompt": (
            "Photorealistic lifestyle photo: die-cut vinyl stickers from the "
            "reference applied on a brushed stainless-steel insulated water "
            "bottle / tumbler standing on a clean neutral surface, bright airy "
            "daylight, soft reflections on the metal. The stickers curve "
            "naturally with the bottle and look durable and waterproof."
        ),
    },
    {
        "key": "handhold",
        "role": "secondary",
        "ref": "grid",
        "prompt": (
            "Photorealistic e-commerce product photo in ONE continuous frame: a "
            "hand enters from the lower-left, thumb and index finger pinching ONE "
            "die-cut vinyl sticker at the center in sharp focus, its clean white "
            "die-cut border clearly visible. The background is filled with a "
            "dense overlapping pile of many different stickers from the same "
            "pack, softly blurred with shallow depth of field so the held "
            "sticker pops. Bright even studio daylight, clean marketplace look. "
            "No envelope, no packaging, no text overlay."
        ),
    },
    {
        "key": "flatlay",
        "role": "secondary",
        "ref": "grid",
        "prompt": (
            "Top-down flat-lay product photo on a clean white surface: 5–7 "
            "die-cut vinyl stickers from the reference arranged neatly with even "
            "spacing, and a real coin (US quarter) placed beside them for size "
            "reference. Bright even overhead daylight, subtle soft shadows, sharp "
            "focus, true-to-life colors so a buyer can judge the sticker size."
        ),
    },
]


def style_prompt(item: dict) -> str:
    """Full prompt for a style item (scene + fidelity clause)."""
    return (item.get("prompt") or "").strip() + FIDELITY
