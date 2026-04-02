"""Minimal Planner chain: structured trend_brief → planning body (Markdown)."""

from typing import Any, Optional

from src.services.batch.sticker_prompts import (
    PLANNER_SYSTEM,
    build_planner_prompt,
)


def plan_sticker_pack(
    trend_brief: dict[str, Any],
    *,
    openai_service: Any,
    user_style: Optional[str] = None,
    user_color_mood: Optional[str] = None,
    user_extra: str = "",
    temperature: float = 0.7,
) -> dict[str, Any]:
    """Call Planner (PLANNER_SYSTEM) with a structured trend brief.

    Args:
        trend_brief: Project-shaped dict (see trend_brief_schema).
        openai_service: Object with ``generate(prompt=, system=, temperature=) -> {"text", "usage", ...}``,
            typically ``OpenAIService``.

    Returns:
        ``{"text": <full plan>, "usage": ...}``, same shape as ``OpenAIService.generate``.
    """
    user_prompt = build_planner_prompt(
        trend_brief=trend_brief,
        user_style=user_style,
        user_color_mood=user_color_mood,
        user_extra=user_extra,
    )
    return openai_service.generate(
        prompt=user_prompt,
        system=PLANNER_SYSTEM,
        temperature=temperature,
    )
