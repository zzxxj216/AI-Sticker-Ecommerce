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

def _append_family_context(lines: list[str], si: dict[str, Any]) -> None:
    """Inject family-pack or sibling context when available."""
    if si.get("is_family_pack"):
        lines.append("[FAMILY PACK CONTEXT]")
        lines.append(f"This is a FAMILY PACK containing {si.get('family_pack_count', 0)} sub-packs under one theme.")
        lines.append("The video should showcase the entire collection as a cohesive product family.")
        subthemes = si.get("family_subthemes", [])
        if subthemes:
            lines.append("Sub-themes in this family:")
            for i, st in enumerate(subthemes, 1):
                name = st.get("subtheme_name", "")
                stype = st.get("subtheme_type", "")
                direction = st.get("one_line_direction", "")
                summary = st.get("brief_summary", "")
                entry = f"  {i}. {name}"
                if stype:
                    entry += f" ({stype})"
                if direction:
                    entry += f" — {direction}"
                if summary:
                    entry += f" | {summary}"
                lines.append(entry)
        lines.append("Strategy: Highlight the variety within the family while maintaining a unified theme.")
        lines.append("Show how different sub-packs complement each other.")
        lines.append("")
    elif si.get("sibling_context"):
        lines.append("[SIBLING PACK CONTEXT]")
        lines.append(si["sibling_context"])
        lines.append("Consider referencing the series in the script to encourage collecting the full set.")
        lines.append("")


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

        # --- Sticker pack context ---
        lines.append("[STICKER PACK CONTEXT]")
        si = script_input
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
            lines.append(f"Collection Sheet: available")
        if si.get("sticker_descriptions"):
            lines.append("Sticker Designs:")
            for i, desc in enumerate(si["sticker_descriptions"][:12], 1):
                lines.append(f"  {i}. {desc}")
        if si.get("one_line_product_angle"):
            lines.append(f"Product Angle: {si['one_line_product_angle']}")
        lines.append("")

        # --- Family / sibling context ---
        _append_family_context(lines, si)

        # --- Brand constraints ---
        _append_brand_section(lines, brand_profile)

        lines.append("[TASK]")
        if si.get("is_family_pack"):
            lines.append("Create a script PLAN for a video showcasing this sticker pack FAMILY as one cohesive product.")
            lines.append("The video should highlight the variety of sub-themes while emphasizing they belong together.")
        else:
            lines.append("Create a script PLAN for this video. Decide type ordering, shot allocation, and intent.")
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

        # --- Sticker pack context ---
        si = script_input
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
            lines.append(f"Collection Sheet: available")
        if si.get("sticker_descriptions"):
            lines.append("Sticker Designs:")
            for i, desc in enumerate(si["sticker_descriptions"][:12], 1):
                lines.append(f"  {i}. {desc}")
        if si.get("one_line_product_angle"):
            lines.append(f"Product Angle: {si['one_line_product_angle']}")
        lines.append("")

        # --- Family / sibling context ---
        _append_family_context(lines, si)

        _append_brand_section(lines, brand_profile)

        lines.append("[TASK]")
        lines.append("Based on the approved plan above, generate the full production-ready storyboard script.")
        lines.append("Follow the shot plan exactly (same number of shots, same type assignments).")
        if si.get("is_family_pack"):
            lines.append("This is a FAMILY PACK video — make sure to showcase stickers from multiple sub-themes.")
        lines.append("Output ONLY a valid JSON object following the schema described in your instructions.")

        return "\n".join(lines)


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
