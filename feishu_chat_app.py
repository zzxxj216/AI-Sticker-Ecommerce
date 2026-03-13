"""飞书贴纸包批量生成机器人 — FastAPI webhook 服务器

用户在飞书上与 Bot 对话，AI 引导完成贴纸包配置后自动并行生成，
生成完成后将预览图发送到聊天中。

Start:
    python feishu_chat_app.py                     # 默认端口 9001
    python feishu_chat_app.py --port 8080         # 自定义端口

Prerequisites:
    1. pip install lark-oapi fastapi uvicorn anthropic google-generativeai
    2. Set FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_VERIFICATION_TOKEN in .env
    3. In Feishu open platform → Event Subscription → set request URL to:
       http://<your-host>:9001/feishu/webhook
    4. Subscribe to event: im.message.receive_v1
    5. Grant bot permissions: im:message, im:message:send_as_bot, im:resource
"""

import json
import argparse
import threading

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
import lark_oapi as lark

from src.services.feishu.bot import FeishuBot

app = FastAPI(title="Feishu Sticker Bot")
bot: FeishuBot = None  # type: ignore
event_handler = None


@app.on_event("startup")
def startup():
    global bot, event_handler
    bot = FeishuBot(auto_generate=True)
    event_handler = bot.build_event_handler()


@app.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """Receive Feishu event callbacks."""
    body = await request.body()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}

    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge", "")
        print(f"[Feishu] URL verification — challenge: {challenge[:10]}...")
        return JSONResponse({"challenge": challenge})

    header = payload.get("header", {})
    event_type = header.get("event_type", "")

    if event_type:
        if event_type == "im.message.receive_v1":
            event = payload.get("event", {})
            threading.Thread(
                target=_handle_event, args=(event,), daemon=True
            ).start()
        else:
            print(f"[Feishu] Ignoring event type: {event_type}")
        return JSONResponse({"code": 0, "msg": "ok"})

    headers = dict(request.headers)
    req = lark.RawRequest()
    req.uri = request.url.path
    req.body = body
    req.headers = headers

    try:
        resp = event_handler.do(req)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=resp.headers,
        )
    except Exception as e:
        print(f"[Feishu] Event handler error: {e}")
        return JSONResponse({"code": 0, "msg": "ok"})


def _handle_event(event: dict):
    """Process a message event in a background thread."""
    try:
        message = event.get("message", {})
        sender = event.get("sender", {})
        event_data = {
            "message": {
                "message_id": message.get("message_id", ""),
                "chat_id": message.get("chat_id", ""),
                "message_type": message.get("message_type", ""),
                "content": message.get("content", "{}"),
            },
            "sender": {
                "sender_id": {
                    "open_id": sender.get("sender_id", {}).get("open_id", "unknown"),
                },
            },
        }
        bot.handle_message(event_data)
    except Exception as e:
        print(f"[Feishu] Message handling error: {e}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "feishu-sticker-bot"}


def main():
    parser = argparse.ArgumentParser(description="Feishu Sticker Bot")
    parser.add_argument("--port", type=int, default=9001)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    print(f"{'=' * 55}")
    print(f"  Feishu Sticker Bot (BatchPipeline)")
    print(f"  Webhook URL: http://{args.host}:{args.port}/feishu/webhook")
    print(f"  Health:      http://{args.host}:{args.port}/health")
    print(f"  API Docs:    http://{args.host}:{args.port}/docs")
    print(f"{'=' * 55}")

    uvicorn.run(
        "feishu_chat_app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
