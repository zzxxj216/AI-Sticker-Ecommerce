"""Video Prompt Builder — constructs prompts for two-stage script generation.

Stage 1 (Plan): decides type ordering, intent, shot allocation.
Stage 2 (Script): produces the full storyboard from a plan.
"""

from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

PLAN_SYSTEM_PROMPT = """You are a senior TikTok US-market short video strategist.

Your job is to create a script PLAN (not the final script) for a TikTok sticker product video.

You are given:
1. A video type combo — which video types to use and their goals
2. Context about the sticker pack — trend brief, sticker descriptions, brand constraints
3. Constraints — duration range, shot count range, hard rules

Your output must be a single valid JSON object:
{
  "video_intent": "one sentence describing what this video wants to achieve",
  "type_order": ["type_id_1", "type_id_2", ...],
  "shot_plan": [
    {
      "shot_index": 1,
      "type_id": "resonance",
      "purpose": "relatable hook to grab attention",
      "duration_sec": 2
    }
  ],
  "cta_direction": "engagement | conversion | mixed",
  "hook_idea": "the core hook concept in one sentence",
  "total_duration_sec": 10,
  "notes": "any strategic notes about this plan"
}

Rules:
- total_duration_sec must be within the given duration range.
- Number of shots must be within the given shot count range.
- type_order must use ONLY the types in the combo's selected_types.
- The primary_type should get the most prominent position.
- If combo contains soft_sell or comment_driver, the last shot MUST be assigned to it.
- cta_direction should match the support types: soft_sell → conversion, comment_driver → engagement.
- Think about TikTok US audience: Gen Z / millennial, short attention spans, native casual tone.
- Output ONLY the JSON object, no markdown fences, no extra text.""".strip()


