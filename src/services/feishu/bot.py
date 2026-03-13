"""飞书贴纸包批量生成机器人

用户通过飞书与 Bot 对话，描述贴纸包主题/风格/数量，
确认后自动调用 BatchPipeline 并行生成，完成后将预览图发到聊天。

Architecture:
    Feishu event  →  FeishuBot.handle_message()
                        ↓
                  BatchSession.process_message()  (收集主题)
                        ↓  (确认后)
                  BatchSession.start_pipeline_async()
                        ↓  (完成回调)
                  收集预览图  →  Feishu send image API
"""

import json
import re
import time
import threading
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

from src.core.config import config
from src.core.logger import get_logger
from src.services.batch.batch_session import BatchSession, BatchSessionResponse
from src.models.batch import BatchResult

logger = get_logger("service.feishu_bot")


def _extract_post_text(content: dict) -> str:
    """Robustly extract plain text from Feishu post (rich-text) content.

    Handles multiple structural variations:
      1. {"zh_cn": {"title": "...", "content": [[{tag, text}, ...]]}}
      2. {"post": {"zh_cn": {"title": "...", "content": [[...]]}}}
      3. {"title": "...", "content": [[...]]}
      4. {"content": [[...]]}
    """
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
        langs = ("zh_cn", "en_us", "ja_jp", "zh_tw", "zh_hk")
        for lang in langs:
            post = root.get(lang)
            if isinstance(post, dict):
                title = title or post.get("title", "")
                body = post.get("content", [])
                if isinstance(body, list):
                    _walk_paragraphs(body)
                if parts:
                    return True
        return False

    # Variation 2: {"post": {"zh_cn": {...}}}
    if isinstance(content.get("post"), dict):
        _try_lang_dict(content["post"])

    # Variation 1: {"zh_cn": {...}}
    if not parts:
        _try_lang_dict(content)

    # Variation 3/4: {"content": [[...]]}
    if not parts and isinstance(content.get("content"), list):
        title = title or content.get("title", "")
        _walk_paragraphs(content["content"])

    result = "\n".join(parts).strip()
    if not result and title:
        result = title.strip()
    return result


