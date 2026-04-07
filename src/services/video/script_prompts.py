"""Video Script Agent — Prompt Templates

Defines the system prompt and user prompt builder for generating
TikTok video scripts based on sticker pack data and script templates.
"""

from __future__ import annotations

import json
from typing import Any


VIDEO_SCRIPT_SYSTEM = """You are a senior TikTok US-market short video script planner.

Your job is to create a detailed, production-ready video script for promoting sticker products on TikTok.

You are given:
1. A video template structure (segments with time ranges and purposes)
2. A sticker pack context (trend brief, sticker descriptions, style info)
3. Brand constraints (tone, material claims)

Your output must be a single valid JSON object with the following top-level keys:

{
  "title": "short working title for this video plan",
  "hook_line": "the opening line or text that grabs attention in the first 1-2 seconds",
  "segments": [
    {
      "time_range": "0-2s",
      "purpose": "hook_text",
      "visual_description": "what appears on screen — be specific about framing, motion, sticker placement",
      "text_overlay": "exact English text shown on screen, or empty string if none",
      "voiceover": "spoken words if any, or empty string",
      "transition": "cut / zoom / swipe / fade — how this segment ends"
    }
  ],
  "cta_text": "the final call-to-action text",
  "hashtags": ["#tag1", "#tag2", "..."],
  "music_mood": "short description of ideal background music vibe",
  "sticker_hero": "which sticker design should be the hero/focus of this video",
  "production_notes": "any extra notes for filming or editing"
}

Rules:
- All text content (hook_line, text_overlay, voiceover, cta_text, hashtags) must be in natural American English.
- The script must feel native to TikTok US — casual, direct, visually driven.
- Follow the template segment structure exactly (same number of segments, same time ranges).
- Be specific in visual descriptions — describe camera angles, sticker placement surfaces, motion.
- Keep voiceover optional and short; TikTok sticker content often works better with text overlays + music.
- Hashtags should mix trend-specific tags with sticker/product tags (6-10 total).
- Output ONLY the JSON object, no markdown fences, no extra text.
""".strip()


def build_video_script_prompt(
    *,
    template: dict[str, Any],
    job_info: dict[str, Any],
    trend_brief: dict[str, Any] | None,
    sticker_descriptions: list[str],
    brand_profile: dict[str, Any],
) -> str:
    """Build the user prompt for the Video Script Agent."""
    lines: list[str] = []

    # Template structure
    structure = template.get("structure", {})
    lines.append("[VIDEO TEMPLATE]")
    lines.append(f"Template: {template.get('name', '')}")
    lines.append(f"Test Goal: {structure.get('test_goal', '')}")
    lines.append(f"Total Duration: {structure.get('total_duration', '6-10s')}")
    lines.append(f"CTA Style: {structure.get('cta_style', '')}")
    lines.append(f"Best For: {structure.get('suitable_for', '')}")
    lines.append("")
    lines.append("Segments:")
    for seg in structure.get("segments", []):
        lines.append(f"  - {seg['time_range']} | {seg['purpose']} | {seg['guidance']}")
    lines.append("")

    # Sticker pack info
    lines.append("[STICKER PACK]")
    trend_name = job_info.get("trend_name", "")
    lines.append(f"Pack Name: {trend_name}")
    lines.append(f"Image Count: {job_info.get('image_count', 0)}")
    if sticker_descriptions:
        lines.append("Sticker Designs:")
        for i, desc in enumerate(sticker_descriptions[:12], 1):
            lines.append(f"  {i}. {desc}")
    lines.append("")

    # Trend brief context
    if trend_brief:
        lines.append("[TREND CONTEXT]")
        for key in ("trend_name", "one_line_explanation", "why_now", "lifecycle",
                     "emotional_core", "visual_symbols", "platform", "product_goal"):
            val = trend_brief.get(key)
            if val:
                display = ", ".join(val) if isinstance(val, list) else str(val)
                lines.append(f"  {key}: {display}")
        ta = trend_brief.get("target_audience")
        if isinstance(ta, dict):
            lines.append(f"  audience_profile: {ta.get('profile', '')}")
            us = ta.get("usage_scenarios")
            if us:
                lines.append(f"  usage_scenarios: {', '.join(us) if isinstance(us, list) else us}")
        lines.append("")

    # Brand profile
    lines.append("[BRAND CONSTRAINTS]")
    brand = brand_profile.get("brand", {})
    if brand.get("voice"):
        lines.append(f"  Voice: {brand['voice'].strip()}")
    materials = brand_profile.get("materials", {})
    safe_claims = materials.get("safe_claims", [])
    if safe_claims:
        lines.append(f"  Material Claims: {', '.join(safe_claims)}")
    avoid_claims = materials.get("avoid_claims", [])
    if avoid_claims:
        lines.append(f"  Avoid Claims: {', '.join(avoid_claims)}")
    lines.append("")

    lines.append("[TASK]")
    lines.append("Generate a complete video script following the template structure above.")
    lines.append("Output ONLY a valid JSON object.")

    return "\n".join(lines)
