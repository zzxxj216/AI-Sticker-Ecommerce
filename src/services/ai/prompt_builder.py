"""Prompt 构建器

提供统一的 Prompt 模板管理和构建功能，用于：
- 主题内容扩展（Theme Content Generation）
- 贴纸包生成
- 风格分析
- 变种生成
"""

from typing import Dict, Any, List, Optional
from src.core.constants import StickerType, VariationDegree


class PromptBuilder:
    """Prompt 构建器基类"""

    @staticmethod
    def build_theme_content_prompt(
        theme: str,
        max_topics: int = 12,
        max_keywords: int = 20,
        max_phrases: int = 15,
    ) -> str:
        """Build a prompt that expands a user-provided theme into
        trending English content suitable for sticker text designs.

        Args:
            theme: Raw theme input (any language)
            max_topics: Number of trending related topics to generate
            max_keywords: Number of keywords to generate
            max_phrases: Number of short sticker-ready phrases to generate

        Returns:
            str: The prompt string
        """
        prompt = f"""You are a trend-savvy cultural analyst and creative copywriter who specialises in global pop culture, internet culture, and social media trends.

The user has provided a theme: "{theme}"

Your task is to expand this theme into rich, trending, English-language content that resonates with a global (primarily English-speaking) audience. Think about what is currently popular, viral, or culturally relevant around this theme.

Please return a JSON object with the following structure:

```json
{{
  "theme_english": "The theme translated / interpreted into a concise English name",
  "theme_description": "A 1-2 sentence English description of the theme and why it is culturally relevant right now",

  "trending_topics": [
    {{
      "name": "Topic or brand name (e.g. ChatGPT, Tesla, Taylor Swift)",
      "category": "One of: brand | person | event | meme | technology | movement | media | other",
      "description": "One sentence explaining what it is and why it is trending",
      "popularity": "high | medium",
      "hashtags": ["#relevant", "#hashtags"]
    }}
  ],

  "keywords": [
    "keyword1", "keyword2"
  ],

  "slang_and_memes": [
    {{
      "text": "A popular slang term, meme phrase, or internet expression related to the theme",
      "meaning": "Brief explanation",
      "origin": "Where it comes from (e.g. TikTok, Reddit, Twitter/X)"
    }}
  ],

  "sticker_phrases": [
    {{
      "text": "Short punchy English phrase suitable for a sticker (2-6 words)",
      "emotion": "The emotion it conveys (e.g. excitement, humor, sarcasm, motivation)",
      "use_case": "When someone would use this sticker (one sentence)"
    }}
  ],

  "color_moods": [
    {{
      "mood": "A mood or vibe associated with the theme",
      "colors": ["#hex1", "#hex2", "#hex3"],
      "description": "Why these colors fit"
    }}
  ]
}}
```

Requirements:
- ALL output must be in English, even if the input theme is in another language.
- "trending_topics": generate exactly {max_topics} items. Focus on what is genuinely popular or culturally significant RIGHT NOW (2024-2026). Include well-known brands, people, tools, events, memes, or movements closely associated with the theme.
- "keywords": generate exactly {max_keywords} single-word or short-phrase keywords that are relevant, SEO-friendly, and reflect current trends.
- "slang_and_memes": generate 8-10 items. Include internet slang, viral phrases, meme references, or Gen-Z / millennial expressions related to the theme.
- "sticker_phrases": generate exactly {max_phrases} items. These must be short (2-6 words), punchy, and work well as standalone sticker text. Mix emotions: funny, motivational, sarcastic, wholesome, etc.
- "color_moods": generate 3-4 mood-color palettes that fit the theme aesthetically.
- Be creative, culturally aware, and trend-conscious. Avoid generic or outdated references.
- Do NOT include any explanation outside the JSON block.
"""
        return prompt

    # ------------------------------------------------------------------
    # Topic generation (v3 pipeline — Step 0)
    # ------------------------------------------------------------------

    @staticmethod
    def build_topic_generation_prompt(
        theme: str,
        max_topics: int = 6,
    ) -> str:
        """Build a prompt that generates topic ideas from a user-provided theme.

        Each topic includes use cases (when/where users would use stickers
        from this topic) and a recommended visual style type.

        Args:
            theme: Raw theme input (any language)
            max_topics: Number of topic ideas to generate

        Returns:
            str: The prompt string
        """
        prompt = f"""You are a senior sticker product strategist who deeply understands how real people express themselves in everyday life.

The user has provided a broad theme: "{theme}"

Your task is to brainstorm {max_topics} distinct **sticker topics** derived from this theme. Each topic must be rooted in **real daily-life scenarios** — the moments, emotions, and social interactions that ordinary people actually experience around this theme.

Think from the user's perspective:
- What emotions do people feel in daily life related to this theme? (frustration, joy, pride, exhaustion, humor...)
- What specific moments trigger the need to express those feelings? (morning routines, work situations, conversations with friends, late-night thoughts...)
- What would make someone laugh, nod in agreement, or feel "this is so me" when they see the sticker?

For each topic, identify:
1. **Use cases** — where would people **physically stick** these printed stickers in real life? Think about the actual surfaces and objects: laptops, water bottles, phone cases, notebooks, planners, car bumpers, helmets, skateboards, toolboxes, guitar cases, locker doors, luggage, gift wrapping, scrapbooks, etc. Be specific about which items and contexts fit this topic. (e.g. "Stick on a developer's laptop lid", "Decorate a reusable water bottle for the gym", "Add to a travel journal or planner").
2. **Style type** — the single best visual art style that suits this topic's vibe and audience (e.g. "flat vector illustration", "hand-drawn sketch", "3D render", "pixel art", "watercolor", "retro comic", "minimalist line art", "bold pop art").
3. **Keywords** — 5-8 keywords that capture the topic's essence.

Return a JSON object with exactly this structure:

```json
{{
  "theme_english": "The theme translated / interpreted into a concise English name",
  "topics": [
    {{
      "name": "Topic name (concise English, 2-5 words)",
      "description": "One-sentence description — focus on what everyday emotion or life moment this topic captures and why people relate to it",
      "use_cases": [
        "Where to stick it (e.g. 'On a laptop lid to show off tech identity')",
        "Another physical use",
        "Another physical use"
      ],
      "style_type": "Recommended art style (one specific style name)",
      "keywords": ["keyword1", "keyword2", "keyword3"]
    }}
  ]
}}
```

Requirements:
- ALL output must be in English, even if the input theme is in another language.
- Generate exactly {max_topics} topics.
- **Topics must feel personal and relatable** — rooted in real daily life. Think about what kind of person would buy this sticker and where they would proudly display it. Avoid generic, corporate, or overly abstract topics.
- Cover a range of audiences and life contexts: tech workers, students, fitness lovers, travelers, hobbyists, pet owners, foodies, etc.
- Each topic should have 3-5 use_cases describing **specific physical places or items** where someone would stick these stickers.
- style_type should be a single, concrete art style. Choose the style that best matches the topic's visual appeal on physical surfaces.
- keywords should be SEO-friendly and reflect current trends (2024-2026).
- Think about what sells well on Etsy, Redbubble, and Amazon — die-cut vinyl stickers that people actually buy and stick on their belongings.
- Do NOT include any explanation outside the JSON block.
"""
        return prompt

    # ------------------------------------------------------------------
    # Style Guide + Type-specific sticker prompts (v2 pipeline)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_style_guide_block(style_guide: Dict[str, Any]) -> str:
        """Render the style guide dict as a text block for prompt injection."""
        palette = style_guide.get("color_palette", {})
        rules = style_guide.get("visual_co·nsistency_rules", [])
        rules_str = "\n".join(f"  - {r}" for r in rules) if rules else "  (none)"

        return f"""=== PACK STYLE GUIDE (mandatory -- every sticker MUST follow this) ===
Art style: {style_guide.get('art_style', 'N/A')}
Color palette:
  primary {palette.get('primary', '#000')}  |  secondary {palette.get('secondary', '#888')}  |  accent {palette.get('accent', '#FF0')}
  background {palette.get('background', '#FFF')}  |  text color {palette.get('text_color', '#000')}
Line style: {style_guide.get('line_style', 'N/A')}
Mood: {style_guide.get('mood', 'N/A')}
Typography: {style_guide.get('typography_style', 'N/A')}
Consistency rules:
{rules_str}
=== END STYLE GUIDE ==="""

    @staticmethod
    def build_pack_style_guide_prompt(theme_content: Dict[str, Any]) -> str:
        """Build a prompt that generates a unified Pack Style Guide.

        This is Claude call 0 -- it runs before any type-specific sticker
        generation so all three types share one visual identity.

        Args:
            theme_content: ThemeContent.to_dict() output

        Returns:
            str: The prompt string
        """
        color_moods = theme_content.get("color_moods", [])
        moods_str = ""
        for cm in color_moods:
            colors = ", ".join(cm.get("colors", []))
            moods_str += f"  - {cm.get('mood', '')}: {colors} -- {cm.get('description', '')}\n"

        prompt = f"""You are an expert visual-identity designer for sticker packs sold on global marketplaces (Etsy, Redbubble, Telegram).

Theme: {theme_content.get('theme_english', '')}
Description: {theme_content.get('theme_description', '')}

Reference color moods from market research:
{moods_str if moods_str else '  (none provided)'}

Your task is to create a **Pack Style Guide** -- a single, cohesive visual-identity specification that will be applied to EVERY sticker in this pack (text-only, element, and combined types alike).

Return a JSON object with exactly this structure:

```json
{{
  "art_style": "Describe the art style in one sentence (e.g. flat vector illustration with rounded shapes and soft shadows)",
  "color_palette": {{
    "primary": "#hex",
    "secondary": "#hex",
    "accent": "#hex",
    "background": "#hex",
    "text_color": "#hex"
  }},
  "line_style": "Describe stroke/outline treatment (e.g. 2px rounded stroke, no hard edges)",
  "mood": "3-5 adjectives describing the overall vibe (e.g. playful, tech-forward, optimistic)",
  "typography_style": "Describe the font/text treatment (e.g. bold sans-serif, slightly rounded, all-caps for emphasis)",
  "visual_consistency_rules": [
    "Rule 1 — e.g. Max 3 colors per sticker from the palette",
    "Rule 2 — e.g. Shadows always bottom-right, 15-degree angle",
    "Rule 3 — e.g. All icons share the same corner radius",
    "Rule 4 — e.g. Text stickers use the same background shape"
  ]
}}
```

Requirements:
- The palette should be derived from the reference color moods but refined into exactly 5 hex colors.
- The art_style must be specific enough that different artists could reproduce it consistently.
- Include 4-6 visual_consistency_rules that ensure a stranger could tell all stickers belong to the same pack.
- Do NOT include any explanation outside the JSON block.
"""
        return prompt

    @staticmethod
    def build_text_sticker_prompt(
        style_guide: Dict[str, Any],
        theme_content: Dict[str, Any],
        count: int,
    ) -> str:
        """Build prompt for text-only sticker ideas (Claude call 1).

        Uses sticker_phrases, slang_and_memes, and trending topic names
        as seed material, then asks Claude to creatively remix them.
        """
        guide_block = PromptBuilder._format_style_guide_block(style_guide)

        phrases = [p.get("text", "") for p in theme_content.get("sticker_phrases", [])]
        slang = [s.get("text", "") for s in theme_content.get("slang_and_memes", [])]
        topic_names = [t.get("name", "") for t in theme_content.get("trending_topics", [])]
        keywords = theme_content.get("keywords", [])

        prompt = f"""{guide_block}

You are a creative sticker text designer for a global English-speaking audience.

Theme: {theme_content.get('theme_english', '')}

=== SEED MATERIAL (use as inspiration, NOT verbatim copy) ===
Sticker phrases: {', '.join(phrases)}
Slang & memes: {', '.join(slang)}
Trending topic names: {', '.join(topic_names)}
Keywords: {', '.join(keywords[:10])}
=== END SEED MATERIAL ===

Design exactly {count} TEXT-ONLY stickers in a **bold die-cut typography style**.

=== MANDATORY VISUAL STYLE FOR TEXT STICKERS ===
Every text sticker MUST follow this specific aesthetic (inspired by popular marketplace stickers):

1. **Bold, chunky typography**: Extra-bold / black-weight sans-serif fonts. Text must look thick, solid, and highly readable even at small sizes.
2. **White outer contour**: Every sticker has a thick white border/outline around the entire design, giving it the classic die-cut sticker look.
3. **Icon-as-letter substitution**: Replace one letter or word with a recognizable symbol or tiny icon related to the topic. Examples:
   - The "O" in "LOVE" replaced by a heart ♥ or a brand logo shape
   - The "A" in a word replaced by a relevant icon
   - A heart symbol replacing the word "love"
4. **Minimal color palette**: Each sticker uses only 2-3 bold, saturated colors from the style guide palette. No complex gradients.
5. **Stacked / multi-line layout**: Text arranged in 2-3 lines with varying font sizes for visual hierarchy. The most important word is largest.
6. **No background scenery**: The design is ONLY text + symbol on a solid or simple color fill, with the white die-cut border. Clean negative space.
7. **High contrast**: Dark text on light, or vivid colored text with white outlines for maximum pop.
=== END VISUAL STYLE ===

Creative divergence techniques you MUST use (mix these across your {count} stickers):
- Emoji / symbol injection: "I ♥ ChatGPT", "AI ⚡ Power"
- Sentence patterns: "I + verb/symbol + topic" (e.g. "Ask Claude", "Trust the Algorithm")
- Humor / sarcasm / contrast: "ChatGPT > Google", "404: Brain not found"
- Internet slang mashups: "No cap, AI slaps", "It's giving... artificial intelligence"
- Topic name + action: "Ctrl+C from ChatGPT", "Siri who?"
- Literary / witty remixes: "To ChatGPT or not to ChatGPT", "Elementary, dear AI"
- Cross-reference slang with topic names for fresh combos

Return a JSON array:

```json
[
  {{
    "index": 1,
    "type": "text",
    "title": "Short English title (max 5 words)",
    "sticker_text": "The actual text that appears on the sticker (2-8 words). Use ♥ ⚡ ★ symbols where appropriate.",
    "concept": "One-sentence design concept in English — mention which icon/symbol replaces which letter and the overall layout",
    "image_prompt": "English image-generation prompt (50-90 words). Describe: (1) the exact text and its layout (stacked lines, which word is biggest), (2) font style (extra-bold, rounded, blocky sans-serif), (3) which letter/word is replaced by what icon/symbol, (4) 2-3 specific hex colors from the palette used for text fill, (5) thick white outer contour/border, (6) solid color or transparent background. Do NOT describe characters, scenes, or objects beyond the icon substitution."
  }}
]
```

Rules:
- ALL output in English.
- sticker_text must be short, punchy, culturally relevant, and NOT a verbatim copy of seed phrases.
- image_prompt must specify the EXACT text content that appears, the font weight (extra-bold/black), white die-cut border, and 2-3 palette colors.
- Do NOT use words: kawaii, cute sticker, cartoon character in image_prompt.
- Ensure variety across the {count} stickers — mix emotions (funny, motivational, sarcastic, wholesome) AND layouts (single-line, stacked 2-line, stacked 3-line).
"""
        return prompt

    @staticmethod
    def build_element_sticker_prompt(
        style_guide: Dict[str, Any],
        theme_content: Dict[str, Any],
        count: int,
    ) -> str:
        """Build prompt for element-based sticker ideas (Claude call 2).

        Uses trending_topics as the primary source for visual subjects.
        """
        guide_block = PromptBuilder._format_style_guide_block(style_guide)

        topics_str = ""
        for t in theme_content.get("trending_topics", []):
            topics_str += f"  - {t.get('name', '')} ({t.get('category', '')}) : {t.get('description', '')}\n"

        prompt = f"""{guide_block}

You are an icon and mascot designer creating element stickers for a global English-speaking audience.

Theme: {theme_content.get('theme_english', '')}

=== TRENDING TOPICS (design visual representations of these) ===
{topics_str if topics_str else '  (none)'}
=== END TRENDING TOPICS ===

Design exactly {count} ELEMENT stickers. Each sticker is a standalone visual icon, mascot, symbol, or object -- NO text or only a tiny label/logo.

Return a JSON array:

```json
[
  {{
    "index": 1,
    "type": "element",
    "title": "Short English title (max 5 words)",
    "concept": "One-sentence design concept in English — which trending topic it represents and how",
    "image_prompt": "English image-generation prompt (40-80 words) describing the visual subject: what it depicts (character, object, icon, mascot), pose/angle, expression, details. MUST follow the style guide art_style, line_style, and use colors from the palette. Do NOT describe any text. Do NOT use words: sticker, kawaii, cute sticker."
  }}
]
```

Rules:
- ALL output in English.
- Each sticker should visually represent or be inspired by a trending topic.
- Spread across different topics — don't repeat the same subject.
- image_prompt must strictly follow the Pack Style Guide (art_style, palette, line_style, consistency rules).
- Ensure variety: mix objects, characters/mascots, abstract icons, and symbolic representations.
"""
        return prompt

    @staticmethod
    def build_combined_sticker_prompt(
        style_guide: Dict[str, Any],
        theme_content: Dict[str, Any],
        count: int,
    ) -> str:
        """Build prompt for combined (text + element) sticker ideas (Claude call 3).

        Pairs sticker_phrases with trending_topics for text+graphic compositions.
        """
        guide_block = PromptBuilder._format_style_guide_block(style_guide)

        phrases = [p.get("text", "") for p in theme_content.get("sticker_phrases", [])]
        slang = [s.get("text", "") for s in theme_content.get("slang_and_memes", [])]
        topic_names = [t.get("name", "") for t in theme_content.get("trending_topics", [])]

        prompt = f"""{guide_block}

You are a sticker designer creating combined text-and-graphic stickers for a global English-speaking audience.

Theme: {theme_content.get('theme_english', '')}

=== MATERIAL TO COMBINE ===
Text candidates (remix freely): {', '.join(phrases)}
Slang & memes: {', '.join(slang)}
Visual subject candidates: {', '.join(topic_names)}
=== END MATERIAL ===

Design exactly {count} COMBINED stickers. Each sticker has BOTH a visual graphic element AND prominent text, working together as one composition.

Return a JSON array:

```json
[
  {{
    "index": 1,
    "type": "combined",
    "title": "Short English title (max 5 words)",
    "sticker_text": "The text that appears on the sticker (2-6 words, creatively remixed)",
    "concept": "One-sentence design concept in English — how text and graphic complement each other",
    "image_prompt": "English image-generation prompt (40-80 words) describing BOTH the visual element (character/object/icon) AND the text treatment (position, font style, effects). Text and graphic must be described as one unified composition. MUST use style guide palette and art_style. Do NOT use words: sticker, kawaii, cute sticker."
  }}
]
```

Rules:
- ALL output in English.
- Each sticker pairs a creatively remixed phrase with a visual element inspired by trending topics.
- Text and graphic must complement each other (e.g. a robot mascot holding a speech bubble with "Ask AI").
- image_prompt must strictly follow the Pack Style Guide.
- Mix pairing strategies: text-as-speech-bubble, text-as-banner, text-integrated-into-scene, text-as-label.
- Ensure variety in both text content and visual subjects.
"""
        return prompt

    # ------------------------------------------------------------------
    # Preview image prompt generation
    # ------------------------------------------------------------------

    @staticmethod
    def build_preview_prompt_via_claude(
        pack_name: str,
        sticker_ideas: List[Dict[str, Any]],
        style_guide: Dict[str, Any],
    ) -> str:
        """Build a prompt that asks Claude to generate a Gemini-ready
        preview image prompt for the entire sticker collection sheet.

        Args:
            pack_name: Name of the sticker pack.
            sticker_ideas: List of sticker idea dicts (must have title, concept, type).
            style_guide: Pack Style Guide dict.

        Returns:
            str: Prompt for Claude to produce a Gemini image prompt.
        """
        guide_block = PromptBuilder._format_style_guide_block(style_guide)

        ideas_block = ""
        for idea in sticker_ideas:
            idx = idea.get("index", "?")
            title = idea.get("title", "Untitled")
            concept = idea.get("concept", "")
            stype = idea.get("type", "element")
            sticker_text = idea.get("sticker_text", "")
            text_info = f'  Text: "{sticker_text}"' if sticker_text else ""
            ideas_block += f"  {idx}. [{stype}] {title} — {concept}{text_info}\n"

        prompt = f"""{guide_block}

You are an expert prompt engineer specializing in AI image generation (Midjourney, DALL-E 3, Gemini Imagen).

Your task is to write a **single, high-quality image generation prompt** that produces a **sticker collection sheet** — one image showing ALL stickers from this pack arranged together.

=== PACK DETAILS ===
Pack name: "{pack_name}"
Total stickers: {len(sticker_ideas)}

Sticker list:
{ideas_block}
=== END PACK DETAILS ===

Write the image generation prompt following these rules:

1. **Format**: Start with "A sticker pack collection sheet titled \\"{pack_name}\\"."
2. **Layout**: Specify the stickers are arranged in an organized grid layout on a white background.
3. **Content**: Briefly describe each sticker's visual appearance (derive from the title and concept). Keep each sticker description to 10-20 words.
4. **Style**: Incorporate the art_style, color palette, and mood from the Style Guide above.
5. **Physical attributes**: Every sticker must have "thick white die-cut border".
6. **Background**: "Isolated on clean white background".
7. **Quality tags**: End with "Professional graphic design, high resolution, flat 2D sticker art."
8. **Length**: The entire prompt should be 150-300 words — detailed enough for quality, concise enough for token limits.

Return ONLY the prompt text. No JSON, no markdown fences, no explanation — just the raw prompt string.
"""
        return prompt

    @staticmethod
    def build_preview_prompt_direct(
        pack_name: str,
        sticker_ideas: List[Dict[str, Any]],
        style_guide: Dict[str, Any],
    ) -> str:
        """Build a Gemini-ready preview prompt directly via template,
        without an extra Claude call. Faster but less creative.

        Args:
            pack_name: Name of the sticker pack.
            sticker_ideas: List of sticker idea dicts.
            style_guide: Pack Style Guide dict.

        Returns:
            str: Image generation prompt for Gemini.
        """
        art_style = style_guide.get("art_style", "minimalist vector illustration")
        palette = style_guide.get("color_palette", {})
        mood = style_guide.get("mood", "modern and creative")

        colors = ", ".join(
            f"{k} {v}" for k, v in palette.items()
            if k != "background" and v
        )

        descriptions = []
        for idea in sticker_ideas:
            title = idea.get("title", "")
            concept = idea.get("concept", "")
            sticker_text = idea.get("sticker_text", "")
            if sticker_text:
                descriptions.append(f'a sticker saying "{sticker_text}" ({title})')
            elif concept:
                short = concept[:80]
                descriptions.append(f"{title}: {short}")
            else:
                descriptions.append(title)

        items_str = ", ".join(descriptions)

        prompt = (
            f'A sticker pack collection sheet titled "{pack_name}". '
            f"{len(sticker_ideas)} individual die-cut stickers arranged in an organized grid layout. "
            f"The stickers include: {items_str}. "
            f"Style: {art_style}, {mood} aesthetic"
        )

        if colors:
            prompt += f" with {colors} color accents"

        prompt += (
            ". Each sticker has a thick white die-cut border. "
            "Isolated on a clean white background. "
            "Professional graphic design, high resolution, flat 2D sticker art."
        )

        return prompt

    # ------------------------------------------------------------------
    # Interactive session → image prompt conversion
    # ------------------------------------------------------------------

    @staticmethod
    def build_style_guide_from_config_prompt(
        theme: str,
        directions: List[str],
        visual_style: str,
        color_mood: str,
    ) -> str:
        """Build a prompt to generate a Pack Style Guide from interactive
        session config fields (visual_style, color_mood, etc.).

        Returns:
            str: Prompt for Claude to produce a style guide JSON.
        """
        directions_str = ", ".join(directions) if directions else theme

        return f"""You are an expert visual-identity designer for sticker packs.

Theme: {theme}
Sub-directions: {directions_str}
Requested visual style: {visual_style}
Requested color mood: {color_mood}

Create a **Pack Style Guide** — a cohesive visual specification for every sticker in this pack.

Return a JSON object with exactly this structure:

```json
{{
  "art_style": "Describe the art style in one sentence based on the requested visual style",
  "color_palette": {{
    "primary": "#hex",
    "secondary": "#hex",
    "accent": "#hex",
    "background": "#hex",
    "text_color": "#hex"
  }},
  "line_style": "Describe stroke/outline treatment",
  "mood": "3-5 adjectives describing the overall vibe",
  "typography_style": "Describe font/text treatment for stickers with text overlay",
  "visual_consistency_rules": [
    "Rule 1", "Rule 2", "Rule 3", "Rule 4"
  ]
}}
```

Requirements:
- The art_style MUST reflect the user's requested visual style ("{visual_style}").
- The palette MUST reflect the requested color mood ("{color_mood}").
- Include 4-6 visual_consistency_rules for a unified pack look.
- Do NOT include any explanation outside the JSON block.
"""

    @staticmethod
    def build_concepts_to_image_prompts(
        style_guide: Dict[str, Any],
        theme: str,
        concepts: List[Dict[str, Any]],
    ) -> str:
        """Build a prompt that converts a list of StickerConcepts (Chinese
        descriptions + optional text_overlay) into English image-generation
        prompts, one per sticker.

        Args:
            style_guide: Pack Style Guide dict.
            theme: Pack theme.
            concepts: List of dicts with keys: index, description, text_overlay.

        Returns:
            str: Prompt for Claude to produce a JSON array of image_prompts.
        """
        guide_block = PromptBuilder._format_style_guide_block(style_guide)

        concepts_block = ""
        for c in concepts:
            idx = c.get("index", "?")
            desc = c.get("description", "")
            text = c.get("text_overlay", "")
            text_info = f'  Text overlay: "{text}"' if text else "  (no text overlay)"
            concepts_block += f"  {idx}. {desc}\n{text_info}\n"

        return f"""{guide_block}

You are an expert prompt engineer for AI image generation (Gemini Imagen, DALL-E 3, Midjourney).

Theme: {theme}

=== STICKER CONCEPTS (user-provided, in Chinese) ===
{concepts_block}
=== END CONCEPTS ===

For EACH concept above, write a detailed **English** image-generation prompt (50-90 words).

Each prompt must:
1. Translate and expand the Chinese description into a vivid English scene description.
2. Specify the character/subject, pose, expression, action, and key visual details.
3. If the concept has a text overlay, describe the EXACT text and its visual treatment \
   (font style, position, effects — e.g. bold text banner at bottom, speech bubble, etc.).
4. Follow the Pack Style Guide above (art_style, palette colors by hex code, line_style).
5. End with: "thick white die-cut sticker border, isolated on white background."
6. Do NOT use the words: kawaii, cute sticker, sticker sheet.

Return a JSON array (no markdown fences, no explanation):

[{{"index": 1, "title": "Short English title (max 5 words)", "image_prompt": "..."}}, ...]
"""

    # ------------------------------------------------------------------
    # Legacy prompt (kept for backward compatibility)
    # ------------------------------------------------------------------

    @staticmethod
    def build_sticker_pack_prompt(
        theme: str,
        text_count: int,
        element_count: int,
        hybrid_count: int
    ) -> str:
        """构建贴纸包生成 Prompt

        Args:
            theme: 主题
            text_count: 纯文本贴纸数量
            element_count: 元素贴纸数量
            hybrid_count: 组合贴纸数量

        Returns:
            str: Prompt
        """
        total = text_count + element_count + hybrid_count

        prompt = f"""你是专业的贴纸设计师。请为主题"{theme}"设计 {total} 张贴纸。

贴纸类型分配：
1. 纯文本贴纸（Text-only）：{text_count} 张
   - 只包含文字，无图形元素
   - 文字简短有力（2-6个字）
   - 适合表达情绪、状态、口号

2. 元素贴纸（Element-based）：{element_count} 张
   - 主要是图形/图标，可含少量文字
   - 视觉元素突出
   - 代表主题相关的物品、符号、角色

3. 组合贴纸（Hybrid）：{hybrid_count} 张
   - 文字与图形结合
   - 相互补充，形成完整表达

输出要求（严格按以下 JSON 格式）：
```json
[
  {{
    "index": 1,
    "type": "text|element|hybrid",
    "title": "贴纸名称（中文，5字以内）",
    "concept": "设计概念（中文，一句话）",
    "image_prompt": "英文图像生成提示词，详细描述视觉元素、风格、色调"
  }},
  ...
]
```

image_prompt 编写规则：
- 必须用英文
- 每张图只描述一个独立的主体
- 主体必须完整、可独立存在
- 描述主体形象（角色/物品/场景）
- 描述风格（realistic/3D render/digital painting/watercolor/flat illustration等）
- 描述色调（pastel/vibrant/monochrome/warm tones/cool tones等）
- 不要出现 sticker、kawaii、cute sticker 等词汇
- 每条 40-80 词，具体生动

请确保：
- 前 {text_count} 张为 type="text"
- 接下来 {element_count} 张为 type="element"
- 最后 {hybrid_count} 张为 type="hybrid"
- 所有贴纸围绕"{theme}"主题
- 风格统一，但内容多样化
"""
        return prompt

    @staticmethod
    def build_style_analysis_prompt(image_description: str = "") -> str:
        """构建风格分析 Prompt

        Args:
            image_description: 图片描述（可选）

        Returns:
            str: Prompt
        """
        prompt = """你是专业的视觉设计分析师。请分析这张贴纸的风格特征。

请从以下 7 个维度进行分析：

1. **视觉风格**（Visual Style）
   - 艺术风格类型（如：扁平插画、手绘水彩、3D渲染、像素风等）
   - 整体视觉印象

2. **色彩方案**（Color Palette）
   - 主色调
   - 辅助色
   - 色彩饱和度和明度
   - 色彩搭配特点

3. **构图布局**（Composition）
   - 主体位置和大小
   - 留白处理
   - 视觉平衡

4. **文字排版**（Typography）
   - 字体风格（如有）
   - 文字位置和大小
   - 文字与图形的关系

5. **情绪氛围**（Mood & Emotion）
   - 传达的情绪
   - 整体氛围感受

6. **细节程度**（Detail Level）
   - 细节丰富度
   - 线条粗细
   - 纹理质感

7. **目标受众**（Target Audience）
   - 适合的年龄群体
   - 使用场景

输出要求（严格按以下 JSON 格式）：
```json
{{
  "visual_style": {{
    "value": "具体风格描述",
    "confidence": 0.9
  }},
  "color_palette": {{
    "value": "色彩方案描述",
    "confidence": 0.9
  }},
  "composition": {{
    "value": "构图描述",
    "confidence": 0.9
  }},
  "typography": {{
    "value": "排版描述",
    "confidence": 0.9
  }},
  "mood_emotion": {{
    "value": "情绪描述",
    "confidence": 0.9
  }},
  "detail_level": {{
    "value": "细节描述",
    "confidence": 0.9
  }},
  "target_audience": {{
    "value": "受众描述",
    "confidence": 0.9
  }},
  "overall_description": "整体风格的综合描述（2-3句话）",
  "key_features": ["特征1", "特征2", "特征3"]
}}
```
"""
        if image_description:
            prompt += f"\n\n图片描述：\n{image_description}"

        return prompt

    @staticmethod
    def build_variant_generation_prompt(
        style_analysis: Dict[str, Any],
        variation_degree: VariationDegree,
        variant_index: int,
        total_variants: int,
        additional_instructions: Optional[str] = None
    ) -> str:
        """构建变种生成 Prompt

        Args:
            style_analysis: 风格分析结果
            variation_degree: 变化程度
            variant_index: 变种索引
            total_variants: 总变种数
            additional_instructions: 额外指令

        Returns:
            str: Prompt
        """
        # 变化程度说明
        variation_instructions = {
            VariationDegree.LOW: "保持90%的风格一致性，仅做微小变化（如角度、表情、颜色微调）",
            VariationDegree.MEDIUM: "保持70%的风格一致性，适度创新（如姿势变化、元素替换）",
            VariationDegree.HIGH: "保持50%的风格一致性，大胆创新（如场景变化、风格融合）"
        }

        prompt = f"""基于以下风格特征，生成第 {variant_index}/{total_variants} 个变种贴纸。

原始风格特征：
- 视觉风格: {style_analysis.get('visual_style', {}).get('value', 'N/A')}
- 色彩方案: {style_analysis.get('color_palette', {}).get('value', 'N/A')}
- 构图布局: {style_analysis.get('composition', {}).get('value', 'N/A')}
- 文字排版: {style_analysis.get('typography', {}).get('value', 'N/A')}
- 情绪氛围: {style_analysis.get('mood_emotion', {}).get('value', 'N/A')}
- 细节程度: {style_analysis.get('detail_level', {}).get('value', 'N/A')}
- 目标受众: {style_analysis.get('target_audience', {}).get('value', 'N/A')}

整体描述: {style_analysis.get('overall_description', 'N/A')}

变化要求: {variation_instructions[variation_degree]}
"""

        if additional_instructions:
            prompt += f"\n额外要求: {additional_instructions}"

        prompt += """

请生成一个英文图像生成提示词（image_prompt），要求：
- 体现上述风格特征
- 根据变化程度进行适当创新
- 40-80 词，具体生动
- 不要出现 sticker、kawaii 等词汇
"""

        return prompt

    @staticmethod
    def build_chat_analysis_prompt(
        user_description: str,
        count: int,
        has_reference: bool = False
    ) -> str:
        """构建对话式分析 Prompt

        Args:
            user_description: 用户描述
            count: 生成数量
            has_reference: 是否有参考图

        Returns:
            str: Prompt
        """
        ref_section = """
参考图说明：
用户提供了一张参考图。请仔细分析其：
- 主体角色/元素是什么
- 整体艺术风格（例：扁平卡通、手绘水彩、像素风）
- 配色方案（主色、辅色、高光色）
- 线条特征（粗黑描边、细线、无描边）
- 表情/动作的表现方式

在生成 image_prompt 时，应体现参考图的风格特征。
""" if has_reference else "（用户未提供参考图）"

        style_hint = "\n请确保 image_prompt 中体现参考图的风格特征。" if has_reference else ""

        prompt = f"""你是专业的素材图片设计助手。用户给出了一段描述，请帮用户生成 {count} 套素材图片设计方案。

用户描述：
{user_description}

{ref_section}

输出要求（严格按以下 JSON 格式）：
```json
[
  {{
    "index": 1,
    "title": "方案名称（中文，5字以内）",
    "concept": "设计概念说明（中文，一句话）",
    "image_prompt": "英文图像生成提示词，详细描述视觉元素、风格、色调"
  }},
  ...
]
```

image_prompt 编写规则：
- 必须用英文
- 每张图只描述一个独立的主体
- 主体必须完整、可独立存在
- 描述主体形象、风格、色调
- 不要出现 sticker、kawaii 等词汇
- 每条 40-80 词{style_hint}
"""
        return prompt


