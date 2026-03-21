"""Feishu Blog Bot — Feishu adapter connecting messages to the BlogAgent.

Receives Feishu messages via lark-oapi SDK, routes them to the BlogAgent
(ReAct loop), and sends responses back. Supports text and post (rich-text)
messages, deduplication, and real-time progress feedback.
"""

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    P2ImMessageReceiveV1,
)

from src.core.logger import get_logger
from src.services.ai.gemini_service import GeminiService
from src.services.feishu.agent_memory import AgentMemory
from src.services.feishu.agent_tools import AgentToolExecutor
from src.services.feishu.blog_agent import BlogAgent
from src.services.feishu.scheduler import SchedulerManager

logger = get_logger("service.feishu_blog_bot")


def _extract_post_text(content: dict) -> str:
    """Extract plain text from Feishu post (rich-text) content."""
    parts: list[str] = []
    title = ""

    def _walk_paragraphs(paragraphs: list) -> None:
        for paragraph in paragraphs:
            if not isinstance(paragraph, list):
                continue
            line_parts: list[str] = []
            for element in paragraph:
                if not isinstance(element, dict):
                    continue
                text = element.get("text") or element.get("content") or ""
                if text:
                    line_parts.append(text)
            if line_parts:
                parts.append("".join(line_parts))

    def _try_lang_dict(root: dict) -> bool:
        nonlocal title
        for lang in ("zh_cn", "en_us", "ja_jp"):
            post = root.get(lang)
            if isinstance(post, dict):
                title = title or post.get("title", "")
                body = post.get("content", [])
                if isinstance(body, list):
                    _walk_paragraphs(body)
                if parts:
                    return True
        return False

    if isinstance(content.get("post"), dict):
        _try_lang_dict(content["post"])
    if not parts:
        _try_lang_dict(content)
    if not parts and isinstance(content.get("content"), list):
        title = title or content.get("title", "")
        _walk_paragraphs(content["content"])

    result = "\n".join(parts).strip()
    return result or title.strip()


