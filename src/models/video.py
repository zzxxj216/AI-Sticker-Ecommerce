"""Video Script Generation — Pydantic Models

Defines the data models for the configurable video type + combo driven
TikTok script generation system (V1).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

_CN_TZ = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(_CN_TZ)


VideoPosition = Literal["opening", "middle", "closing", "any"]


class VideoTypeConfig(BaseModel):
    """Single video type definition — what it does, what it needs, where it fits."""

    type_id: str
    name: str
    goal: str = ""
    description: str = ""
    required_inputs: list[str] = Field(default_factory=list)
    allowed_positions: list[VideoPosition] = Field(default_factory=lambda: ["any"])
    text_style_rules: list[str] = Field(default_factory=list)
    can_pair_with: list[str] = Field(default_factory=list)
    output_elements: list[str] = Field(default_factory=list)
    is_active: bool = True


class VideoComboConfig(BaseModel):
    """A curated combination of video types with duration / shot constraints."""

    combo_id: str
    name: str
    selected_types: list[str] = Field(default_factory=list)
    primary_type: str = ""
    secondary_types: list[str] = Field(default_factory=list)
    support_types: list[str] = Field(default_factory=list)
    duration_range: dict[str, int] = Field(default_factory=lambda: {"min": 7, "max": 12})
    shot_count_range: dict[str, int] = Field(default_factory=lambda: {"min": 3, "max": 5})
    constraints: list[str] = Field(default_factory=list)
    is_active: bool = True


class VideoScriptInput(BaseModel):
    """Unified input assembled from existing pipeline assets."""

    pack_id: str = ""
    job_id: str = ""
    design_id: str = ""
    trend_topic: str = ""
    one_line_explanation: str = ""
    emotional_hooks: list[str] = Field(default_factory=list)
    audience_persona: str = ""
    visual_symbols: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    brand_tone: str = ""
    sticker_descriptions: list[str] = Field(default_factory=list)
    hero_sticker: str = ""
    collection_sheet: str = ""
    one_line_product_angle: str = ""
    platform: str = "tiktok"


class ShotPlan(BaseModel):
    """A single shot in the script plan."""

    shot_index: int = 0
    type_id: str = ""
    purpose: str = ""
    duration_sec: float = 0


class VideoScriptPlan(BaseModel):
    """First-stage output: what this video wants to express and how types are arranged."""

    plan_id: str = ""
    video_intent: str = ""
    type_order: list[str] = Field(default_factory=list)
    shot_plan: list[ShotPlan] = Field(default_factory=list)
    cta_direction: Literal["engagement", "conversion", "mixed"] = "engagement"
    hook_idea: str = ""
    total_duration_sec: int = 10
    notes: str = ""


class ShotScript(BaseModel):
    """A single shot in the final script."""

    shot_index: int = 0
    type_id: str = ""
    time_range: str = ""
    duration_sec: float = 0
    visual_description: str = ""
    on_screen_text: str = ""
    voiceover: str = ""
    transition: str = "cut"
    sticker_action: str = ""


class VideoScriptOutput(BaseModel):
    """Second-stage output: the full production-ready storyboard script."""

    script_id: str = ""
    plan_id: str = ""
    combo_id: str = ""
    title: str = ""
    hook_text: str = ""
    cta_text: str = ""
    caption_text: str = ""
    title_options: list[str] = Field(default_factory=list)
    shots: list[ShotScript] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    music_mood: str = ""
    sticker_hero: str = ""
    production_notes: str = ""
    total_duration_sec: int = 0