# 便捷函数 — topic generation (v3)
def build_topic_generation_prompt(theme: str, max_topics: int = 6) -> str:
    """Build topic generation prompt (convenience function)."""
    return PromptBuilder.build_topic_generation_prompt(theme, max_topics)


# 便捷函数 — v2 pipeline
def build_pack_style_guide_prompt(theme_content: Dict[str, Any]) -> str:
    """Build Pack Style Guide prompt (convenience function)."""
    return PromptBuilder.build_pack_style_guide_prompt(theme_content)


def build_text_sticker_prompt(
    style_guide: Dict[str, Any], theme_content: Dict[str, Any], count: int
) -> str:
    """Build text-only sticker prompt (convenience function)."""
    return PromptBuilder.build_text_sticker_prompt(style_guide, theme_content, count)


def build_element_sticker_prompt(
    style_guide: Dict[str, Any], theme_content: Dict[str, Any], count: int
) -> str:
    """Build element sticker prompt (convenience function)."""
    return PromptBuilder.build_element_sticker_prompt(style_guide, theme_content, count)


def build_combined_sticker_prompt(
    style_guide: Dict[str, Any], theme_content: Dict[str, Any], count: int
) -> str:
    """Build combined sticker prompt (convenience function)."""
    return PromptBuilder.build_combined_sticker_prompt(style_guide, theme_content, count)