SCRIPT_SYSTEM_PROMPT = """You are a senior TikTok US-market short video scriptwriter.

Your job is to produce a production-ready storyboard script based on a given script plan.

You are given:
1. The approved script plan (type ordering, shot allocation, intent)
2. Sticker pack context (trend, descriptions, brand constraints)
3. Video type rules (what each type should output)

Your output must be a single valid JSON object:
{
  "title": "short working title",
  "hook_text": "the attention-grabbing opening text/line",
  "cta_text": "the final call-to-action",
  "caption_text": "TikTok post caption (with hashtags)",
  "title_options": ["option 1", "option 2", "option 3"],
  "shots": [
    {
      "shot_index": 1,
      "type_id": "resonance",
      "time_range": "0-2s",
      "duration_sec": 2,
      "visual_description": "specific description of what appears on screen",
      "on_screen_text": "exact text overlay (max 8 words)",
      "voiceover": "spoken words or empty string",
      "transition": "cut | zoom | swipe | fade",
      "sticker_action": "what the sticker is doing in this shot"
    }
  ],
  "hashtags": ["#tag1", "#tag2"],
  "music_mood": "short description of ideal background music",
  "sticker_hero": "which sticker design is the hero",
  "production_notes": "extra notes for filming/editing",
  "total_duration_sec": 10
}

Rules:
- All text content must be in natural American English — casual, TikTok-native.
- on_screen_text must be 8 words or fewer per shot.
- voiceover should be optional and very short; sticker TikToks work better with text + music.
- Each shot must specify what the sticker is doing (sticker_action).
- The hero sticker must appear at least once prominently.
- If the plan includes collection_flex, at least one shot must show the collection sheet / full pack spread.
- If the plan includes commerce_scene, at least one shot must show a real use-case (laptop, bottle, etc.).
- If the last shot is comment_driver, it must end with a question.
- If the last shot is soft_sell, it must have a soft purchase CTA.
- Hashtags should mix trend-specific + sticker/product tags (6-10 total).
- Tone: playful, internet-native, lightly sarcastic, never cringe or corporate.
- Output ONLY the JSON object, no markdown fences, no extra text.""".strip()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class VideoPromptBuilder:
    """Assembles plan and script prompts from structured inputs."""

    @staticmethod
    def build_plan_prompt(
        *,
        script_input: dict[str, Any],
        combo: dict[str, Any],
        type_configs: list[dict[str, Any]],
        brand_profile: dict[str, Any] | None = None,
    ) -> str:
        lines: list[str] = []

        # --- Combo info ---
        lines.append("[VIDEO TYPE COMBO]")
        lines.append(f"Combo: {combo.get('name', combo.get('combo_id', ''))}")
        lines.append(f"Selected Types: {', '.join(combo.get('selected_types', []))}")
        lines.append(f"Primary Type: {combo.get('primary_type', '')}")
        secondary = combo.get("secondary_types", [])
        if secondary:
            lines.append(f"Secondary Types: {', '.join(secondary)}")
        support = combo.get("support_types", [])
        if support:
            lines.append(f"Support Types: {', '.join(support)}")
        dr = combo.get("duration_range", {})
        lines.append(f"Duration Range: {dr.get('min', 7)}-{dr.get('max', 12)} seconds")
        sr = combo.get("shot_count_range", {})
        lines.append(f"Shot Count Range: {sr.get('min', 3)}-{sr.get('max', 5)} shots")
        constraints = combo.get("constraints", [])
        if constraints:
            lines.append(f"Hard Constraints: {', '.join(constraints)}")
        lines.append("")

        # --- Type definitions ---
        lines.append("[VIDEO TYPE DEFINITIONS]")
        for tc in type_configs:
            lines.append(f"  type_id: {tc['type_id']}")
            lines.append(f"    name: {tc['name']}")
            lines.append(f"    goal: {tc.get('goal', '')}")
            lines.append(f"    allowed_positions: {', '.join(tc.get('allowed_positions', ['any']))}")
            styles = tc.get("text_style_rules", [])
            if styles:
                lines.append(f"    text_style: {'; '.join(styles)}")
            elements = tc.get("output_elements", [])
            if elements:
                lines.append(f"    output_elements: {', '.join(elements)}")
            lines.append("")

        _append_sticker_context(lines, script_input)
        _append_brand_section(lines, brand_profile)

        lines.append("[TASK]")
        lines.append("Create a script PLAN for this video. Decide type ordering, shot allocation, and intent.")
        lines.append("The plan MUST reference specific sticker designs and product details from the context above.")
        lines.append("Output ONLY a valid JSON object following the schema described in your instructions.")

        return "\n".join(lines)

    @staticmethod
    def build_script_prompt(
        *,
        script_input: dict[str, Any],
        combo: dict[str, Any],
        type_configs: list[dict[str, Any]],
        plan: dict[str, Any],
        brand_profile: dict[str, Any] | None = None,
    ) -> str:
        lines: list[str] = []

        # --- Approved plan ---
        lines.append("[APPROVED SCRIPT PLAN]")
        lines.append(f"Video Intent: {plan.get('video_intent', '')}")
        lines.append(f"Type Order: {', '.join(plan.get('type_order', []))}")
        lines.append(f"CTA Direction: {plan.get('cta_direction', 'engagement')}")
        lines.append(f"Hook Idea: {plan.get('hook_idea', '')}")
        lines.append(f"Total Duration: {plan.get('total_duration_sec', 10)}s")
        shot_plan = plan.get("shot_plan", [])
        if shot_plan:
            lines.append("Shot Plan:")
            for sp in shot_plan:
                lines.append(f"  Shot {sp.get('shot_index', '?')}: "
                             f"type={sp.get('type_id', '')} | "
                             f"purpose={sp.get('purpose', '')} | "
                             f"duration={sp.get('duration_sec', 0)}s")
        if plan.get("notes"):
            lines.append(f"Strategic Notes: {plan['notes']}")
        lines.append("")

        # --- Combo constraints ---
        lines.append("[COMBO CONSTRAINTS]")
        lines.append(f"Combo: {combo.get('name', '')}")
        dr = combo.get("duration_range", {})
        lines.append(f"Duration Range: {dr.get('min', 7)}-{dr.get('max', 12)}s")
        sr = combo.get("shot_count_range", {})
        lines.append(f"Shot Count Range: {sr.get('min', 3)}-{sr.get('max', 5)}")
        constraints = combo.get("constraints", [])
        if constraints:
            lines.append(f"Hard Constraints: {', '.join(constraints)}")
        lines.append("")

        # --- Type definitions (for style reference) ---
        lines.append("[VIDEO TYPE STYLE REFERENCE]")
        for tc in type_configs:
            lines.append(f"  {tc['type_id']}: {tc.get('goal', '')}")
            styles = tc.get("text_style_rules", [])
            if styles:
                lines.append(f"    style: {'; '.join(styles)}")
            elements = tc.get("output_elements", [])
            if elements:
                lines.append(f"    outputs: {', '.join(elements)}")
        lines.append("")

        _append_sticker_context(lines, script_input)
        _append_brand_section(lines, brand_profile)

        lines.append("[TASK]")
        lines.append("Based on the approved plan above, generate the full production-ready storyboard script.")
        lines.append("Follow the shot plan exactly (same number of shots, same type assignments).")
        lines.append("IMPORTANT: Every shot must reference specific sticker designs, slogans, or visual elements from the context above.")
        lines.append("The stickers ARE the product — show them being used, admired, applied, and collected.")
        lines.append("Output ONLY a valid JSON object following the schema described in your instructions.")

        return "\n".join(lines)


