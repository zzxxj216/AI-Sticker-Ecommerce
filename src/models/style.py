"""风格分析数据模型"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict

from src.core.constants import VariationDegree


class StyleDimension(BaseModel):
    """风格维度"""

    name: str = Field(..., description="维度名称")
    value: str = Field(..., description="维度值")
    confidence: Optional[float] = Field(None, ge=0, le=1, description="置信度")
    description: Optional[str] = Field(None, description="描述")


class StyleAnalysis(BaseModel):
    """风格分析结果"""

    id: str = Field(..., description="分析唯一标识")
    source_image: str = Field(..., description="源图片路径")

    # 7个核心维度
    visual_style: StyleDimension = Field(..., description="视觉风格")
    color_palette: StyleDimension = Field(..., description="色彩方案")
    composition: StyleDimension = Field(..., description="构图布局")
    typography: StyleDimension = Field(..., description="文字排版")
    mood_emotion: StyleDimension = Field(..., description="情绪氛围")
    detail_level: StyleDimension = Field(..., description="细节程度")
    target_audience: StyleDimension = Field(..., description="目标受众")

    # 综合描述
    overall_description: str = Field(..., description="整体风格描述")
    key_features: List[str] = Field(default_factory=list, description="关键特征")

    # 元数据
    analyzed_at: datetime = Field(default_factory=datetime.now)
    analyzer_version: str = Field(default="2.0.0")

    def get_all_dimensions(self) -> List[StyleDimension]:
        """获取所有维度"""
        return [
            self.visual_style,
            self.color_palette,
            self.composition,
            self.typography,
            self.mood_emotion,
            self.detail_level,
            self.target_audience,
        ]

    def to_prompt(self, variation_degree: VariationDegree = VariationDegree.MEDIUM) -> str:
        """转换为生成提示词

        Args:
            variation_degree: 变化程度

        Returns:
            str: 提示词
        """
        # 根据变化程度调整提示词
        variation_instructions = {
            VariationDegree.LOW: "保持90%的风格一致性，仅做微小变化",
            VariationDegree.MEDIUM: "保持70%的风格一致性，适度创新",
            VariationDegree.HIGH: "保持50%的风格一致性，大胆创新",
        }

        prompt_parts = [
            f"视觉风格: {self.visual_style.value}",
            f"色彩方案: {self.color_palette.value}",
            f"构图布局: {self.composition.value}",
            f"文字排版: {self.typography.value}",
            f"情绪氛围: {self.mood_emotion.value}",
            f"细节程度: {self.detail_level.value}",
            f"目标受众: {self.target_audience.value}",
            f"\n整体描述: {self.overall_description}",
            f"\n变化要求: {variation_instructions[variation_degree]}",
        ]

        return "\n".join(prompt_parts)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()


class VariantConfig(BaseModel):
    """变种生成配置"""

    model_config = ConfigDict(use_enum_values=True)

    style_analysis: StyleAnalysis = Field(..., description="风格分析结果")
    variation_degree: VariationDegree = Field(
        default=VariationDegree.MEDIUM,
        description="变化程度"
    )
    variant_count: int = Field(default=5, ge=1, le=20, description="变种数量")

    # 可选的额外指令
    additional_instructions: Optional[str] = Field(None, description="额外指令")
    preserve_elements: List[str] = Field(default_factory=list, description="保留元素")
    avoid_elements: List[str] = Field(default_factory=list, description="避免元素")

    @field_validator('variation_degree', mode='before')
    @classmethod
    def validate_variation_degree(cls, v):
        """验证变化程度"""
        if isinstance(v, str):
            return VariationDegree(v)
        return v

    def get_generation_prompt(self, variant_index: int) -> str:
        """获取生成提示词

        Args:
            variant_index: 变种索引

        Returns:
            str: 提示词
        """
        base_prompt = self.style_analysis.to_prompt(self.variation_degree)

        # 添加变种特定指令
        variant_instructions = [
            f"\n变种编号: {variant_index + 1}/{self.variant_count}",
        ]

        if self.preserve_elements:
            variant_instructions.append(f"必须保留: {', '.join(self.preserve_elements)}")

        if self.avoid_elements:
            variant_instructions.append(f"必须避免: {', '.join(self.avoid_elements)}")

        if self.additional_instructions:
            variant_instructions.append(f"额外要求: {self.additional_instructions}")

        return base_prompt + "\n".join(variant_instructions)
