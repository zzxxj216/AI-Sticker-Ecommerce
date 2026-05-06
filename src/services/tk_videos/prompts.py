"""Prompts for TK video caption two-step generation.

The video is a finished sticker pack showcase — operator already shot the
visuals. We just produce the *caption text + hashtags*. To save the
operator a re-roll, the model returns FOUR named caption variants in one
call (POV / Showcase / Trend / Story); the operator picks one in the UI.

Output language: English (overseas TikTok audience: US/UK/AU/CA).
"""

from __future__ import annotations


CAPTION_MAIN_SYSTEM_PROMPT = (
    "You write natural TikTok captions that pair with finished sticker-pack "
    "showcase videos. This is NOT a hard-sell product listing or ad script. "
    "Your job is only the CAPTION TEXT + a tight set of hashtags that go "
    "under the video. The caption should feel like a creator casually posting "
    "a cute/aesthetic sticker-pack video: visual, mood-driven, human, and "
    "easy to read. All public-facing copy MUST be English (en-US), written "
    "naturally for TikTok users in the US/UK/AU/CA market. Output a markdown "
    "plan with sections — do NOT output JSON or code fences; a separate "
    "extractor pulls the structured fields. Keep each caption tight "
    "(<=140 chars), 0-1 emoji only if natural, no corporate-sounding "
    "language, no direct sales CTA."
)


# Four caption styles the model fills in one shot. Names + notes are part
# of the prompt so the model writes distinctly different angles instead of
# four near-duplicates.
CAPTION_VARIANT_STYLES: list[dict[str, str]] = [
    {
        "key": "pov",
        "label": "POV / 氛围共情",
        "note": "Open with 'POV:' only if it feels natural. Capture the viewer's mood/identity; do not pitch the product.",
    },
    {
        "key": "showcase",
        "label": "画面配文",
        "note": "Caption what the video feels like visually. Mention 1-2 standout motifs/colors, not a feature list or use-case list.",
    },
    {
        "key": "trend",
        "label": "TikTok 语感",
        "note": "Use casual TikTok-native phrasing without pretending to know real-time trends. Understated, punchy, not cringe.",
    },
    {
        "key": "story",
        "label": "小故事 / 收藏感",
        "note": "Give the pack a tiny mood/story or collector feeling. Warm and human, but not a small-business sales pitch.",
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
    return f"""Write FOUR natural TikTok caption variants to pair with this sticker-pack video.
The operator will pick one — make each clearly distinct, not near-duplicates.
These captions should feel like video captions, not product ads.

### Pack
- name: **{pack_display_name}**
- archetype: `{pack_archetype or 'general'}`
- {total_stickers} stickers, palette: {palette or 'multi-color'}
- visual style: {style_anchor[:300] if style_anchor else '(not specified)'}

### What the video shows (operator's one-liner)
{video_one_liner or '(not specified — the video is a generic sticker-pack showcase)'}

Use the pack details as context, not as a checklist. If the one-liner is empty,
write a safe mood/aesthetic caption based on the pack name and visual style.

### Caption styles (write one variant for EACH)
{style_list_md}

### Output (markdown sections, no JSON)

For each style above, output ONE block in this EXACT order, using the
style's `key` as the heading suffix:

#### variant pov
- caption: <one paragraph, ≤140 chars, no hashtags inside>
- hashtags: 3-5 tags (NOT 8-15) — only the most critical. One per line, prefixed with `#`. Mix:
  - 1-2 broad sticker tags (#stickers, #stickerpack, #vinylstickers)
  - 1-2 niche/aesthetic tags matching the archetype (#aestheticstickers, #grad2026, etc.)
  - 0-1 TikTok-native tag (#tiktokmademebuyit, #smallbusinesstiktok) — only if it genuinely fits
- posting_tip: one short English sentence of operator advice (best time to post, etc.)

#### variant showcase
(same structure)

#### variant trend
(same structure)

#### variant story
(same structure)

Rules:
- Each caption must read naturally for the named style — POV ≠ Visual Caption.
- Prioritize mood, scene, aesthetic, and identity over product specs.
- Do NOT sound like pure selling. Avoid: "buy now", "shop now", "grab yours",
  "must-have", "perfect for laptop/water bottle/journal", "limited stock",
  "waterproof vinyl", "small business made with love", or price/shipping claims.
- Do NOT cram in sticker count, materials, or a long list of use cases unless
  the operator's one-liner explicitly says the video shows that.
- Avoid overused AI-sounding phrases like "your personality is basically...",
  "not me needing my whole life...", and repeated "...in sticker form" formulas.
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
