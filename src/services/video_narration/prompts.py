"""Prompt for Gemini video analysis -> English voiceover + scene-anchored segments."""

from __future__ import annotations

from typing import Any, Optional


def _meta_block(meta: Optional[dict]) -> str:
    if not meta:
        return ""
    lines = []
    if meta.get("display_name"):
        lines.append(f"- Pack name: {meta['display_name']}")
    if meta.get("archetype"):
        lines.append(f"- Archetype: {meta['archetype']}")
    if meta.get("style_anchor"):
        lines.append(f"- Visual style: {str(meta['style_anchor'])[:300]}")
    if meta.get("palette"):
        lines.append(f"- Palette: {meta['palette']}")
    if meta.get("themes"):
        lines.append(f"- Sticker themes: {', '.join(str(t) for t in meta['themes'][:10])}")
    if meta.get("hot_topic"):
        lines.append(f"- Trend/hook angle: {meta['hot_topic']}")
    if meta.get("emotion"):
        lines.append(f"- Core emotion/meme: {meta['emotion']}")
    if not lines:
        return ""
    return (
        "\n\n### Pack context (use this — it is ground truth; do NOT contradict it)\n"
        + "\n".join(lines)
    )


def build_analysis_prompt(duration: float, *, meta: Optional[dict] = None) -> str:
    word_budget = max(8, int(duration * 2.0))
    return f"""You are a short-form video scriptwriter for an overseas (US-market) sticker e-commerce brand.

You are given a SILENT promotional video that is {duration:.1f} seconds long. Watch it, then produce a JSON object that:
  1. Analyzes what the video shows (product, scenes, any on-screen text).
  2. Writes an English (en-US) VOICEOVER to be read aloud over it — natural, energetic TikTok-creator tone, NOT robotic. Hook in the first ~2 seconds, highlight the sticker pack's appeal/benefits, end with a soft call-to-action.
  3. Breaks the voiceover into timed segments. CRITICAL: each segment's ``start_s`` MUST be the on-screen moment that line refers to — anchor every line to the scene/shot it describes (hook over the opening shot; product/benefit lines over the matching shots; CTA over the final / logo shot). Lines will be placed in the audio at these exact start times, so wrong timestamps = audio talking about the wrong shot.
  4. Size each segment so its text is comfortably speakable within its span at a natural pace (~2.2 words/second); a segment that needs more time should claim a later start only if its scene is actually later. Keep it CONCISE — about {word_budget} words total; it is better to slightly UNDER-fill than to overrun.{_meta_block(meta)}

Return ONLY this JSON (no prose, no code fences):
{{
  "product_name": "string",
  "summary": "1-2 sentence description of what the video shows",
  "scenes": [
    {{"start_s": 0.0, "end_s": 0.0, "visual": "what is on screen", "on_screen_text": "readable overlay text, else empty"}}
  ],
  "narration_segments": [
    {{"start_s": 0.0, "end_s": 0.0, "text": "spoken voiceover line, anchored to the scene at start_s"}}
  ],
  "hook": "the opening voiceover line",
  "cta": "the closing call-to-action line"
}}"""
