"""通用多轮对话服务

封装 ClaudeService，提供面向聊天场景的高层接口：
- 多用户隔离的对话历史管理
- 上下文窗口控制 & 自动摘要压缩
- 可定制的系统提示词
- Token 用量统计

Usage:
    chat = ChatService()
    reply = chat.chat("user_001", "你好，介绍一下你自己")
    reply = chat.chat("user_001", "能帮我写一段 Python 代码吗？")
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.core.logger import get_logger
from src.services.ai.claude_service import ClaudeService

logger = get_logger("service.chat")

DEFAULT_SYSTEM_PROMPT = (
    "你是一个友好、专业的 AI 助手，在飞书平台上与用户对话。\n"
    "请用简洁、自然的中文回复，语气亲切但不过分口语化。\n"
    "如果用户的问题涉及代码、技术、创意、写作等，尽可能给出高质量的回答。\n"
    "当你不确定时，诚实地说明而非编造信息。"
)

MAX_HISTORY_TURNS = 40
CONTEXT_WINDOW = 20
SUMMARY_TRIGGER = 30


@dataclass
class ChatSession:
    """Single-user conversation state."""

    user_id: str
    history: List[Dict[str, str]] = field(default_factory=list)
    summary: str = ""
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0


class ChatService:
    """General-purpose multi-turn chat service wrapping Claude.

    Manages per-user sessions with automatic context compression.
    """

    def __init__(
        self,
        claude_service: Optional[ClaudeService] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        self.claude = claude_service or ClaudeService()
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._sessions: Dict[str, ChatSession] = {}

        logger.info("ChatService initialized (model=%s)", self.claude.model)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        user_id: str,
        message: str,
        system_override: Optional[str] = None,
    ) -> str:
        """Send a message and get a reply, maintaining conversation context.

        Args:
            user_id: Unique user identifier.
            message: User's message text.
            system_override: One-shot system prompt override for this turn.

        Returns:
            The assistant's reply text.
        """
        session = self._get_or_create_session(user_id)
        session.history.append({"role": "user", "content": message})
        session.last_active = time.time()

        system = system_override or self.system_prompt
        if session.summary:
            system += (
                "\n\n[之前对话摘要]\n"
                f"{session.summary}\n"
                "请基于以上摘要的上下文继续对话。"
            )

        messages = self._build_messages(session)

        try:
            result = self.claude.generate_multiturn(
                messages=messages,
                system=system,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            reply = result["text"].strip()

            session.total_input_tokens += result["usage"]["input_tokens"]
            session.total_output_tokens += result["usage"]["output_tokens"]
            session.total_cost += result["cost"]

        except Exception as e:
            logger.error("Chat failed for user %s: %s", user_id, e)
            reply = f"抱歉，处理消息时出了点问题，请稍后再试。\n\n错误信息: {e}"

        session.history.append({"role": "assistant", "content": reply})

        if len(session.history) >= SUMMARY_TRIGGER:
            self._compress_history(session)

        return reply

    def reset(self, user_id: str) -> None:
        """Clear a user's conversation history."""
        if user_id in self._sessions:
            self._sessions[user_id] = ChatSession(user_id=user_id)
            logger.info("Session reset for user %s", user_id)

    def get_session_info(self, user_id: str) -> Optional[Dict]:
        """Return session statistics for a user."""
        session = self._sessions.get(user_id)
        if not session:
            return None
        return {
            "user_id": user_id,
            "turns": len(session.history),
            "total_input_tokens": session.total_input_tokens,
            "total_output_tokens": session.total_output_tokens,
            "total_cost": round(session.total_cost, 4),
            "created_at": session.created_at,
            "last_active": session.last_active,
        }

    def set_system_prompt(self, prompt: str) -> None:
        """Update the default system prompt for all future turns."""
        self.system_prompt = prompt
        logger.info("System prompt updated (%d chars)", len(prompt))

    def cleanup_idle_sessions(self, max_idle_seconds: int = 3600) -> int:
        """Remove sessions idle for longer than max_idle_seconds."""
        now = time.time()
        stale = [
            uid for uid, s in self._sessions.items()
            if now - s.last_active > max_idle_seconds
        ]
        for uid in stale:
            del self._sessions[uid]
        if stale:
            logger.info("Cleaned up %d idle sessions", len(stale))
        return len(stale)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create_session(self, user_id: str) -> ChatSession:
        if user_id not in self._sessions:
            self._sessions[user_id] = ChatSession(user_id=user_id)
            logger.info("New chat session for user %s", user_id)
        return self._sessions[user_id]

    def _build_messages(self, session: ChatSession) -> List[Dict[str, str]]:
        """Build the message list, trimmed to the context window."""
        recent = session.history[-CONTEXT_WINDOW:]

        if recent and recent[0]["role"] == "assistant":
            recent = recent[1:]

        return list(recent)

    def _compress_history(self, session: ChatSession) -> None:
        """Summarize older turns and keep only recent ones."""
        to_summarize = session.history[:-CONTEXT_WINDOW]
        if not to_summarize:
            return

        summary_input = "\n".join(
            f"{m['role']}: {m['content'][:300]}" for m in to_summarize
        )

        prompt = (
            "请对以下对话内容做一个简洁的中文摘要（不超过 300 字），"
            "保留关键信息和上下文：\n\n"
            f"{summary_input}"
        )

        try:
            result = self.claude.generate(
                prompt=prompt,
                system="你是一个对话摘要助手，输出简洁精确的中文摘要。",
                max_tokens=500,
                temperature=0.3,
            )
            new_summary = result["text"].strip()
            if session.summary:
                session.summary = f"{session.summary}\n{new_summary}"
            else:
                session.summary = new_summary

            session.history = session.history[-CONTEXT_WINDOW:]
            logger.info(
                "History compressed for user %s: kept %d turns, summary %d chars",
                session.user_id, len(session.history), len(session.summary),
            )
        except Exception as e:
            logger.warning("History compression failed: %s", e)
            session.history = session.history[-(CONTEXT_WINDOW + 4):]
