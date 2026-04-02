"""Sticker Pack Generation - Data Models

Multi-agent architecture data structures:
- PackPlan: Planner Agent output (6-section text planning)
- StickerDesign: Designer Agent output (single sticker design)
- StickerPrompt: Prompter Agent output (single image generation prompt)
- CategoryPreview: Category preview result
- StickerPackConfig / StickerPackResult: Full pack config and result
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# Planner Agent output
# ------------------------------------------------------------------

class PackPlan(BaseModel):
    """Planner Agent structured output

    Parsed from AgentResponse.structured_data,
    all 6 sections are free-text strings, LLM organizes content based on theme.
    """

    theme_name: str = Field(default="", description="Short product theme name")
    theme_direction: str = Field(
        default="", description="Theme direction: emotional core, main atmosphere, why suitable for sticker pack"
    )
    pack_structure: str = Field(
        default="", description="Pack structure: category modules, quantity allocation, usage types"
    )
    design_style: str = Field(
        default="", description="Design style: illustration style, texture, color mood, consistency"
    )
    sticker_format: str = Field(
        default="", description="Sticker format: shape, size combinations, content and format matching"
    )
    hit_details: str = Field(
        default="", description="Hit-selling details: product appeal drivers, common pitfalls"
    )
    bundle_example: str = Field(
        default="", description="Bundle example: 1 complete pack composition"
    )


# ------------------------------------------------------------------
# Designer Agent output
# ------------------------------------------------------------------

STICKER_CATEGORIES = ("scene", "vehicle", "landmark", "icon", "text")


class StickerDesign(BaseModel):
    """Designer Agent output: single sticker design"""

    index: int = Field(..., description="Index (1-based)")
    main_element: str = Field(..., description="Main element")
    decorative_elements: List[str] = Field(
        default_factory=list, description="Decorative elements (2-3)"
    )
    text_slogan: str = Field(default="", description="Text/slogan (optional)")
    category: str = Field(default="scene", description="Category: scene/vehicle/landmark/icon/text")


# ------------------------------------------------------------------
# Prompter Agent output
# ------------------------------------------------------------------

class StickerPrompt(BaseModel):
    """Prompter Agent output: single image generation prompt"""

    index: int = Field(..., description="Index (1-based)")
    title: str = Field(default="", description="Sticker title (3-6 words)")
    image_prompt: str = Field(..., description="Image generation prompt (50-90 words)")
    category: str = Field(default="scene", description="Category")


# ------------------------------------------------------------------
# Category preview result
# ------------------------------------------------------------------

class CategoryPreview(BaseModel):
    """Single category preview generation result"""

    category: str = Field(..., description="Category name")
    sticker_count: int = Field(default=0, description="Sticker count in this category")
    preview_prompt: str = Field(default="", description="Preview synthesis prompt")
    preview_image_path: Optional[str] = Field(None, description="Preview image path")
    success: bool = Field(default=False)
    error: Optional[str] = Field(None)


# ------------------------------------------------------------------
# Full pack config and result
# ------------------------------------------------------------------

class StickerPackConfig(BaseModel):
    """Complete sticker pack configuration"""

    pack_id: str = Field(default="", description="Pack ID")
    pack_index: int = Field(default=1, description="Pack index (1-based)")
    pack_name: str = Field(default="", description="Pack name")

    plan: Optional[PackPlan] = Field(None, description="Planner output")
    designs: List[StickerDesign] = Field(default_factory=list, description="Designer output")
    prompts: List[StickerPrompt] = Field(default_factory=list, description="Prompter output")
    category_previews: List[CategoryPreview] = Field(
        default_factory=list, description="Category previews"
    )

    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def sticker_count(self) -> int:
        return len(self.designs)

    def designs_by_category(self) -> Dict[str, List[StickerDesign]]:
        groups: Dict[str, List[StickerDesign]] = {}
        for d in self.designs:
            groups.setdefault(d.category, []).append(d)
        return groups

    def prompts_by_category(self) -> Dict[str, List[StickerPrompt]]:
        groups: Dict[str, List[StickerPrompt]] = {}
        for p in self.prompts:
            groups.setdefault(p.category, []).append(p)
        return groups


class StickerPackResult(BaseModel):
    """Sticker pack generation result"""

    pack_id: str = Field(default="")
    pack_index: int = Field(default=1)
    pack_name: str = Field(default="")
    status: str = Field(default="pending")
    sticker_count: int = Field(default=0)
    preview_count: int = Field(default=0)
    error: Optional[str] = Field(None)

    started_at: Optional[datetime] = Field(None)
    completed_at: Optional[datetime] = Field(None)
    duration_seconds: Optional[float] = Field(None)

    def mark_started(self):
        self.started_at = datetime.now()
        self.status = "running"

    def mark_completed(self):
        self.completed_at = datetime.now()
        self.status = "completed"
        if self.started_at:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()

    def mark_failed(self, error: str):
        self.completed_at = datetime.now()
        self.status = "failed"
        self.error = error
        if self.started_at:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()


# ------------------------------------------------------------------
# Pipeline overall result
# ------------------------------------------------------------------

class PipelineResult(BaseModel):
    """Full multi-agent Pipeline run result"""

    theme: str = Field(default="")
    planner_output: str = Field(default="", description="Planner full output")
    planner_part2: str = Field(
        default="",
        description="Plan excerpt for Designer/Prompter (4-part: PART2-4; legacy: PART2)",
    )
    designer_output: str = Field(default="", description="Designer full output")
    prompter_output: str = Field(default="", description="Prompter full output")
    prompts_grouped: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict,
        description="Grouped prompts: {category: [{index, prompt}, ...]}",
    )
    prompts_flat: List[Dict[str, Any]] = Field(
        default_factory=list, description="Flattened per-sticker prompts"
    )
    preview_paths: Dict[str, str] = Field(
        default_factory=dict, description="Preview image paths by category"
    )
    image_paths: List[str] = Field(
        default_factory=list, description="Per-sticker generated image paths"
    )
    status: str = Field(default="pending")
    error: Optional[str] = Field(None)

    started_at: Optional[datetime] = Field(None)
    completed_at: Optional[datetime] = Field(None)
    duration_seconds: Optional[float] = Field(None)

    def mark_started(self):
        self.started_at = datetime.now()
        self.status = "running"

    def mark_completed(self):
        self.completed_at = datetime.now()
        self.status = "completed"
        if self.started_at:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()

    def mark_failed(self, error: str):
        self.completed_at = datetime.now()
        self.status = "failed"
        self.error = error
        if self.started_at:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()
