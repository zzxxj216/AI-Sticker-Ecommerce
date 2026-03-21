"""统一 Agent 响应协议

所有 Agent 输出统一 envelope 格式：
  - message_to_user: 自然语言说明（给用户/聊天界面展示）
  - structured_data: 结构化数据（给程序/下游 Agent 消费）
  - control: 流程控制信号（状态/下一步/是否需要用户输入）
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class AgentControl(BaseModel):
    """Agent 流程控制信号"""

    status: str = Field(default="completed", description="completed / needs_input / error")
    needs_user_input: bool = Field(default=False, description="是否需要暂停等待用户输入")
    next_recommended_action: str = Field(default="", description="推荐的下一步动作，如 run_designer_agent")
    suggested_ui_component: str = Field(default="", description="建议的 UI 组件，如 brief_card / palette_picker")


class AgentResponse(BaseModel):
    """统一 Agent 响应格式

    用法：
      - 前端展示 message_to_user
      - 程序解析 structured_data
      - 编排器读取 control 决定下一步
    """

    message_to_user: str = Field(default="", description="给用户看的自然语言说明")
    structured_data: Dict[str, Any] = Field(default_factory=dict, description="结构化数据")
    control: AgentControl = Field(default_factory=AgentControl, description="流程控制")

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "AgentResponse":
        """从 LLM 返回的原始 JSON 构建 AgentResponse"""
        return cls(
            message_to_user=raw.get("message_to_user", ""),
            structured_data=raw.get("structured_data", {}),
            control=AgentControl(**raw.get("control", {})),
        )
