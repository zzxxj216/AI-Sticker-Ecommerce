"""Feishu Blog Agent — WebSocket long-polling entry point.

Uses lark.ws.Client to maintain a persistent WebSocket connection to
Feishu servers. No webhook, no public IP, no FastAPI required.

Start:
    python feishu_blog_app.py

Prerequisites:
    1. pip install lark-oapi>=1.4.0
    2. Set FEISHU_BLOG_APP_ID and FEISHU_BLOG_APP_SECRET in .env
    3. In Feishu open platform -> App Features -> Bot: enable
    4. In Feishu open platform -> Event Subscription:
       - Choose "Use long-polling to receive events"
       - Subscribe to: im.message.receive_v1
    5. Grant bot permissions: im:message, im:message:send_as_bot, im:resource
"""

import os
import signal
import sys

from dotenv import load_dotenv

load_dotenv(override=True)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

import lark_oapi as lark

from src.services.feishu.blog_bot import FeishuBlogBot


def main():
    app_id = os.getenv("FEISHU_BLOG_APP_ID", "")
    app_secret = os.getenv("FEISHU_BLOG_APP_SECRET", "")

    if not app_id or not app_secret:
        print("Error: FEISHU_BLOG_APP_ID and FEISHU_BLOG_APP_SECRET must be set in .env")
        sys.exit(1)

    print("=" * 55)
    print("  Feishu Blog Agent (WebSocket)")
    print(f"  App ID: {app_id}")
    print("  Mode: Long-polling (lark.ws.Client)")
    print("=" * 55)

    bot = FeishuBlogBot(app_id=app_id, app_secret=app_secret)
    event_handler = bot.build_event_handler()

    def _shutdown(sig, frame):
        print("\nShutting down...")
        bot._scheduler.shutdown()
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
