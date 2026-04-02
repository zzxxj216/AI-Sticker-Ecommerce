"""用户偏好数据模型"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class StylePreference(BaseModel):
    """风格偏好"""
    
    style_name: str = Field(..., description="风格名称")
    preference_score: float = Field(..., ge=0, le=1, description="偏好分数 0-1")
    usage_count: int = Field(default=0, description="使用次数")
    last_used: Optional[datetime] = Field(None, description="最后使用时间")


class ThemePreference(BaseModel):
    """主题偏好"""
    
    theme_name: str = Field(..., description="主题名称")
    usage_count: int = Field(default=0, description="使用次数")
    success_rate: float = Field(default=0.0, ge=0, le=1, description="成功率")
    last_used: Optional[datetime] = Field(None, description="最后使用时间")


class UserPreference(BaseModel):
    """用户偏好数据模型"""
    
    user_id: str = Field(..., description="用户 ID")
    
    # 风格偏好
    style_preferences: List[StylePreference] = Field(
        default_factory=list,
        description="风格偏好列表"
    )
    
    # 主题偏好
    theme_preferences: List[ThemePreference] = Field(
        default_factory=list,
        description="主题偏好列表"
    )
    
    # 常用设置
    default_sticker_count: int = Field(default=40, description="默认贴纸数量")
    default_variation_degree: str = Field(default="medium", description="默认变化程度")
    
    # 避免的元素
    avoided_elements: List[str] = Field(default_factory=list, description="避免的元素")
    preferred_elements: List[str] = Field(default_factory=list, description="偏好的元素")
    
    # 统计信息
    total_generations: int = Field(default=0, description="总生成次数")
    total_stickers: int = Field(default=0, description="总贴纸数")
    
    # 时间信息
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    
    def add_style_preference(self, style_name: str, score: float = 0.5):
        """添加或更新风格偏好"""
        # 查找现有偏好
        for pref in self.style_preferences:
            if pref.style_name == style_name:
                pref.preference_score = (pref.preference_score + score) / 2
                pref.usage_count += 1
                pref.last_used = datetime.now()
                self.updated_at = datetime.now()
                return
        
        # 添加新偏好
        self.style_preferences.append(
            StylePreference(
                style_name=style_name,
                preference_score=score,
                usage_count=1,
                last_used=datetime.now()
            )
        )
        self.updated_at = datetime.now()
    
    def add_theme_preference(self, theme_name: str, success_rate: float = 1.0):
        """添加或更新主题偏好"""
        # 查找现有偏好
        for pref in self.theme_preferences:
            if pref.theme_name == theme_name:
                # 更新成功率（加权平均）
                total = pref.usage_count + 1
                pref.success_rate = (pref.success_rate * pref.usage_count + success_rate) / total
                pref.usage_count += 1
                pref.last_used = datetime.now()
                self.updated_at = datetime.now()
                return
        
        # 添加新偏好
        self.theme_preferences.append(
            ThemePreference(
                theme_name=theme_name,
                usage_count=1,
                success_rate=success_rate,
                last_used=datetime.now()
            )
        )
        self.updated_at = datetime.now()
    
    def get_top_styles(self, limit: int = 5) -> List[StylePreference]:
        """获取最喜欢的风格"""
        sorted_styles = sorted(
            self.style_preferences,
            key=lambda x: (x.preference_score, x.usage_count),
            reverse=True
        )
        return sorted_styles[:limit]
    
    def get_top_themes(self, limit: int = 5) -> List[ThemePreference]:
        """获取最常用的主题"""
        sorted_themes = sorted(
            self.theme_preferences,
            key=lambda x: (x.usage_count, x.success_rate),
            reverse=True
        )
        return sorted_themes[:limit]
    
    def increment_generation_count(self, sticker_count: int = 1):
        """增加生成计数"""
        self.total_generations += 1
        self.total_stickers += sticker_count
        self.updated_at = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()
    
    def to_summary(self) -> Dict[str, Any]:
        """转换为摘要"""
        return {
            "user_id": self.user_id,
            "total_generations": self.total_generations,
            "total_stickers": self.total_stickers,
            "top_styles": [s.style_name for s in self.get_top_styles(3)],
            "top_themes": [t.theme_name for t in self.get_top_themes(3)],
            "default_settings": {
                "sticker_count": self.default_sticker_count,
                "variation_degree": self.default_variation_degree,
            },
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class PreferenceHistory(BaseModel):
    """偏好历史记录"""
    
    id: str = Field(..., description="记录 ID")
    user_id: str = Field(..., description="用户 ID")
    action: str = Field(..., description="操作类型")
    details: Dict[str, Any] = Field(default_factory=dict, description="详细信息")
    timestamp: datetime = Field(default_factory=datetime.now)
