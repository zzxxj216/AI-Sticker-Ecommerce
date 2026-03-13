"""批量生成管线的 Prompt 模板

为 6 卡包并行生成提供所有 AI 调用所需的 prompt：
- 6 包方向 + 风格分配
- 单包多话题规划
- 按话题生成概念
- 话题预览合并
- 编辑意图解析
"""

from typing import Any, Dict, List, Optional


# ------------------------------------------------------------------
# 1) 6 卡包方向 + 风格分配
# ------------------------------------------------------------------

SIX_PACK_PLANNER = """You are a senior sticker product strategist. The user wants to generate **{pack_count} sticker packs** in parallel from one theme.

=== USER INPUT ===
Theme: {theme}
{style_section}
{extra_section}
=== END INPUT ===

Your task:
1. Design {pack_count} **distinct pack directions** derived from the theme. Each direction targets a different audience segment, use case, or emotional angle.
2. Assign each pack a **unique visual style** (unless the user explicitly asked for a unified style).
3. Assign each pack a **color mood** that complements its style.

{style_constraint}

Return a JSON object:

```json
{{
  "packs": [
    {{
      "pack_index": 1,
      "pack_name": "Short pack name (English, 3-6 words)",
      "direction": "One-sentence description of this pack's creative angle",
      "visual_style": "Art style (e.g. flat vector, hand-drawn watercolor, retro comic, pixel art, 3D render, minimalist line art)",
      "color_mood": "Color mood description (e.g. cyber blue-purple neon, warm earth tones, pastel candy)"
    }}
  ]
}}
```

Requirements:
- ALL output in English.
- Exactly {pack_count} packs.
- Pack directions must be distinct — cover different life scenarios, emotions, or target audiences.
- Visual styles must be distinct across packs (no two packs share the same art style) unless user requested unified style.
- Do NOT include any explanation outside the JSON block.
"""


def build_six_pack_planner_prompt(
    theme: str,
    pack_count: int = 6,
    user_style: Optional[str] = None,
    user_color_mood: Optional[str] = None,
    user_extra: str = "",
) -> str:
    style_section = ""
    if user_style:
        style_section = f"User-requested style: {user_style}"
    if user_color_mood:
        style_section += f"\nUser-requested color mood: {user_color_mood}"
    if not style_section:
        style_section = "(No style preference specified — assign diverse styles)"

    extra_section = f"Additional notes: {user_extra}" if user_extra else ""

    if user_style:
        style_constraint = (
            f'The user specified a style preference: "{user_style}". '
            f"All {pack_count} packs should derive from or interpret this style, "
            f"creating {pack_count} variations within the user's preferred aesthetic. "
            f"Styles should still be distinguishable but share a common DNA."
        )
    else:
        style_constraint = (
            f"The user did NOT specify a style. Assign {pack_count} "
            f"mutually distinct visual styles across packs."
        )

    return SIX_PACK_PLANNER.format(
        theme=theme,
        pack_count=pack_count,
        style_section=style_section,
        extra_section=extra_section,
        style_constraint=style_constraint,
    )


# ------------------------------------------------------------------
# 2) 单包多话题规划（凑齐 50-60 张，每话题 6-8）
# ------------------------------------------------------------------

TOPIC_PLANNER = """You are a sticker content strategist.

=== PACK INFO ===
Theme: {theme}
Pack direction: {direction}
Pack visual style: {visual_style}
Target total stickers: {target_count} (must be between 50 and 60)
Stickers per topic: {per_topic} (each topic should have {per_topic} stickers)
=== END PACK INFO ===

Design a list of **topics** for this sticker pack. Each topic is a sub-theme or life scenario within the pack's direction.

Rules:
- The total sticker count across all topics must be between 50 and 60.
- Each topic should have exactly {per_topic} stickers.
- Therefore you need approximately {topic_count_hint} topics.
- Topics should be distinct, relatable, and rooted in real daily-life scenarios.
- Think about what moments, emotions, and interactions people experience around this direction.

Return a JSON object:

```json
{{
  "topics": [
    {{
      "topic_id": "topic_01",
      "topic_name": "Topic Name (English, 2-5 words)",
      "description": "One-sentence description of what daily-life moment this topic captures",
      "sticker_count": {per_topic}
    }}
  ],
  "total_stickers": 56
}}
```

Requirements:
- ALL output in English.
- total_stickers must equal the sum of all sticker_counts and be between 50 and 60.
- Do NOT include any explanation outside the JSON block.
"""


