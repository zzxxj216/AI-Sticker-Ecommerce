"""Prompts for TK video caption two-step generation.

Output language is English (overseas TikTok audience). Operator-facing
guidance in the system prompt is in English so the model doesn't drift
into Chinese.
"""

from __future__ import annotations


CAPTION_MAIN_SYSTEM_PROMPT = (
    "You write TikTok captions for an overseas sticker e-commerce brand. "
    "All buyer-facing copy MUST be English (en-US), written naturally for "
    "Gen-Z TikTok users in the US/UK/AU/CA market. Do not output JSON or "
    "code fences — write a short markdown plan with sections so a separate "
    "extractor can pull out the structured fields. Keep the caption tight "
    "(<=150 chars), include 1-2 emojis only if natural, and avoid corporate-"
    "sounding language."
)


def build_caption_main_prompt(
    *,
    pack_display_name: str,
    pack_archetype: str,
    style_anchor: str,
    palette: str,
    total_stickers: int,
    video_one_liner: str,
) -> str:
    """Markdown plan asking for: hook, caption, hashtags, posting tip."""
    return f"""Plan a TikTok video caption for this sticker pack.

### Pack
- name: **{pack_display_name}**
- archetype: `{pack_archetype or 'general'}`
- {total_stickers} stickers, palette: {palette or 'multi-color'}
- visual style: {style_anchor[:300] if style_anchor else '(not specified)'}

### What the video shows (operator's one-liner)
{video_one_liner or '(not specified — write a generic showcase caption)'}

### Output (markdown sections, no JSON)

#### hook
A 5-10 word opening line that stops the scroll. Should NOT include the
caption itself, just the opening hook line.

#### caption
The full caption text. Maximum 150 characters. Must work without any
hashtags. Conversational, natural Gen-Z tone. Do NOT include hashtags
in this section — list them separately below.

#### hashtags
8-15 hashtags, each on its own line, prefixed with `#`. Mix:
- 2-3 broad (#stickers, #stickercollection, #vinylstickers)
- 3-5 niche/aesthetic (matching the archetype, e.g. #blackgoldstickers,
  #aestheticstickers, #grad2026)
- 2-3 trending TikTok-style (#tiktokmademebuyit, #smallbusinesstiktok,
  #etsyseller)
- 1-2 product-fit (#waterproofstickers, #laptopstickers)
Avoid banned/spammy tags. No more than 15 total.

#### posting_tip
One sentence of operator-facing English advice (e.g. "Post 7-10pm EST
weekday for grad season audience"). Not part of the caption itself.
"""


CAPTION_EXTRACT_INSTRUCTIONS = """
Mapping rules:
- "hook": pull from the #### hook section, plain text, no markdown.
- "caption": pull from #### caption section, plain text, no leading/
  trailing whitespace, no surrounding quotes. Strip any "**" bold markers.
- "hashtags": pull from #### hashtags section. One string per tag,
  WITH the leading "#" included. Strip markdown bullets. Preserve order.
- "posting_tip": pull from #### posting_tip section, plain text.
- If a section is missing, use empty string / empty list.
""".strip()


CAPTION_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["hook", "caption", "hashtags", "posting_tip"],
    "properties": {
        "hook":         {"type": "string"},
        "caption":      {"type": "string", "maxLength": 200},
        "hashtags":     {"type": "array", "items": {"type": "string"}},
        "posting_tip":  {"type": "string"},
    },
}
