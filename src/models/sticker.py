"""贴纸数据模型"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator

from src.core.constants import StickerType


class StickerMetadata(BaseModel):
    """贴纸元数据"""
    
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    tags: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    source: Optional[str] = None  # "pack_generator" or "style_analyzer"
    parent_id: Optional[str] = None  # 如果是变种，记录父贴纸 ID
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class Sticker(BaseModel):
    """贴纸数据模型"""
    
    id: str = Field(..., description="贴纸唯一标识")
    type: StickerType = Field(..., description="贴纸类型")
    prompt: str = Field(..., description="生成提示词")
    image_path: Optional[str] = Field(None, description="图片文件路径")
    image_url: Optional[str] = Field(None, description="图片 URL")
    theme: Optional[str] = Field(None, description="主题")
    description: Optional[str] = Field(None, description="描述")
    metadata: StickerMetadata = Field(default_factory=StickerMetadata)
    
    # 生成状态
    status: str = Field(default="pending", description="状态: pending, generating, success, failed")
    error_message: Optional[str] = Field(None, description="错误信息")
    retry_count: int = Field(default=0, description="重试次数")
    
    # 质量评分（可选）
    quality_score: Optional[float] = Field(None, ge=0, le=1, description="质量评分 0-1")
    
    @validator('type', pre=True)
    def validate_type(cls, v):
        """验证贴纸类型"""
        if isinstance(v, str):
            return StickerType(v)
        return v
    
    def mark_success(self, image_path: str):
        """标记为成功"""
        self.status = "success"
        self.image_path = image_path
        self.metadata.updated_at = datetime.now()
    
    def mark_failed(self, error: str):
        """标记为失败"""
        self.status = "failed"
        self.error_message = error
        self.retry_count += 1
        self.metadata.updated_at = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.dict()
    
    class Config:
        use_enum_values = True


class StickerPack(BaseModel):
    """贴纸包数据模型"""
    
    id: str = Field(..., description="贴纸包唯一标识")
    name: str = Field(..., description="贴纸包名称")
    theme: str = Field(..., description="主题")
    stickers: List[Sticker] = Field(default_factory=list, description="贴纸列表")
    
    # 统计信息
    total_count: int = Field(default=0, description="总数量")
    success_count: int = Field(default=0, description="成功数量")
    failed_count: int = Field(default=0, description="失败数量")
    
    # 类型分布
    type_distribution: Dict[str, int] = Field(
        default_factory=dict,
        description="类型分布统计"
    )
    
    # v3 pipeline provenance
    topic_result: Optional[Dict[str, Any]] = Field(
        None, description="TopicGenerationResult — topics, use cases, style types"
    )
    theme_content: Optional[Dict[str, Any]] = Field(
        None, description="ThemeContent used to generate this pack"
    )
    style_guide: Optional[Dict[str, Any]] = Field(
        None, description="Pack Style Guide used for visual consistency"
    )
    
    # 元数据
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    
    def add_sticker(self, sticker: Sticker):
        """添加贴纸"""
        self.stickers.append(sticker)
        self.total_count = len(self.stickers)
        self._update_statistics()
    
    def _update_statistics(self):
        """更新统计信息"""
        self.success_count = sum(1 for s in self.stickers if s.status == "success")
        self.failed_count = sum(1 for s in self.stickers if s.status == "failed")
        
        # 更新类型分布
        self.type_distribution = {}
        for sticker in self.stickers:
            type_name = sticker.type.value if hasattr(sticker.type, 'value') else sticker.type
            self.type_distribution[type_name] = self.type_distribution.get(type_name, 0) + 1
    
    def mark_completed(self):
        """标记为完成"""
        self.completed_at = datetime.now()
        if self.created_at:
            self.duration_seconds = (self.completed_at - self.created_at).total_seconds()
        self._update_statistics()
    
    def get_success_rate(self) -> float:
        """获取成功率"""
        if self.total_count == 0:
            return 0.0
        return self.success_count / self.total_count
    
    def get_stickers_by_type(self, sticker_type: StickerType) -> List[Sticker]:
        """按类型获取贴纸"""
        return [s for s in self.stickers if s.type == sticker_type]
    
    def get_successful_stickers(self) -> List[Sticker]:
        """获取成功的贴纸"""
        return [s for s in self.stickers if s.status == "success"]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.dict()
    
    def to_summary(self) -> Dict[str, Any]:
        """转换为摘要"""
        return {
            "id": self.id,
            "name": self.name,
            "theme": self.theme,
            "total_count": self.total_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "success_rate": f"{self.get_success_rate():.1%}",
            "type_distribution": self.type_distribution,
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