def build_topic_planner_prompt(
    theme: str,
    direction: str,
    visual_style: str,
    target_count: int = 55,
    per_topic: int = 8,
) -> str:
    topic_count_hint = f"{target_count // per_topic}-{(target_count + per_topic - 1) // per_topic}"
    return TOPIC_PLANNER.format(
        theme=theme,
        direction=direction,
        visual_style=visual_style,
        target_count=target_count,
        per_topic=per_topic,
        topic_count_hint=topic_count_hint,
    )


# ------------------------------------------------------------------
# 3) 按话题生成贴纸概念
# ------------------------------------------------------------------

TOPIC_CONCEPTS_GENERATOR = """You are a creative sticker designer who specialises in creating visually diverse sticker collections.

=== PACK INFO ===
Theme: {theme}
Pack direction: {direction}
Visual style reference (overall mood): {visual_style}
Color mood reference: {color_mood}
=== END PACK INFO ===

=== TOPIC ===
Topic: {topic_name}
Description: {topic_description}
Required sticker count: {sticker_count}
=== END TOPIC ===

Design exactly {sticker_count} sticker concepts for this topic.

=== CRITICAL: VISUAL DIVERSITY ===
Each sticker within the same topic must share the SAME SUBJECT MATTER / THEME, but use DIFFERENT visual approaches. You MUST mix the following sticker types across the {sticker_count} stickers:

| Type | Count | What it is | Example |
|------|-------|-----------|---------|
| **text_only** | 1-2 | Bold typography-only design. NO characters or objects. Just stylised text with decorative font treatment (shadows, gradients, shapes). | "CTRL+Z My Life" in bold block letters with glitch effect |
| **element** | 2-3 | Pure visual icon / object / character / mascot. NO text on the sticker. A standalone illustration that tells the story visually. | A laptop with steam coming out, a cat sleeping on a keyboard |
| **combined** | 2-3 | Character or object WITH short text working together. Text and visual complement each other. | A robot holding a coffee cup with speech bubble "Not Now" |
| **pattern** | 0-1 | Repeating mini-icons or a seamless tile of small related objects. NO dominant single subject. | Tiny scattered coffee cups, code brackets, wifi symbols |
| **emoji_style** | 0-1 | A single expressive face/emotion icon, no body, no scene. Oversized expression fills the frame. | A giant melting smiley face, an eye-rolling expression |

IMPORTANT:
- Do NOT make every sticker a "character + text" combo. That kills diversity.
- For text_only stickers, text_overlay is the MAIN content (the text IS the sticker).
- For element stickers, text_overlay MUST be empty string "".
- For pattern stickers, text_overlay MUST be empty string "".
- For emoji_style stickers, text_overlay can be empty or 1-2 words max.
=== END DIVERSITY ===

Return a JSON object:

```json
{{
  "stickers": [
    {{
      "index": 1,
      "sticker_type": "text_only | element | combined | pattern | emoji_style",
      "description": "Detailed visual description (English, 1-2 sentences). Describe WHAT we see, not how to draw it.",
      "text_overlay": "Short text (or empty string if no text)"
    }}
  ]
}}
```

Requirements:
- ALL output in English.
- Exactly {sticker_count} stickers.
- MUST include at least 3 different sticker_type values across the {sticker_count} stickers.
- Mix emotions: funny, wholesome, sarcastic, motivational, dramatic, deadpan.
- For element stickers: describe an interesting composition, angle, or scenario — not just "a cute X doing Y".
- For text_only stickers: describe the typography treatment, layout, and decorative elements.
- Do NOT include any explanation outside the JSON block.
"""


