"""生成配置数据模型

交互式会话系统的核心数据结构，供 InteractiveSession 和 PackGenerator 共同使用。
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# 会话阶段常量
# ------------------------------------------------------------------

PHASE_COLLECTING = "collecting"
PHASE_REVIEWING_CONCEPTS = "reviewing_concepts"
PHASE_PREVIEW = "preview"  # 合并话题预览：先出预览图，用户确认后再出单张
PHASE_EDITING = "editing"  # 单张生成后，用户可通过对话修改某张的 prompt 并重新生成


# ------------------------------------------------------------------
# 数据模型
# ------------------------------------------------------------------

class Slot(BaseModel):
    """待收集的单项信息槽位"""

    name: str = Field(..., description="槽位标识符")
    label: str = Field(..., description="中文显示标签")
    description: str = Field(..., description="槽位用途说明（英文，供 AI 理解）")
    required: bool = Field(default=True, description="是否必填")
    value: Any = Field(default=None, description="用户提供的值")
    default: Any = Field(default=None, description="默认值（仅可选槽位）")

    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_filled(self) -> bool:
        return self.value is not None

    @property
    def effective_value(self) -> Any:
        return self.value if self.value is not None else self.default


class StickerConcept(BaseModel):
    """单张贴纸的内容概念"""

    index: int = Field(..., description="序号（1-based）")
    description: str = Field(..., description="贴纸画面描述")
    text_overlay: str = Field(default="", description="贴纸上的文字")
    sticker_type: str = Field(
        default="combined",
        description="贴纸视觉类型: text_only, element, combined, pattern, emoji_style",
    )

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class GenerationConfig(BaseModel):
    """最终生成配置，由 InteractiveSession 产出，供 PackGenerator / 预览管线消费"""

    theme: str = Field(default="", description="贴纸包主题")
    directions: List[str] = Field(default_factory=list, description="细分方向列表")
    visual_style: str = Field(default="", description="视觉风格")
    color_mood: str = Field(default="", description="色彩情绪")
    sticker_count: int = Field(default=10, description="每套贴纸数量")
    pack_name: str = Field(default="", description="贴纸包名称")
    sticker_concepts: List[StickerConcept] = Field(
        default_factory=list, description="贴纸概念列表",
    )

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class SessionResponse(BaseModel):
    """InteractiveSession.process_message() 的返回值"""

    message: str = Field(..., description="AI 回复文本")
    phase: str = Field(default=PHASE_COLLECTING, description="当前会话阶段")
    updated_slots: Dict[str, Any] = Field(
        default_factory=dict, description="本轮更新的槽位",
    )
    missing_slots: List[str] = Field(
        default_factory=list, description="尚缺的必填槽位名",
    )
    ready_for_generation: bool = Field(
        default=False, description="是否已可开始生成",
    )
    configs: List[GenerationConfig] = Field(
        default_factory=list, description="确认后的生成配置列表",
    )
