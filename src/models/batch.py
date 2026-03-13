"""批量生成管线数据模型

6 卡包并行生成 + 话题预览 + 按话题对话修改 的核心数据结构。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.models.generation import StickerConcept


# ------------------------------------------------------------------
# 话题级分组
# ------------------------------------------------------------------

class TopicGroup(BaseModel):
    """一个话题下的贴纸概念分组（每话题 6-8 张）"""

    topic_id: str = Field(..., description="话题唯一标识 (e.g. 'topic_01')")
    topic_name: str = Field(..., description="话题名称 (英文)")
    description: str = Field(default="", description="话题描述")
    target_count: int = Field(default=8, description="该话题目标张数 (6-8)")

    concepts: List[StickerConcept] = Field(
        default_factory=list, description="该话题下的贴纸概念",
    )
    ideas: List[Dict[str, Any]] = Field(
        default_factory=list, description="带 image_prompt 的贴纸 idea 列表",
    )

    preview_prompt: Optional[str] = Field(None, description="该话题的预览合并 prompt")
    preview_image_path: Optional[str] = Field(None, description="该话题总图路径")
    preview_success: Optional[bool] = Field(None, description="预览图是否生成成功")


# ------------------------------------------------------------------
# 单包配置
# ------------------------------------------------------------------

class BatchPackConfig(BaseModel):
    """6 卡包中单个卡包的配置"""

    pack_index: int = Field(..., description="卡包序号 (1-6)")
    pack_name: str = Field(default="", description="卡包名称")
    theme: str = Field(..., description="总主题")
    direction: str = Field(default="", description="该包的具体方向")
    visual_style: str = Field(default="", description="视觉风格")
    color_mood: str = Field(default="", description="色彩情绪")

    style_guide: Optional[Dict[str, Any]] = Field(None, description="包级 style guide")
    topics: List[TopicGroup] = Field(default_factory=list, description="话题列表")
    target_count: int = Field(default=55, description="目标贴纸总数 (50-60)")

    @property
    def total_concept_count(self) -> int:
        return sum(len(t.concepts) for t in self.topics)

    @property
    def total_idea_count(self) -> int:
        return sum(len(t.ideas) for t in self.topics)


# ------------------------------------------------------------------
# 批次总配置
# ------------------------------------------------------------------

class BatchConfig(BaseModel):
    """6 卡包批次的总配置"""

    batch_id: str = Field(default="", description="批次唯一 ID")
    user_theme: str = Field(..., description="用户输入的总主题")
    user_style: Optional[str] = Field(None, description="用户指定的风格（若有）")
    user_color_mood: Optional[str] = Field(None, description="用户指定的色彩情绪（若有）")
    user_extra: str = Field(default="", description="用户额外说明")
    pack_count: int = Field(default=6, description="卡包数量")
    target_per_pack: int = Field(default=55, description="每包目标张数")
    stickers_per_topic: int = Field(default=8, description="每话题张数 (6-8)")

    pack_configs: List[BatchPackConfig] = Field(
        default_factory=list, description="各卡包配置",
    )

    created_at: datetime = Field(default_factory=datetime.now)


# ------------------------------------------------------------------
# 话题预览结果
# ------------------------------------------------------------------

class TopicPreviewResult(BaseModel):
    """单个话题的预览图生成结果"""

    pack_index: int = Field(..., description="所属卡包序号")
    topic_id: str = Field(..., description="话题 ID")
    topic_name: str = Field(..., description="话题名称")
    preview_prompt: str = Field(default="", description="预览 prompt")
    preview_image_path: Optional[str] = Field(None, description="预览图路径")
    success: bool = Field(default=False)
    error: Optional[str] = Field(None)


# ------------------------------------------------------------------
# 单包结果
# ------------------------------------------------------------------

class BatchPackStatus:
    PLANNING = "planning"
    STYLE_GUIDE = "style_guide"
    GENERATING_CONTENT = "generating_content"
    GENERATING_PREVIEWS = "generating_previews"
    GENERATING_IMAGES = "generating_images"
    COMPLETED = "completed"
    FAILED = "failed"


class BatchPackResult(BaseModel):
    """单个卡包的完整生成结果"""

    pack_index: int = Field(..., description="卡包序号 (1-6)")
    pack_name: str = Field(default="")
    direction: str = Field(default="")
    visual_style: str = Field(default="")

    status: str = Field(default=BatchPackStatus.PLANNING)
    error: Optional[str] = Field(None)

    style_guide: Optional[Dict[str, Any]] = Field(None)
    topics: List[TopicGroup] = Field(default_factory=list)
    topic_previews: List[TopicPreviewResult] = Field(default_factory=list)

    sticker_count: int = Field(default=0, description="已生成贴纸数")
    sticker_success_count: int = Field(default=0)
    sticker_images_dir: Optional[str] = Field(None)

    started_at: Optional[datetime] = Field(None)
    completed_at: Optional[datetime] = Field(None)
    duration_seconds: Optional[float] = Field(None)

    def mark_started(self):
        self.started_at = datetime.now()

    def mark_completed(self):
        self.completed_at = datetime.now()
        self.status = BatchPackStatus.COMPLETED
        if self.started_at:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()

    def mark_failed(self, error: str):
        self.status = BatchPackStatus.FAILED
        self.error = error
        self.completed_at = datetime.now()
        if self.started_at:
            self.duration_seconds = (self.completed_at - self.started_at).total_seconds()


# ------------------------------------------------------------------
# 批次总结果
# ------------------------------------------------------------------

class BatchResult(BaseModel):
    """6 卡包批次的完整结果"""

    batch_id: str = Field(..., description="批次 ID")
    user_theme: str = Field(default="")
    pack_results: List[BatchPackResult] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = Field(None)
    duration_seconds: Optional[float] = Field(None)

    def mark_completed(self):
        self.completed_at = datetime.now()
        if self.created_at:
            self.duration_seconds = (self.completed_at - self.created_at).total_seconds()

    @property
    def completed_packs(self) -> int:
        return sum(1 for p in self.pack_results if p.status == BatchPackStatus.COMPLETED)

    @property
    def failed_packs(self) -> int:
        return sum(1 for p in self.pack_results if p.status == BatchPackStatus.FAILED)

    def to_summary(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "user_theme": self.user_theme,
            "total_packs": len(self.pack_results),
            "completed": self.completed_packs,
            "failed": self.failed_packs,
            "duration_seconds": self.duration_seconds,
            "packs": [
                {
                    "index": p.pack_index,
                    "name": p.pack_name,
                    "direction": p.direction,
                    "style": p.visual_style,
                    "status": p.status,
                    "topics": len(p.topics),
                    "stickers": p.sticker_count,
                    "previews": len(p.topic_previews),
                }
                for p in self.pack_results
            ],
        }


# ------------------------------------------------------------------
# 会话阶段常量（批量模式专用）
# ------------------------------------------------------------------

BATCH_PHASE_INPUT = "batch_input"
BATCH_PHASE_PLANNING = "batch_planning"
BATCH_PHASE_GENERATING = "batch_generating"
BATCH_PHASE_PREVIEW = "batch_preview"
BATCH_PHASE_EDITING = "batch_editing"
BATCH_PHASE_DONE = "batch_done"
