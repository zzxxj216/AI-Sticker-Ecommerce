"""Prompts for AI video-script generation.

Caption-only (no voiceover). Each scene gets a screen caption + b-roll
brief (Chinese, for the operator) + suggested hashtags. Music is a style
description + 3 candidate tracks.
"""

from __future__ import annotations


SCRIPT_SYSTEM_PROMPT = (
    "You are a TikTok short-video producer for an overseas sticker brand. "
    "You produce CAPTION-ONLY scripts (no voiceover). Output a markdown plan "
    "with the requested sections — do NOT output JSON; a separate extractor "
    "will pull the structured fields. All buyer-facing copy in English (en-US). "
    "Operator-facing direction (b_roll_brief) in Chinese."
)


def build_script_main_prompt(
    *,
    template_name: str,
    music_style: str,
    pack_display_name: str,
    pack_archetype: str,
    style_anchor: str,
    palette: str,
    total_stickers: int,
    sticker_briefs_sample: list[str],
    seller_sku: str,
    scene_blueprint: list[dict],
    variant_label: str = "A",
) -> str:
    """Build the LLM prompt for ONE script variant.

    ``scene_blueprint`` is a list of fixed scenes from the chosen template:
        [{"kind","label","duration_s","intent","visual_brief"}, ...]
    The model fills caption + b_roll_brief + hashtags per scene, plus
    3 candidate music tracks.
    """
    sample_lines = (
        "\n".join(f"- {b}" for b in sticker_briefs_sample[:8])
        or "- (no sample available)"
    )
    scene_lines = []
    for i, s in enumerate(scene_blueprint, start=1):
        scene_lines.append(
            f"  Scene {i} — kind=`{s.get('kind','')}` "
            f"label=\"{s.get('label','')}\" "
            f"duration={s.get('duration_s',0)}s\n"
            f"     intent: {s.get('intent','')}\n"
            f"     visual_brief: {s.get('visual_brief','')}"
        )
    scenes_block = "\n".join(scene_lines) or "  (no scenes)"

    return f"""Plan a TikTok short-video script (variant {variant_label}) for this sticker pack.

### Template
- name: **{template_name}**
- music style: {music_style or '(operator chooses)'}
- variant: {variant_label}

### Pack
- name: **{pack_display_name}**
- archetype: `{pack_archetype or 'general'}`
- {total_stickers} stickers
- palette: {palette or 'multi-color'}
- visual style: {style_anchor[:300] if style_anchor else '(general)'}
- seller_sku: {seller_sku or '(none)'}

### Sample sticker contents
{sample_lines}

### Scene blueprint (fixed structure — fill copy for each)
{scenes_block}

### Output (markdown sections, no JSON)

#### music_suggestions
Three candidate background music styles, one per line. Each line:
``<short name> | <mood>` `<bpm>` | <one-sentence note about why it fits>
Example:
``Soft Sunshine Vlog | uplifting wholesome | 95 bpm | matches cozy daily-use feel``

#### scenes
For each scene above, output ONE block in this EXACT order:

##### scene N
- caption: a single screen-caption line (≤80 chars). Punchy, scannable.
  Use English. NO emojis unless required by intent. NO hashtags inside the caption.
- b_roll_brief: a 1-2 sentence operator-facing instruction in **Chinese**
  describing what to film / record / overlay. Mention concrete shots
  (close-up, top-down, hands, props, transition).
- hashtags: 3-6 short tags WITHOUT # prefix, one per line, lowercase.

Output format example for one scene:
``````
##### scene 1
- caption: POV: you found the perfect mood sticker pack
- b_roll_brief: 镜头快切到一张梗贴纸放大特写，用 0.5s 转场+大字幕"POV..."钩住注意力。
- hashtags:
  funnystickers
  introvert
  laptopstickers
``````

#### overall_notes
2-3 short bullet points the operator should keep in mind across all scenes
(e.g. consistent palette / lighting / color grading). English or Chinese OK.
"""


SCRIPT_EXTRACT_INSTRUCTIONS = """
Mapping rules:
- "music_suggestions": pull from #### music_suggestions section. One object per
  non-empty line. Format `name | mood | bpm | note`. Split on " | ". Output:
  [{"name": str, "mood": str, "bpm": str, "note": str}].
- "scenes": pull from #### scenes section. One object per "##### scene N"
  block, in order. Each scene object:
    {"idx": int (from "scene N"),
     "caption": str (from "- caption: ..."),
     "b_roll_brief": str (from "- b_roll_brief: ..."),
     "hashtags": list of strings (from "- hashtags:" lines, strip leading "#" if present)}
- "overall_notes": pull from #### overall_notes section. List of strings,
  one per non-empty bullet line (strip leading "- ", "* ").
- If a section is missing, use empty string / empty list, but always include
  the field.
""".strip()


SCRIPT_EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["scenes", "music_suggestions"],
    "properties": {
        "music_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "mood": {"type": "string"},
                    "bpm":  {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["idx", "caption", "b_roll_brief"],
                "properties": {
                    "idx":          {"type": "integer"},
                    "caption":      {"type": "string", "maxLength": 200},
                    "b_roll_brief": {"type": "string"},
                    "hashtags":     {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "overall_notes": {"type": "array", "items": {"type": "string"}},
    },
}