def _append_sticker_context(lines: list[str], si: dict[str, Any]) -> None:
    """Render all sticker pack context sections into the prompt."""

    lines.append("[STICKER PACK CONTEXT]")
    if si.get("trend_topic"):
        lines.append(f"Trend Topic: {si['trend_topic']}")
    if si.get("one_line_explanation"):
        lines.append(f"Explanation: {si['one_line_explanation']}")
    if si.get("emotional_hooks"):
        lines.append(f"Emotional Hooks: {', '.join(si['emotional_hooks'])}")
    if si.get("audience_persona"):
        lines.append(f"Audience Persona: {si['audience_persona']}")
    if si.get("visual_symbols"):
        lines.append(f"Visual Symbols: {', '.join(si['visual_symbols'])}")
    if si.get("use_cases"):
        lines.append(f"Use Cases: {', '.join(si['use_cases'])}")
    if si.get("materials"):
        lines.append(f"Materials: {', '.join(si['materials'])}")
    if si.get("hero_sticker"):
        lines.append(f"Hero Sticker: {si['hero_sticker']}")
    if si.get("collection_sheet"):
        lines.append("Collection Sheet: available")
    if si.get("sticker_descriptions"):
        lines.append("Sticker Designs:")
        for i, desc in enumerate(si["sticker_descriptions"][:12], 1):
            lines.append(f"  {i}. {desc}")
    if si.get("one_line_product_angle"):
        lines.append(f"Product Angle: {si['one_line_product_angle']}")
    lines.append("")

    ec = si.get("event_context")
    if ec:
        lines.append("[EVENT CONTEXT]")
        lines.append(f"Event: {ec.get('title', '')}")
        if ec.get("category"):
            lines.append(f"Category: {ec['category']}")
        if ec.get("date"):
            date_str = ec["date"]
            if ec.get("end_date") and ec["end_date"] != ec["date"]:
                date_str += f" — {ec['end_date']}"
            lines.append(f"Date: {date_str}")
        if ec.get("description"):
            lines.append(f"Description: {ec['description']}")
        lines.append("")

    dd = si.get("design_direction")
    if dd:
        lines.append("[DESIGN DIRECTION]")
        lines.append(f"Direction Name: {dd.get('name', '')}")
        if dd.get("name_zh"):
            lines.append(f"Direction (Chinese): {dd['name_zh']}")
        if dd.get("keywords"):
            lines.append(f"Keywords: {dd['keywords']}")
        if dd.get("design_elements"):
            lines.append(f"Visual Elements: {dd['design_elements']}")
        if dd.get("text_slogans"):
            lines.append(f"Text Slogans: {dd['text_slogans']}")
        if dd.get("decorative_elements"):
            lines.append(f"Decorative Elements: {dd['decorative_elements']}")
        lines.append("")

    ps = si.get("pack_style")
    if ps:
        lines.append("[VISUAL STYLE]")
        if ps.get("art_style"):
            lines.append(f"Art Style: {ps['art_style']}")
        cp = ps.get("color_palette")
        if cp and isinstance(cp, dict):
            colors = ", ".join(f"{k}: {v}" for k, v in list(cp.items())[:5])
            lines.append(f"Color Palette: {colors}")
        if ps.get("mood"):
            lines.append(f"Mood: {ps['mood']}")
        if ps.get("line_style"):
            lines.append(f"Line Style: {ps['line_style']}")
        if ps.get("typography_style"):
            lines.append(f"Typography: {ps['typography_style']}")
        lines.append("")

    td = si.get("theme_details")
    if td:
        lines.append("[THEME DETAILS]")
        if td.get("theme_english"):
            lines.append(f"Theme: {td['theme_english']}")
        if td.get("visual_keywords"):
            lines.append(f"Visual Keywords: {', '.join(td['visual_keywords'][:10])}")
        if td.get("cultural_context"):
            lines.append(f"Cultural Context: {td['cultural_context'][:200]}")
        lines.append("")

    sp = si.get("product_selling_points")
    if sp:
        lines.append("[PRODUCT SELLING POINTS]")
        for point in sp:
            lines.append(f"- {point}")
        lines.append("")


def _append_brand_section(lines: list[str], brand_profile: dict[str, Any] | None) -> None:
    lines.append("[BRAND CONSTRAINTS]")
    if not brand_profile:
        lines.append("  Voice: playful, internet-native, lightly sarcastic, not cringe")
        lines.append("")
        return
    brand = brand_profile.get("brand", {})
    if brand.get("voice"):
        lines.append(f"  Voice: {brand['voice'].strip()}")
    tone_kw = brand.get("tone_keywords", [])
    if tone_kw:
        lines.append(f"  Tone Keywords: {', '.join(tone_kw)}")
    avoid = brand.get("avoid_tone", [])
    if avoid:
        lines.append(f"  Avoid Tone: {', '.join(avoid)}")
    materials = brand_profile.get("materials", {})
    safe_claims = materials.get("safe_claims", [])
    if safe_claims:
        lines.append(f"  Material Claims: {', '.join(safe_claims)}")
    avoid_claims = materials.get("avoid_claims", [])
    if avoid_claims:
        lines.append(f"  Avoid Claims: {', '.join(avoid_claims)}")
    lines.append("")