class FeishuBot:
    """飞书贴纸包批量生成机器人。

    每个用户独立 BatchSession，对话收集主题后并行生成贴纸包，
    生成完成后只将预览图发送到飞书聊天。
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        verification_token: Optional[str] = None,
        encrypt_key: Optional[str] = None,
        auto_generate: bool = True,
    ):
        self._app_id = app_id or config.feishu_app_id
        self._app_secret = app_secret or config.feishu_app_secret
        self._verification_token = verification_token or config.feishu_verification_token
        self._encrypt_key = encrypt_key or config.feishu_encrypt_key

        if not self._app_id or not self._app_secret:
            raise ValueError(
                "Feishu credentials missing. "
                "Set FEISHU_APP_ID and FEISHU_APP_SECRET in .env"
            )

        self._auto_generate = auto_generate

        self._client = (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

        self._sessions: Dict[str, BatchSession] = {}
        self._user_chat_ids: Dict[str, str] = {}
        self._seen_message_ids: Dict[str, float] = {}
        self._lock = threading.Lock()

        logger.info("FeishuBot initialized (batch mode, auto_generate=%s)", auto_generate)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_or_create_session(self, user_id: str) -> BatchSession:
        with self._lock:
            if user_id not in self._sessions:
                self._sessions[user_id] = BatchSession()
                logger.info("New BatchSession for user %s", user_id)
            return self._sessions[user_id]

    def _reset_session(self, user_id: str) -> None:
        with self._lock:
            if user_id in self._sessions:
                self._sessions[user_id].reset()
                logger.info("Session reset for user %s", user_id)

    def _remove_session(self, user_id: str) -> None:
        with self._lock:
            self._sessions.pop(user_id, None)

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
            logger.debug("Duplicate message %s, skipping", message_id)
            return

        self._user_chat_ids[user_id] = chat_id

        content_str = message.get("content", "{}")
        logger.info("Received msg_type=%s from user %s, content=%s",
                     msg_type, user_id, content_str[:300])

        user_text = self._extract_text(msg_type, content_str)

        if user_text is None:
            logger.info("Unsupported msg_type=%s, content=%s", msg_type, content_str[:200])
            self._send_text(chat_id, "目前只支持文字消息哦~ 请用文字描述你想要的贴纸包！")
            return

        if not user_text:
            self._send_text(chat_id, "消息内容好像是空的，请重新输入~")
            return

        logger.info("User %s: %s", user_id, user_text[:80])

        if self._handle_command(user_id, chat_id, user_text):
            return

        session = self._get_or_create_session(user_id)

        if session.phase in ("batch_planning", "batch_generating", "batch_preview"):
            self._send_text(chat_id, "正在生成中，请耐心等待~ ⏳")
            return

        try:
            resp = session.process_message(user_text)
        except Exception as e:
            logger.error("Session error for user %s: %s", user_id, e)
            self._send_text(chat_id, f"处理出错了，请稍后再试 🙏\n\n错误信息: {e}")
            return

        self._send_text(chat_id, resp.message)

        if resp.data.get("ready") and self._auto_generate:
            pack_count = resp.data.get("pack_count", 6)
            self._send_text(
                chat_id,
                f"🚀 开始并行生成 {pack_count} 套贴纸包...\n"
                f"关键步骤会实时通知你，请耐心等待！",
            )
            session.start_pipeline_async(
                skip_images=True,
                on_progress=lambda event, data: self._on_progress(
                    chat_id, event, data
                ),
                on_complete=lambda result: self._on_pipeline_complete(
                    user_id, chat_id, result
                ),
            )

    # ------------------------------------------------------------------
    # Text extraction — supports text and post (rich text) messages
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(msg_type: str, content_str: str) -> Optional[str]:
        """Extract plain text from Feishu message content.

        Returns extracted text, empty string if parsed but empty,
        or None if message type is unsupported.
        """
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
            logger.info("Post content structure: %s", json.dumps(content, ensure_ascii=False)[:500])
            return _extract_post_text(content)

        return None

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    def _handle_command(self, user_id: str, chat_id: str, text: str) -> bool:
        cmd = text.lower().strip()

        if cmd in ("/help", "/帮助"):
            self._send_text(chat_id, self._help_text())
            return True

        if cmd in ("/reset", "/重置"):
            self._reset_session(user_id)
            self._send_text(chat_id, "会话已重置 ✅ 请重新告诉我你想做什么贴纸~")
            return True

        return False

    # ------------------------------------------------------------------
    # Pipeline progress — send key milestones to Feishu
    # ------------------------------------------------------------------

    def _on_progress(self, chat_id: str, event: str, data: dict) -> None:
        """Pipeline 进度回调：在关键节点推送消息到飞书。"""

        if event == "plan_done":
            packs = data.get("packs", [])
            pack_names = [p.get("name", f"Pack {p.get('index')}") for p in packs]
            self._send_text(
                chat_id,
                f"📋 规划完成！共 {len(packs)} 套贴纸包：\n"
                + "\n".join(f"  {i+1}. {name}" for i, name in enumerate(pack_names))
                + "\n\n正在生成内容概念...",
            )

        elif event == "content_done":
            packs_info = data.get("packs", [])
            total_concepts = sum(p.get("concepts", 0) for p in packs_info)
            self._send_text(
                chat_id,
                f"💡 内容概念生成完成！共 {total_concepts} 个贴纸创意\n"
                f"正在细化创意和生成 style guide...",
            )

        elif event == "ideas_done":
            packs_info = data.get("packs", [])
            total_ideas = sum(p.get("ideas", 0) for p in packs_info)
            self._send_text(
                chat_id,
                f"🖌️ 创意细化完成！共 {total_ideas} 个 image prompt\n"
                f"开始生成预览图，每个话题一张...",
            )

        elif event == "topic_preview_done":
            success = data.get("success", False)
            topic = data.get("topic", "")
            pack_index = data.get("pack_index", 0)
            image_path_str = data.get("image_path")

            if success and image_path_str:
                image_path = Path(image_path_str)
                if image_path.exists():
                    self._send_text(chat_id, f"🖼️ Pack {pack_index} — {topic}")
                    self._send_image(chat_id, image_path)

    # ------------------------------------------------------------------
    # Pipeline completion
    # ------------------------------------------------------------------

    def _on_pipeline_complete(
        self, user_id: str, chat_id: str, result: BatchResult
    ) -> None:
        """Pipeline 完成后的回调。"""
        summary = result.to_summary()
        total_packs = summary.get("total_packs", 0)
        duration = summary.get("duration_seconds")
        duration_str = f"，总耗时 {duration:.0f}s" if duration else ""

        packs_detail = []
        for p in summary.get("packs", []):
            packs_detail.append(
                f"  {p.get('index')}. {p.get('name')} — "
                f"{p.get('previews', 0)} 张预览图"
            )

        self._send_text(
            chat_id,
            f"🎊 全部完成！共 {total_packs} 套贴纸包{duration_str}\n\n"
            + "\n".join(packs_detail)
            + "\n\n发送新消息可以开始设计下一套贴纸包~",
        )
        self._remove_session(user_id)

    # ------------------------------------------------------------------
    # Feishu API — send text
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Feishu API — send image
    # ------------------------------------------------------------------

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
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
            }.get(suffix, "image/png")

            with open(image_path, "rb") as f:
                body = CreateImageRequestBody()
                body.image_type = "message"
                body.image = (image_path.name, f, mime)

                request = CreateImageRequest.builder().request_body(body).build()
                response = self._client.im.v1.image.create(request)

            if response.success():
                image_key = response.data.image_key
                logger.info("Image uploaded: %s → %s", image_path.name, image_key)
                return image_key
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
    # Lark SDK event handler
    # ------------------------------------------------------------------

    def build_event_handler(self) -> lark.EventDispatcherHandler:
        handler = (
            lark.EventDispatcherHandler.builder(
                self._verification_token, self._encrypt_key
            )
            .register_p2_im_message_receive_v1(self._on_p2_message)
            .build()
        )
        return handler

    def _on_p2_message(self, data: P2ImMessageReceiveV1) -> None:
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
        self.handle_message(event_data)

    # ------------------------------------------------------------------
    # Help text
    # ------------------------------------------------------------------

    @staticmethod
    def _help_text() -> str:
        return (
            "🎨 AI 贴纸包批量生成助手\n\n"
            "直接用文字描述你想要的贴纸主题、风格和数量，\n"
            "AI 会和你自然对话，确认后自动并行生成贴纸包！\n\n"
            "完成后将预览图直接发到聊天中 📸\n\n"
            "📌 命令:\n"
            "  /帮助 — 显示此帮助信息\n"
            "  /重置 — 重新开始一个新的设计\n\n"
            "💡 示例:\n"
            "  「做一套猫咪日常的贴纸包，可爱卡通风格」\n"
            "  「程序员主题表情包，赛博朋克风，2包」\n"
            "  「复古美式潮流方向，波普艺术风格，黑红白美学」"
        )