# 便捷函数 — theme content
def build_theme_content_prompt(
    theme: str,
    max_topics: int = 12,
    max_keywords: int = 20,
    max_phrases: int = 15,
) -> str:
    """Build theme content expansion prompt (convenience function)."""
    return PromptBuilder.build_theme_content_prompt(theme, max_topics, max_keywords, max_phrases)


def build_sticker_pack_prompt(theme: str, text_count: int, element_count: int, hybrid_count: int) -> str:
    """构建贴纸包生成 Prompt（便捷函数）"""
    return PromptBuilder.build_sticker_pack_prompt(theme, text_count, element_count, hybrid_count)


def build_style_analysis_prompt(image_description: str = "") -> str:
    """构建风格分析 Prompt（便捷函数）"""
    return PromptBuilder.build_style_analysis_prompt(image_description)


def build_variant_generation_prompt(
    style_analysis: Dict[str, Any],
    variation_degree: VariationDegree,
    variant_index: int,
    total_variants: int,
    additional_instructions: Optional[str] = None
) -> str:
    """构建变种生成 Prompt（便捷函数）"""
    return PromptBuilder.build_variant_generation_prompt(
        style_analysis,
        variation_degree,
        variant_index,
        total_variants,
        additional_instructions
    )


# 便捷函数 — preview prompt
def build_preview_prompt_via_claude(
    pack_name: str,
    sticker_ideas: List[Dict[str, Any]],
    style_guide: Dict[str, Any],
) -> str:
    """Build Claude prompt for generating a preview image prompt (convenience)."""
    return PromptBuilder.build_preview_prompt_via_claude(pack_name, sticker_ideas, style_guide)


def build_preview_prompt_direct(
    pack_name: str,
    sticker_ideas: List[Dict[str, Any]],
    style_guide: Dict[str, Any],
) -> str:
    """Build Gemini-ready preview prompt directly (convenience)."""
    return PromptBuilder.build_preview_prompt_direct(pack_name, sticker_ideas, style_guide)


# 便捷函数 — interactive session config → prompts
def build_style_guide_from_config_prompt(
    theme: str,
    directions: List[str],
    visual_style: str,
    color_mood: str,
) -> str:
    """Build style guide prompt from interactive config (convenience)."""
    return PromptBuilder.build_style_guide_from_config_prompt(
        theme, directions, visual_style, color_mood
    )


def build_concepts_to_image_prompts(
    style_guide: Dict[str, Any],
    theme: str,
    concepts: List[Dict[str, Any]],
) -> str:
    """Build concept→image_prompt conversion prompt (convenience)."""
    return PromptBuilder.build_concepts_to_image_prompts(style_guide, theme, concepts)
