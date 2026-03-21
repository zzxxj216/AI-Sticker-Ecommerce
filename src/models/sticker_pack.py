"""贴纸包生成 — 新版数据模型

多 Agent 架构数据结构：
- PackPlan: Planner Agent 输出（6 板块纯文本规划）
- StickerDesign: Designer Agent 输出（单张贴纸设计）
- StickerPrompt: Prompter Agent 输出（单张图片生成 prompt）
- CategoryPreview: 分类预览图结果
- StickerPackConfig / StickerPackResult: 整包配置与结果
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# Planner Agent 输出
# ------------------------------------------------------------------

class PackPlan(BaseModel):
    """Planner Agent 的结构化输出

    从 AgentResponse.structured_data 解析而来，
    6 个板块均为自由文本字符串，LLM 根据主题自行组织内容。
    """

    theme_name: str = Field(default="", description="简短的产品化主题名称")
    theme_direction: str = Field(default="", description="主题方向：情绪核心、主打氛围、为什么适合做贴纸包")
    pack_structure: str = Field(default="", description="贴纸结构：分类模块、数量分配、各类用途")
    design_style: str = Field(default="", description="设计风格：画风、质感、配色感觉、一致性")
    sticker_format: str = Field(default="", description="贴纸形式：形状、尺寸搭配、内容与版型匹配")
    hit_details: str = Field(default="", description="爆款细节：商品感驱动因素、常见坑")
    bundle_example: str = Field(default="", description="组合示例：1 套完整卡包搭配方案")


# ------------------------------------------------------------------
# Designer Agent 输出
# ------------------------------------------------------------------

STICKER_CATEGORIES = ("scene", "vehicle", "landmark", "icon", "text")


class StickerDesign(BaseModel):
    """Designer Agent 的输出：单张贴纸设计"""

    index: int = Field(..., description="序号 (1-based)")
    main_element: str = Field(..., description="主元素")
    decorative_elements: List[str] = Field(default_factory=list, description="装饰元素（2-3 个）")
    text_slogan: str = Field(default="", description="文案/标语（可选）")
    category: str = Field(default="scene", description="分类: scene/vehicle/landmark/icon/text")


# ------------------------------------------------------------------
# Prompter Agent 输出
# ------------------------------------------------------------------

class StickerPrompt(BaseModel):
    """Prompter Agent 的输出：单张图片生成 prompt"""

    index: int = Field(..., description="序号 (1-based)")
    title: str = Field(default="", description="贴纸标题（3-6 词）")
    image_prompt: str = Field(..., description="图片生成 prompt（50-90 词）")
    category: str = Field(default="scene", description="分类")


# ------------------------------------------------------------------
# 分类预览图结果
# ------------------------------------------------------------------

class CategoryPreview(BaseModel):
    """单个分类的预览图生成结果"""

    category: str = Field(..., description="分类名")
    sticker_count: int = Field(default=0, description="该分类贴纸数")
    preview_prompt: str = Field(default="", description="合成预览的 prompt")
    preview_image_path: Optional[str] = Field(None, description="预览图路径")
    success: bool = Field(default=False)
    error: Optional[str] = Field(None)


# ------------------------------------------------------------------
# 整包配置与结果
# ------------------------------------------------------------------

class StickerPackConfig(BaseModel):
    """一套贴纸包的完整配置"""

    pack_id: str = Field(default="", description="包 ID")
    pack_index: int = Field(default=1, description="包序号 (1-based)")
    pack_name: str = Field(default="", description="包名称")

    plan: Optional[PackPlan] = Field(None, description="Planner 输出")
    designs: List[StickerDesign] = Field(default_factory=list, description="Designer 输出")
    prompts: List[StickerPrompt] = Field(default_factory=list, description="Prompter 输出")
    category_previews: List[CategoryPreview] = Field(default_factory=list, description="分类预览图")

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
    """一套贴纸包的生成结果"""

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
# Pipeline 整体结果
# ------------------------------------------------------------------

class PipelineResult(BaseModel):
    """三 Agent Pipeline 的完整运行结果"""

    theme: str = Field(default="")
    planner_output: str = Field(default="", description="Planner 完整输出")
    planner_part2: str = Field(default="", description="Planner PART 2 摘取")
    designer_output: str = Field(default="", description="Designer 完整输出")
    prompter_output: str = Field(default="", description="Prompter 完整输出")
    prompts_grouped: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict, description="按主题分组的 prompt: {category: [{index, prompt}, ...]}"
    )
    prompts_flat: List[Dict[str, Any]] = Field(default_factory=list, description="展平后的逐张 prompt")
    preview_paths: Dict[str, str] = Field(default_factory=dict, description="按主题分类的预览图路径")
    image_paths: List[str] = Field(default_factory=list, description="逐张生图路径")
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