def build_topic_concepts_prompt(
    theme: str,
    direction: str,
    visual_style: str,
    color_mood: str,
    topic_name: str,
    topic_description: str,
    sticker_count: int = 8,
) -> str:
    return TOPIC_CONCEPTS_GENERATOR.format(
        theme=theme,
        direction=direction,
        visual_style=visual_style,
        color_mood=color_mood,
        topic_name=topic_name,
        topic_description=topic_description,
        sticker_count=sticker_count,
    )


# ------------------------------------------------------------------
# 4) 按话题生成 image prompts（概念 → prompt）
# ------------------------------------------------------------------

TOPIC_IDEAS_CONVERTER = """You are an expert prompt engineer for AI image generation (Gemini Imagen).

=== STYLE GUIDE (for thematic reference — NOT a rigid visual template) ===
{style_guide_block}
=== END STYLE GUIDE ===

=== TOPIC ===
Topic: {topic_name}
=== END TOPIC ===

=== STICKER CONCEPTS ===
{concepts_block}
=== END CONCEPTS ===

For EACH concept above, write a detailed **English** image-generation prompt (50-90 words).

=== CRITICAL: DIVERSITY IN VISUAL APPROACH ===
The style guide defines the MOOD and COLOR PALETTE — it does NOT mean every sticker must look identical.
You MUST vary the visual approach based on each sticker's type:

- **text_only**: Focus on typography. Describe font weight, layout (stacked, diagonal, curved), decorative elements (underlines, shadows, backgrounds shapes). NO characters or objects. Use 2-3 colors from the palette.
- **element**: Focus on the subject/object. Describe composition, angle, lighting, expression, props. NO text in the image. Can be realistic, stylised, or abstract.
- **combined**: Describe both the visual subject AND how text integrates (speech bubble, banner, label, hand-held sign, floating text). 
- **pattern**: Describe the repeating elements, spacing, and overall tile look. Small, simplified icons scattered across the frame.
- **emoji_style**: Describe a single large expressive face/emotion filling most of the frame. Minimal detail, maximum expression.

DO NOT:
- Copy-paste the same line width, shadow angle, and hex codes into every prompt
- Make every sticker a "chibi character with text below"
- Use identical sentence structures across prompts
- Repeat the exact art_style description verbatim in every prompt

DO:
- Let each prompt stand alone as a unique visual description
- Use the palette colors naturally (not as "#hex" codes — describe colors by name)
- Vary composition: close-up, full-body, bird's-eye, isometric, flat lay, centered icon
- Vary rendering: some clean and minimal, some detailed, some with texture
=== END DIVERSITY ===

Each prompt must:
1. Describe WHAT we see — the subject, composition, colors, and mood.
2. If text_overlay is provided, describe the EXACT text content and how it appears visually.
3. Match the overall mood and color palette from the Style Guide (warm/cool, vibrant/muted).
4. End with: "thick white die-cut sticker border, isolated on white background."
5. Do NOT use the words: kawaii, cute sticker, sticker sheet.

Return a JSON array (no markdown fences):

[{{"index": 1, "sticker_type": "text_only|element|combined|pattern|emoji_style", "title": "Short English title (max 5 words)", "concept": "original description", "text_overlay": "original text", "image_prompt": "..."}}, ...]
"""


def build_topic_ideas_prompt(
    topic_name: str,
    concepts: List[Dict[str, Any]],
    style_guide: Dict[str, Any],
) -> str:
    from src.services.ai.prompt_builder import PromptBuilder
    style_guide_block = PromptBuilder._format_style_guide_block(style_guide)

    concepts_block = ""
    for c in concepts:
        idx = c.get("index", "?")
        stype = c.get("sticker_type", "combined")
        desc = c.get("description", "")
        text = c.get("text_overlay", "")
        text_info = f'  Text overlay: "{text}"' if text else "  (no text overlay)"
        concepts_block += f"  {idx}. [{stype}] {desc}\n{text_info}\n"

    return TOPIC_IDEAS_CONVERTER.format(
        style_guide_block=style_guide_block,
        topic_name=topic_name,
        concepts_block=concepts_block,
    )


