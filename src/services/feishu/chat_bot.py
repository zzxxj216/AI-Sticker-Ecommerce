"""飞书通用对话聊天机器人

支持两种工作模式：
1. 聊天模式（默认）— 通用 AI 对话，回答各类问题
2. 贴纸模式 — 切换到贴纸包设计流程（复用 InteractiveSession）

Architecture:
    Feishu event  →  FeishuChatBot.handle_message()
                        ↓
                  ChatService.chat()  (聊天模式)
                  InteractiveSession  (贴纸模式)
                        ↓
                  Feishu send message API

Commands:
    /帮助, /help     — 显示帮助信息
    /重置, /reset    — 重置当前会话
    /贴纸, /sticker  — 切换到贴纸设计模式
    /聊天, /chat     — 切换回聊天模式
    /状态, /status   — 查看会话状态
"""

from __future__ import annotations

import json
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
from src.services.ai.chat_service import ChatService
from src.services.sticker.interactive_session import InteractiveSession

logger = get_logger("service.feishu_chat_bot")

MODE_CHAT = "chat"
MODE_STICKER = "sticker"


class FeishuChatBot:
    """飞书聊天机器人 — 通用对话 + 贴纸生成双模式。

    每个用户独立维护会话状态和对话模式。
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        verification_token: Optional[str] = None,
        encrypt_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
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

        self._chat_service = ChatService(system_prompt=system_prompt)
        self._sticker_sessions: Dict[str, InteractiveSession] = {}
        self._user_modes: Dict[str, str] = {}
        self._seen_message_ids: Dict[str, float] = {}
        self._lock = threading.Lock()

        logger.info("FeishuChatBot initialized (dual-mode)")

    # ------------------------------------------------------------------
    # Mode management
    # ------------------------------------------------------------------

    def _get_mode(self, user_id: str) -> str:
        return self._user_modes.get(user_id, MODE_CHAT)

    def _set_mode(self, user_id: str, mode: str) -> None:
        self._user_modes[user_id] = mode
        logger.info("User %s switched to %s mode", user_id, mode)

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

        if msg_type != "text":
            self._send_text(chat_id, "目前只支持文字消息哦~ 请直接用文字和我对话吧！")
            return

        content_str = message.get("content", "{}")
        try:
            content = json.loads(content_str)
        except json.JSONDecodeError:
            content = {}
        user_text = content.get("text", "").strip()

        if not user_text:
            return

        logger.info("[%s] User %s: %s", self._get_mode(user_id), user_id, user_text[:80])

        # --- Command routing ---
        if self._handle_command(user_id, chat_id, user_text):
            return

        # --- Mode-based routing ---
        mode = self._get_mode(user_id)
        if mode == MODE_STICKER:
            self._handle_sticker_mode(user_id, chat_id, user_text)
        else:
            self._handle_chat_mode(user_id, chat_id, user_text)

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------

    def _handle_command(self, user_id: str, chat_id: str, text: str) -> bool:
        """Handle slash commands. Returns True if a command was handled."""
        cmd = text.lower().strip()

        if cmd in ("/help", "/帮助"):
            self._send_text(chat_id, self._help_text())
            return True

        if cmd in ("/reset", "/重置"):
            self._reset_user(user_id)
            self._send_text(chat_id, "会话已重置 ✅ 可以开始新的对话了！")
            return True

        if cmd in ("/chat", "/聊天"):
            self._set_mode(user_id, MODE_CHAT)
            self._send_text(chat_id, "已切换到聊天模式 💬 你可以问我任何问题！")
            return True

        if cmd in ("/sticker", "/贴纸"):
            self._set_mode(user_id, MODE_STICKER)
            if user_id not in self._sticker_sessions:
                self._sticker_sessions[user_id] = InteractiveSession()
            self._send_text(
                chat_id,
                "已切换到贴纸设计模式 🎨\n"
                "请告诉我你想做什么主题的贴纸包！\n"
                "输入 /聊天 可以切换回普通对话。",
            )
            return True

        if cmd in ("/status", "/状态"):
            self._send_status(user_id, chat_id)
            return True

        return False

    # ------------------------------------------------------------------
    # Chat mode
    # ------------------------------------------------------------------

    def _handle_chat_mode(self, user_id: str, chat_id: str, text: str) -> None:
        """Process a message in general chat mode."""
        reply = self._chat_service.chat(user_id, text)
        self._send_text(chat_id, reply)

    # ------------------------------------------------------------------
    # Sticker mode (reuses existing InteractiveSession + PackGenerator)
    # ------------------------------------------------------------------

    def _handle_sticker_mode(self, user_id: str, chat_id: str, text: str) -> None:
        """Process a message in sticker design mode."""
        if user_id not in self._sticker_sessions:
            self._sticker_sessions[user_id] = InteractiveSession()

        session = self._sticker_sessions[user_id]

        try:
            resp = session.process_message(text)
        except Exception as e:
            logger.error("Sticker session error for user %s: %s", user_id, e)
            self._send_text(chat_id, f"处理出错了，请稍后再试 🙏\n\n错误信息: {e}")
            return

        reply = resp.message
        if resp.updated_slots:
            slots = ", ".join(resp.updated_slots.keys())
            reply += f"\n\n📋 已记录: {slots}"

        self._send_text(chat_id, reply)

        if resp.ready_for_generation and resp.configs:
            self._send_text(
                chat_id,
                f"🎉 配置已确认！共 {len(resp.configs)} 套贴纸包，开始生成中...\n"
                f"图片生成需要一些时间，请耐心等待~",
            )
            if self._auto_generate:
                threading.Thread(
                    target=self._background_generate,
                    args=(chat_id, user_id, resp.configs),
                    daemon=True,
                ).start()

    # ------------------------------------------------------------------
    # Background sticker generation
    # ------------------------------------------------------------------

    def _background_generate(
        self, chat_id: str, user_id: str, configs: list
    ) -> None:
        from src.services.sticker.pack_generator import PackGenerator

        generator = PackGenerator()

        for i, cfg in enumerate(configs):
            pack_label = f"Pack {i + 1}/{len(configs)}"
            self._send_text(
                chat_id,
                f"🖌️ 正在生成 {pack_label}: {cfg.theme} ({len(cfg.sticker_concepts)} 张)...",
            )

            try:
                pack = generator.generate_from_config(cfg, max_workers=3)
            except Exception as e:
                logger.error("Generation failed for %s: %s", pack_label, e)
                self._send_text(chat_id, f"❌ {pack_label} 生成失败: {e}")
                continue

            self._send_text(
                chat_id,
                f"✅ {pack_label} 完成！成功 {pack.success_count}/{pack.total_count}，"
                f"耗时 {pack.duration_seconds:.1f}s",
            )

            for sticker in pack.stickers:
                if sticker.status == "success" and sticker.image_path:
                    image_path = Path(sticker.image_path)
                    if image_path.exists():
                        self._send_image(chat_id, image_path)

        self._send_text(chat_id, "🎊 全部贴纸包生成完毕！发送新消息可以继续对话或设计新贴纸~")
        self._sticker_sessions.pop(user_id, None)
        self._set_mode(user_id, MODE_CHAT)

    # ------------------------------------------------------------------
    # User reset
    # ------------------------------------------------------------------

    def _reset_user(self, user_id: str) -> None:
        """Reset both chat and sticker sessions for a user."""
        self._chat_service.reset(user_id)
        if user_id in self._sticker_sessions:
            self._sticker_sessions[user_id].reset()
            del self._sticker_sessions[user_id]
        self._user_modes.pop(user_id, None)
        logger.info("Full reset for user %s", user_id)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _send_status(self, user_id: str, chat_id: str) -> None:
        mode = self._get_mode(user_id)
        info = self._chat_service.get_session_info(user_id)

        lines = [
            f"📊 会话状态",
            f"当前模式: {'💬 聊天' if mode == MODE_CHAT else '🎨 贴纸设计'}",
        ]

        if info:
            lines.extend([
                f"对话轮数: {info['turns']}",
                f"Token 用量: 输入 {info['total_input_tokens']}, 输出 {info['total_output_tokens']}",
                f"累计成本: ${info['total_cost']}",
            ])
        else:
            lines.append("（尚未开始对话）")

        if mode == MODE_STICKER and user_id in self._sticker_sessions:
            session = self._sticker_sessions[user_id]
            lines.append(f"贴纸阶段: {session.phase}")
            lines.append(f"配置摘要:\n{session.slots_summary()}")

        self._send_text(chat_id, "\n".join(lines))

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
            body = (
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(image_path)
                .build()
            )
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
            "🤖 AI 助手 — 飞书聊天机器人\n\n"
            "我可以和你进行自由对话，回答问题、写代码、做翻译、"
            "头脑风暴……几乎无所不能！\n\n"
            "此外，我还能帮你设计和生成 AI 贴纸包 🎨\n\n"
            "📌 命令:\n"
            "  /帮助 — 显示此帮助信息\n"
            "  /重置 — 重置当前会话\n"
            "  /聊天 — 切换到聊天模式（默认）\n"
            "  /贴纸 — 切换到贴纸设计模式\n"
            "  /状态 — 查看当前会话状态\n\n"
            "💡 使用示例:\n"
            "  「帮我写一段 Python 爬虫」\n"
            "  「翻译这段话成英文：……」\n"
            "  「/贴纸」→「我想做一套猫咪贴纸」"
        )
