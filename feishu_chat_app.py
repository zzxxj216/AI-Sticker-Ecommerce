"""飞书贴纸包批量生成机器人 — WebSocket 长连接

用户在飞书上与 Bot 对话，AI 引导完成贴纸包配置后自动并行生成，
生成完成后将预览图发送到聊天中。

Start:
    python feishu_chat_app.py

Prerequisites:
    1. pip install lark-oapi>=1.4.0
    2. Set FEISHU_APP_ID, FEISHU_APP_SECRET in .env
    3. In Feishu open platform -> Event Subscription:
       - Choose "Use long-polling to receive events"
       - Subscribe to: im.message.receive_v1
    4. Grant bot permissions: im:message, im:message:send_as_bot, im:resource
"""

import os
import signal
import sys

from dotenv import load_dotenv

load_dotenv(override=True)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

import lark_oapi as lark

from src.services.feishu.bot import FeishuBot


def main():
    bot = FeishuBot(auto_generate=True)
    event_handler = bot.build_event_handler()

    app_id = bot._app_id
    app_secret = bot._app_secret

    print(f"{'=' * 55}")
    print(f"  Feishu Sticker Bot (WebSocket)")
    print(f"  App ID: {app_id}")
    print(f"  Mode: Long-polling (lark.ws.Client)")
    print(f"{'=' * 55}")

    def _shutdown(sig, frame):
        print("\nShutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print("Connecting to Feishu WebSocket...")
    print("Bot is ready. Send a message in Feishu to start.")
    print("Press Ctrl+C to stop.\n")

    cli = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )
    cli.start()


if __name__ == "__main__":
    main()