# ------------------------------------------------------------------
# 5) 话题预览 prompt（合并该话题下多张 idea → 一张话题总图）
# ------------------------------------------------------------------

TOPIC_PREVIEW_PROMPT_BUILDER = """You are an expert prompt engineer specializing in AI image generation.

Your task is to write a **single image generation prompt** that produces a **topic preview sheet** — one image showing all stickers from this specific topic arranged together.

=== STYLE GUIDE (mood & palette reference) ===
{style_guide_block}
=== END STYLE GUIDE ===

=== TOPIC DETAILS ===
Topic: {topic_name}
Pack: {pack_name}
Sticker count: {sticker_count}

Sticker list:
{ideas_block}
=== END TOPIC DETAILS ===

Write the image generation prompt following these rules:

1. Start with: "A sticker collection sheet for the topic \\"{topic_name}\\"."
2. Layout: stickers in an organized grid on a white background.
3. Briefly describe each sticker's appearance (10-20 words each). IMPORTANT: preserve the DIVERSITY of sticker types — some are text-only, some are pure illustrations, some are character+text combos, some are patterns. Do NOT describe them all the same way.
4. Use the style guide's color palette and mood as a unifying thread, but allow each sticker to have its own visual character.
5. Each sticker has "thick white die-cut border".
6. Background: "isolated on clean white background".
7. End with: "Professional graphic design, high resolution, diverse sticker art styles unified by a cohesive color palette."
8. Total prompt: 150-300 words.

Return ONLY the prompt text. No JSON, no markdown fences.
"""


def build_topic_preview_meta_prompt(
    topic_name: str,
    pack_name: str,
    sticker_ideas: List[Dict[str, Any]],
    style_guide: Dict[str, Any],
) -> str:
    from src.services.ai.prompt_builder import PromptBuilder
    style_guide_block = PromptBuilder._format_style_guide_block(style_guide)

    ideas_block = ""
    for idea in sticker_ideas:
        idx = idea.get("index", "?")
        title = idea.get("title", "Untitled")
        concept = idea.get("concept", "")
        text = idea.get("text_overlay", idea.get("sticker_text", ""))
        text_info = f'  Text: "{text}"' if text else ""
        ideas_block += f"  {idx}. {title} — {concept}{text_info}\n"

    return TOPIC_PREVIEW_PROMPT_BUILDER.format(
        style_guide_block=style_guide_block,
        topic_name=topic_name,
        pack_name=pack_name,
        sticker_count=len(sticker_ideas),
        ideas_block=ideas_block,
    )


# ------------------------------------------------------------------
# 6) 编辑意图解析
# ------------------------------------------------------------------

EDIT_INTENT_PARSER = """You are a JSON extraction bot for a sticker editing system.

The user wants to modify a specific sticker in a batch of 6 sticker packs.

=== AVAILABLE PACKS ===
{packs_summary}
=== END PACKS ===

=== USER MESSAGE ===
{user_message}
=== END MESSAGE ===

Parse the user's intent and return a JSON object:

```json
{{
  "pack_index": 1,
  "topic_identifier": "topic name or topic index (1-based)",
  "sticker_index_in_topic": 3,
  "action": "modify | view | regenerate",
  "new_prompt": "new image prompt if user provided one (or null)",
  "modification_intent": "user's modification intent in English (e.g. 'make it warmer', 'remove text')",
  "understood": true
}}
```

Rules:
- pack_index: 1-6, which pack the user is referring to. Default 1 if not specified.
- topic_identifier: topic name (e.g. "加班", "Overtime") or topic index (e.g. 2 for the 2nd topic).
- sticker_index_in_topic: which sticker within the topic (1-based).
- action: "modify" if changing prompt, "view" if just viewing, "regenerate" if re-generating with same prompt.
- understood: false if the user's intent is unclear.
- Return JSON only, no explanation.
"""