class FeishuBlogBot:
    """Feishu adapter for the Blog Agent.

    Handles message reception, deduplication, text extraction,
    and response delivery. Delegates all reasoning to BlogAgent.
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
    ):
        self._app_id = app_id or os.getenv("FEISHU_BLOG_APP_ID", "")
        self._app_secret = app_secret or os.getenv("FEISHU_BLOG_APP_SECRET", "")

        if not self._app_id or not self._app_secret:
            raise ValueError(
                "Feishu Blog Bot credentials missing. "
                "Set FEISHU_BLOG_APP_ID and FEISHU_BLOG_APP_SECRET in .env"
            )

        self._client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

        self._seen_message_ids: Dict[str, float] = {}
        self._lock = threading.Lock()

        db_path = os.getenv("AGENT_DB_PATH", "./data/agent.db")
        memory = AgentMemory(max_history=30, db_path=db_path)
        tool_executor = AgentToolExecutor(
            memory=memory,
            send_progress=self.send_progress,
            send_image=self.send_image_to_chat,
        )

        scheduler = SchedulerManager()
        scheduler.set_generate_callback(self._on_scheduled_generate)
        tool_executor.set_scheduler(scheduler)
        self._scheduler = scheduler
        self._tool_executor = tool_executor

        gemini_text_model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")
        llm = GeminiService(timeout=120, model=gemini_text_model)

        self._agent = BlogAgent(llm_service=llm, memory=memory, tools=tool_executor)
        self._memory = memory

        logger.info("FeishuBlogBot initialized (WebSocket mode)")

    # ------------------------------------------------------------------
    # Feishu SDK event handler (for lark.ws.Client)
    # ------------------------------------------------------------------

    def build_event_handler(self) -> lark.EventDispatcherHandler:
        builder = lark.EventDispatcherHandler.builder("", "")
        builder.register_p2_im_message_receive_v1(self._on_p2_message)

        # Silently consume message-read events to avoid SDK "processor not found" errors
        try:
            builder.register_p2_im_message_read_v1(lambda _: None)
        except (AttributeError, TypeError):
            pass

        handler = builder.build()
        return handler

    def _on_p2_message(self, data: P2ImMessageReceiveV1) -> None:
        """Handle P2 message event from lark SDK."""
        event_data = {
            "message": {
                "message_id": data.event.message.message_id,
                "chat_id": data.event.message.chat_id,
                "message_type": data.event.message.message_type,
                "content": data.event.message.content,
            },
            "sender": {
                "sender_id": {
                    "open_id": data.event.sender.sender_id.open_id,
                },
            },
        }
        threading.Thread(
            target=self.handle_message, args=(event_data,), daemon=True
        ).start()

    # ------------------------------------------------------------------
    # Core message handler
    # ------------------------------------------------------------------

    def handle_message(self, event_data: dict) -> None:
        """Process a single incoming Feishu message event."""
        message = event_data.get("message", {})
        message_id = message.get("message_id", "")
        chat_id = message.get("chat_id", "")
        msg_type = message.get("message_type", "")
        sender = event_data.get("sender", {})
        user_id = sender.get("sender_id", {}).get("open_id", "unknown")

        if self._is_duplicate(message_id):
            return

        content_str = message.get("content", "{}")
        user_text = self._extract_text(msg_type, content_str)

        if user_text is None:
            self._send_text(chat_id, "Currently only text messages are supported.")
            return

        if not user_text:
            self._send_text(chat_id, "Message seems empty, please try again.")
            return

        logger.info(f"User {user_id}: {user_text[:80]}")

        if self._handle_command(user_id, chat_id, user_text):
            return

        try:
            response = self._agent.process(user_id, chat_id, user_text)
            self._send_text(chat_id, response)
        except Exception as e:
            logger.error(f"Agent error for user {user_id}: {e}", exc_info=True)
            self._send_text(chat_id, f"Processing error, please try again later.\n\nError: {e}")

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def _handle_command(self, user_id: str, chat_id: str, text: str) -> bool:
        cmd = text.lower().strip()

        if cmd in ("/help", "/帮助"):
            self._send_text(chat_id, self._help_text())
            return True

        if cmd in ("/reset", "/重置"):
            self._memory.clear_history(user_id)
            self._send_text(chat_id, "Session reset. Tell me what blog you'd like to create!")
            return True

        return False

    # ------------------------------------------------------------------
    # Scheduled generation callback
    # ------------------------------------------------------------------

    def _on_scheduled_generate(self, task, auto_publish: bool):
        """Called by SchedulerManager when a scheduled task fires."""
        cancel_event = threading.Event()
        self._tool_executor._cancel_flags[task.task_id] = cancel_event
        self._tool_executor._run_blog_pipeline(task, auto_publish, cancel_event)

    # ------------------------------------------------------------------
    # Feishu messaging
    # ------------------------------------------------------------------

    def send_progress(self, chat_id: str, message: str):
        """Send a progress update to a Feishu chat (public for tool callbacks)."""
        self._send_text(chat_id, message)

    def send_image_to_chat(self, chat_id: str, image_path: Path):
        """Send an image to a Feishu chat (public for tool callbacks)."""
        self._send_image(chat_id, image_path)

    def _send_text(self, chat_id: str, text: str) -> None:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        try:
            response = self._client.im.v1.message.create(request)
            if not response.success():
                logger.error(
                    "Failed to send text: code=%s, msg=%s",
                    response.code, response.msg,
                )
        except Exception as e:
            logger.error("Send text error: %s", e)

    def _send_image(self, chat_id: str, image_path: Path) -> None:
        image_key = self._upload_image(image_path)
        if not image_key:
            return

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("image")
            .content(json.dumps({"image_key": image_key}))
            .build()
        )
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        try:
            response = self._client.im.v1.message.create(request)
            if not response.success():
                logger.error(
                    "Failed to send image: code=%s, msg=%s",
                    response.code, response.msg,
                )
        except Exception as e:
            logger.error("Send image error: %s", e)

    def _upload_image(self, image_path: Path) -> Optional[str]:
        try:
            suffix = image_path.suffix.lower()
            mime = {
                ".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp",
            }.get(suffix, "image/png")

            with open(image_path, "rb") as f:
                body = CreateImageRequestBody()
                body.image_type = "message"
                body.image = (image_path.name, f, mime)
                request = CreateImageRequest.builder().request_body(body).build()
                response = self._client.im.v1.image.create(request)

            if response.success():
                return response.data.image_key
            else:
                logger.error(
                    "Image upload failed: code=%s, msg=%s",
                    response.code, response.msg,
                )
                return None
        except Exception as e:
            logger.error("Image upload error: %s", e)
            return None

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(msg_type: str, content_str: str) -> Optional[str]:
        try:
            content = json.loads(content_str)
        except (json.JSONDecodeError, TypeError):
            return None

        if msg_type == "text":
            text = content.get("text", "")
            if isinstance(text, str):
                text = re.sub(r"@_user_\d+\s*", "", text).strip()
                return text
            return ""

        if msg_type == "post":
            return _extract_post_text(content)

        return None

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _is_duplicate(self, message_id: str) -> bool:
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._seen_message_ids.items() if now - v > 300]
            for k in stale:
                del self._seen_message_ids[k]
            if message_id in self._seen_message_ids:
                return True
            self._seen_message_ids[message_id] = now
            return False

    # ------------------------------------------------------------------
    # Help text
    # ------------------------------------------------------------------

    @staticmethod
    def _help_text() -> str:
        return (
            "Blog Generation Agent\n\n"
            "I can help you generate, schedule, and manage SEO blog posts "
            "for your sticker store.\n\n"
            "What I can do:\n"
            "- Generate blog posts: tell me the topic and keywords\n"
            "- Schedule publishing: specify a time and I'll handle it\n"
            "- Manage articles: publish, unpublish, or list Shopify articles\n"
            "- Check status: ask about any running or past task\n\n"
            "Commands:\n"
            "  /help — Show this help\n"
            "  /reset — Clear conversation history\n\n"
            "Examples:\n"
            '  "Write a blog about cowboy western stickers"\n'
            '  "Generate a blog about anime stickers, publish tomorrow at 10am"\n'
            '  "Show me all draft articles on Shopify"\n'
            '  "Publish the latest draft article"'
        )
