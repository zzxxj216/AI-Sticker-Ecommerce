"""会话数据模型"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict


class SessionState(str, Enum):
    """会话状态"""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionMessage(BaseModel):
    """会话消息"""
    
    role: str = Field(..., description="角色: user, assistant, system")
    content: str = Field(..., description="消息内容")
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SessionContext(BaseModel):
    """会话上下文"""
    
    theme: Optional[str] = Field(None, description="当前主题")
    sticker_count: Optional[int] = Field(None, description="贴纸数量")
    current_step: Optional[str] = Field(None, description="当前步骤")
    
    # 用户偏好（会话级别）
    preferred_styles: List[str] = Field(default_factory=list)
    avoided_styles: List[str] = Field(default_factory=list)
    
    # 临时数据
    temp_data: Dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    """会话数据模型"""
    
    model_config = ConfigDict(use_enum_values=True)
    
    id: str = Field(..., description="会话唯一标识")
    user_id: Optional[str] = Field(None, description="用户 ID")
    
    # 会话状态
    state: SessionState = Field(default=SessionState.ACTIVE)
    
    # 会话内容
    messages: List[SessionMessage] = Field(default_factory=list)
    context: SessionContext = Field(default_factory=SessionContext)
    
    # 生成结果
    generated_stickers: List[str] = Field(default_factory=list, description="生成的贴纸 ID 列表")
    sticker_pack_id: Optional[str] = Field(None, description="关联的贴纸包 ID")
    
    # 时间信息
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    
    # 统计信息
    total_messages: int = Field(default=0)
    total_stickers: int = Field(default=0)
    
    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        """添加消息"""
        message = SessionMessage(
            role=role,
            content=content,
            metadata=metadata or {}
        )
        self.messages.append(message)
        self.total_messages = len(self.messages)
        self.updated_at = datetime.now()
    
    def add_sticker(self, sticker_id: str):
        """添加生成的贴纸"""
        self.generated_stickers.append(sticker_id)
        self.total_stickers = len(self.generated_stickers)
        self.updated_at = datetime.now()
    
    def update_context(self, **kwargs):
        """更新上下文"""
        for key, value in kwargs.items():
            if hasattr(self.context, key):
                setattr(self.context, key, value)
        self.updated_at = datetime.now()
    
    def mark_completed(self):
        """标记为完成"""
        self.state = SessionState.COMPLETED
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()
    
    def mark_failed(self):
        """标记为失败"""
        self.state = SessionState.FAILED
        self.updated_at = datetime.now()
    
    def get_duration_seconds(self) -> Optional[float]:
        """获取会话持续时间（秒）"""
        if self.completed_at:
            return (self.completed_at - self.created_at).total_seconds()
        return None
    
    def get_recent_messages(self, count: int = 10) -> List[SessionMessage]:
        """获取最近的消息"""
        return self.messages[-count:]
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()
    
    def to_summary(self) -> Dict[str, Any]:
        """转换为摘要"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "state": self.state.value,
            "total_messages": self.total_messages,
            "total_stickers": self.total_stickers,
            "theme": self.context.theme,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.get_duration_seconds(),
        }