def build_edit_intent_prompt(
    user_message: str,
    packs_summary: str,
) -> str:
    return EDIT_INTENT_PARSER.format(
        packs_summary=packs_summary,
        user_message=user_message,
    )


# ------------------------------------------------------------------
# 7) 修改 prompt（意图 → 新 prompt）
# ------------------------------------------------------------------

PROMPT_MODIFIER = """You are a sticker prompt editor.

=== CURRENT PROMPT ===
{current_prompt}
=== END CURRENT PROMPT ===

=== STYLE GUIDE ===
{style_guide_block}
=== END STYLE GUIDE ===

=== MODIFICATION REQUEST ===
{modification_intent}
=== END REQUEST ===

Rewrite the image prompt to incorporate the user's modification while keeping the style guide consistent.

Return ONLY the new prompt text (50-90 words). No JSON, no explanation.
"""


def build_prompt_modifier(
    current_prompt: str,
    modification_intent: str,
    style_guide: Dict[str, Any],
) -> str:
    from src.services.ai.prompt_builder import PromptBuilder
    style_guide_block = PromptBuilder._format_style_guide_block(style_guide)

    return PROMPT_MODIFIER.format(
        current_prompt=current_prompt,
        style_guide_block=style_guide_block,
        modification_intent=modification_intent,
    )


# ------------------------------------------------------------------
# 8) 批量模式 style guide（强调主题一致而非视觉克隆）
# ------------------------------------------------------------------

BATCH_STYLE_GUIDE = """You are an expert visual-identity designer for sticker packs sold on Etsy, Redbubble, and Amazon.

Theme: {theme}
Pack direction: {direction}
Requested visual style: {visual_style}
Requested color mood: {color_mood}

Create a **Pack Style Guide** for this sticker pack. This guide defines the THEMATIC IDENTITY — the mood, color palette, and overall aesthetic that ties the pack together.

IMPORTANT: This pack will contain DIVERSE sticker types (text-only typography, standalone illustrations, character+text combos, patterns, emoji faces). The style guide must be flexible enough to accommodate all these types while maintaining a cohesive feel.

The consistency comes from:
- A shared COLOR PALETTE (same 5 colors used across all stickers)
- A shared MOOD / VIBE (same emotional tone)
- A shared QUALITY LEVEL (all look professional and polished)

The consistency does NOT mean:
- Every sticker uses the same line width or outline style
- Every sticker has the same composition layout
- Every sticker must include the same type of character

Return a JSON object:

```json
{{
  "art_style": "Overall aesthetic in one sentence. Must accommodate both text-only and illustrated stickers.",
  "color_palette": {{
    "primary": "#hex — main brand color",
    "secondary": "#hex — supporting color",
    "accent": "#hex — pop/highlight color",
    "background": "#hex — light neutral",
    "text_color": "#hex — readable on backgrounds"
  }},
  "line_style": "Default stroke treatment (but individual sticker types may vary)",
  "mood": "3-5 adjectives for the overall emotional vibe",
  "typography_style": "Font treatment for stickers that include text",
  "visual_consistency_rules": [
    "All stickers share the same 5-color palette",
    "Rule about overall quality level",
    "Rule about mood consistency",
    "Rule about what makes stickers feel like a set despite different visual types"
  ]
}}
```

Requirements:
- The art_style should be a flexible umbrella description, not a rigid single technique.
- The palette MUST reflect "{color_mood}".
- visual_consistency_rules should focus on THEMATIC unity, not visual cloning.
- Do NOT include any explanation outside the JSON block.
"""


def build_batch_style_guide_prompt(
    theme: str,
    direction: str,
    visual_style: str,
    color_mood: str,
) -> str:
    return BATCH_STYLE_GUIDE.format(
        theme=theme,
        direction=direction,
        visual_style=visual_style,
        color_mood=color_mood,
    )
