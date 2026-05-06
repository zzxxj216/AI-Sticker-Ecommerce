"""Prompts for TK video caption two-step generation.

The video is a finished sticker pack showcase — operator already shot the
visuals. We just produce the *caption text + hashtags*. To save the
operator a re-roll, the model returns FOUR named caption variants in one
call (POV / Showcase / Trend / Story); the operator picks one in the UI.

Output language: English (overseas TikTok audience: US/UK/AU/CA).
"""

from __future__ import annotations


CAPTION_MAIN_SYSTEM_PROMPT = (
    "You write TikTok captions for an overseas sticker e-commerce brand. "
    "The video itself is already shot — it's a sticker-pack showcase. Your "
    "only job is the CAPTION TEXT + a tight set of hashtags that go under "
    "the video. All buyer-facing copy MUST be English (en-US), written "
    "naturally for Gen-Z TikTok users in the US/UK/AU/CA market. Output a "
    "markdown plan with sections — do NOT output JSON or code fences; a "
    "separate extractor pulls the structured fields. Keep each caption "
    "tight (<=150 chars), 1-2 emojis only if natural, no corporate-sounding "
    "language."
)


# Four caption styles the model fills in one shot. Names + notes are part
# of the prompt so the model writes distinctly different angles instead of
# four near-duplicates.
CAPTION_VARIANT_STYLES: list[dict[str, str]] = [
    {
        "key": "pov",
        "label": "POV / 共情",
        "note": "Open with 'POV:' or a relatable hook. Speak to the buyer's mood/identity rather than the product.",
    },
    {
        "key": "showcase",
        "label": "产品展示",
        "note": "Highlight what's IN the pack — sticker count, palette, what they decorate. Concrete and tactile.",
    },
    {
        "key": "trend",
        "label": "趋势 / 梗",
        "note": "Ride a current TikTok trend or meme phrasing. Punchy, scroll-stopping. Avoid being cringe.",
    },
    {
        "key": "story",
        "label": "小店 / 手作",
        "note": "Small-business / handmade angle. Behind-the-scenes warmth, gratitude, made-with-love framing.",
    },
]


def build_caption_main_prompt(
    *,
    pack_display_name: str,
    pack_archetype: str,
    style_anchor: str,
    palette: str,
    total_stickers: int,
    video_one_liner: str,
) -> str:
    """Markdown plan asking for 4 caption VARIANTS in distinct styles."""
    style_list_md = "\n".join(
        f"{i+1}. **{s['label']}** (key: `{s['key']}`) — {s['note']}"
        for i, s in enumerate(CAPTION_VARIANT_STYLES)
    )
    return f"""Write FOUR TikTok caption variants for this sticker-pack showcase video.
The operator will pick one — make each clearly distinct, not near-duplicates.

### Pack
- name: **{pack_display_name}**
- archetype: `{pack_archetype or 'general'}`
- {total_stickers} stickers, palette: {palette or 'multi-color'}
- visual style: {style_anchor[:300] if style_anchor else '(not specified)'}

### What the video shows (operator's one-liner)
{video_one_liner or '(not specified — the video is a generic sticker-pack showcase)'}

### Caption styles (write one variant for EACH)
{style_list_md}

### Output (markdown sections, no JSON)

For each style above, output ONE block in this EXACT order, using the
style's `key` as the heading suffix:

#### variant pov
- caption: <one paragraph, ≤150 chars, no hashtags inside>
- hashtags: 3-5 tags (NOT 8-15) — only the most critical. One per line, prefixed with `#`. Mix:
  - 1-2 broad sticker tags (#stickers, #stickerpack, #vinylstickers)
  - 1-2 niche/aesthetic tags matching the archetype (#aestheticstickers, #grad2026, etc.)
  - 0-1 trending TikTok tag (#tiktokmademebuyit, #smallbusinesstiktok) — only if it genuinely fits
- posting_tip: one short English sentence of operator advice (best time to post, etc.)

#### variant showcase
(same structure)

#### variant trend
(same structure)

#### variant story
(same structure)

Rules:
- Each caption must read naturally for the named style — POV ≠ Showcase.
- Total hashtags per variant: **3 to 5, never more**. Quality over quantity.
- No banned/spammy tags. No emoji-only captions.
- The operator will see all four side-by-side, so make the differences obvious.
"""


CAPTION_EXTRACT_INSTRUCTIONS = """
The markdown contains FOUR variants, each under a `#### variant <key>` heading
where <key> is one of: pov, showcase, trend, story.

Mapping rules:
- "variants": one object per `#### variant <key>` block, in order. Each object:
    {
      "key": <the key from the heading, e.g. "pov">,
      "caption": <text from "- caption: ..." line, plain text, no leading/
                  trailing whitespace, strip surrounding quotes and "**" bold>,
      "hashtags": <list of strings from "- hashtags:" lines, WITH leading "#",
                   strip markdown bullets, preserve order, max 5 items>,
      "posting_tip": <text from "- posting_tip: ..." line, plain text>
    }
- If a variant block is missing, omit it from the array (do not invent one).
- If a variant has no hashtags, return an empty list, not null.
""".strip()


CAPTION_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["variants"],
    "properties": {
        "variants": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["key", "caption", "hashtags"],
                "properties": {
                    "key":         {"type": "string"},
                    "caption":     {"type": "string", "maxLength": 200},
                    "hashtags":    {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                    "posting_tip": {"type": "string"},
                },
            },
        },
    },
}
