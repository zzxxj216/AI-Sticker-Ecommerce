"""Chat-based sticker pack generation service.

Combines multi-turn conversation (via ChatService/Claude) with the
StickerPackPipeline to let users design and generate sticker packs
through natural language dialogue.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from src.core.logger import get_logger
from src.services.ai.chat_service import ChatService
from src.services.ai.claude_service import ClaudeService
from src.services.ai.gemini_service import GeminiService
from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_pipeline import StickerPackPipeline

logger = get_logger("tools.chat_sticker")

STICKER_CHAT_SYSTEM = """\
你是一位专业的卡贴包设计顾问，帮助用户设计和规划卡贴包（sticker pack）。

你的职责：
1. 了解用户想要的卡贴主题、风格、色彩和情绪
2. 给出专业的设计建议，帮助用户完善创意
3. 当信息足够时，总结设计方案供用户确认

沟通风格：
- 友好、专业、简洁
- 主动提问引导用户完善需求
- 每次回复末尾用一句话总结当前进展

你可以帮助的设计维度：
- 主题（Theme）：卡贴包的核心主题
- 风格（Style）：可爱、极简、复古、赛博朋克、水彩等
- 色彩情绪（Color & Mood）：暖色活泼、冷色沉稳、黑白极简等
- 特殊要求：文字内容、特定元素、使用场景等

注意：你只负责设计咨询对话，实际的生成动作由系统的「生成卡贴」按钮触发。\
"""

EXTRACT_SYSTEM = """\
You are a parameter extraction assistant. Given a conversation between a user and a sticker design consultant, \
extract the sticker pack design parameters as a JSON object.

Output ONLY a valid JSON object with these fields:
{
  "theme": "the core theme of the sticker pack (required, in English)",
  "style": "visual style description (optional, e.g. cute, minimalist, retro)",
  "color_mood": "color palette and mood (optional, e.g. warm pastel, dark cyberpunk)",
  "extras": "any additional requirements or special instructions (optional)",
  "summary_zh": "一句话中文总结这个卡贴包的设计方案"
}

If the conversation lacks sufficient information for a theme, set theme to an empty string.\
"""


class ChatStickerService:
    """Manages chat sessions and sticker generation tasks."""

    def __init__(self):
        self._chat = ChatService(
            claude_service=ClaudeService(),
            system_prompt=STICKER_CHAT_SYSTEM,
            max_tokens=2048,
            temperature=0.7,
        )
        self._tasks: Dict[str, dict] = {}
        self._task_queues: Dict[str, queue.Queue] = {}

    def chat(self, session_id: str, message: str) -> str:
        return self._chat.chat(session_id, message)

    def reset(self, session_id: str) -> None:
        self._chat.reset(session_id)

    def get_history(self, session_id: str) -> list:
        session = self._chat._sessions.get(session_id)
        if not session:
            return []
        return list(session.history)

    def extract_params(self, session_id: str) -> dict:
        """Extract structured sticker parameters from conversation history."""
        history = self.get_history(session_id)
        if not history:
            return {"theme": "", "style": "", "color_mood": "", "extras": ""}

        conversation_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
            for m in history[-20:]
        )

        try:
            openai = OpenAIService()
            result = openai.generate_json(
                prompt=f"Conversation:\n{conversation_text}\n\nExtract the sticker pack parameters:",
                system=EXTRACT_SYSTEM,
                temperature=0.2,
            )
            if isinstance(result, dict):
                return result
        except Exception as e:
            logger.warning("Parameter extraction failed: %s", e)

        return {"theme": "", "style": "", "color_mood": "", "extras": ""}

    def start_generation(
        self,
        session_id: str,
        params: Optional[dict] = None,
        created_by: str = "system",
    ) -> str:
        """Start sticker generation in background. Returns task_id."""
        if params is None:
            params = self.extract_params(session_id)

        theme = params.get("theme", "").strip()
        if not theme:
            raise ValueError("无法从对话中提取有效的卡贴主题，请继续描述你的需求。")

        task_id = f"stk_{uuid.uuid4().hex[:12]}"
        q: queue.Queue = queue.Queue()
        self._task_queues[task_id] = q
        self._tasks[task_id] = {
            "id": task_id,
            "session_id": session_id,
            "params": params,
            "status": "running",
            "created_by": created_by,
            "created_at": datetime.now().isoformat(),
        }

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(task_id, params),
            daemon=True,
        )
        thread.start()
        return task_id

    def get_task_queue(self, task_id: str) -> Optional[queue.Queue]:
        return self._task_queues.get(task_id)

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def _run_pipeline(self, task_id: str, params: dict) -> None:
        q = self._task_queues[task_id]

        def on_progress(event: str, data: dict):
            q.put({"type": event, "data": data})

        try:
            openai_service = OpenAIService()
            gemini_service = GeminiService()
            pipeline = StickerPackPipeline(
                openai_service=openai_service,
                gemini_service=gemini_service,
                output_dir="output/h5_jobs",
            )
            result = pipeline.run(
                theme=params.get("theme", "sticker pack"),
                user_style=params.get("style"),
                user_color_mood=params.get("color_mood"),
                user_extra=params.get("extras", ""),
                on_progress=on_progress,
            )

            image_urls = []
            for p in result.image_paths:
                p_str = p.replace("\\", "/")
                marker = "output/h5_jobs/"
                idx = p_str.find(marker)
                if idx >= 0:
                    image_urls.append("/outputs/" + p_str[idx + len(marker):])
                else:
                    image_urls.append(p_str)

            self._tasks[task_id]["status"] = result.status
            self._tasks[task_id]["image_count"] = len(result.image_paths)
            self._tasks[task_id]["image_urls"] = image_urls
            self._tasks[task_id]["output_dir"] = str(pipeline.output_dir)

            self._register_job_in_db(task_id, params, result, pipeline.output_dir)

            q.put({
                "type": "complete",
                "data": {
                    "status": result.status,
                    "image_count": len(result.image_paths),
                    "image_urls": image_urls,
                    "duration": result.duration_seconds,
                    "error": result.error,
                },
            })
        except Exception as exc:
            logger.error("Chat sticker generation failed: %s", exc, exc_info=True)
            self._tasks[task_id]["status"] = "failed"
            q.put({
                "type": "error",
                "data": {"error": str(exc)},
            })
        finally:
            q.put(None)  # sentinel

    def _register_job_in_db(self, task_id: str, params: dict, result, output_dir) -> None:
        """Persist the completed chat sticker job into generation_jobs/outputs."""
        try:
            from src.services.ops.db import OpsDatabase
            from src.services.ops.job_service import JobService
            from src.models.ops import GenerationJob

            task = self._tasks.get(task_id, {})
            session_id = task.get("session_id", "unknown")
            created_by = task.get("created_by", "system")

            db = OpsDatabase()
            job = GenerationJob(
                id=f"job_{uuid.uuid4().hex[:12]}",
                trend_id=f"chat:{session_id}",
                trend_name=params.get("theme", "Chat Sticker Pack"),
                status=result.status if result.status in {"completed", "failed"} else "completed",
                output_dir=str(output_dir),
                image_count=len(result.image_paths),
                error_message=result.error or "",
                created_by=created_by,
                created_at=datetime.utcnow(),
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.create_job(job)

            outputs = JobService._collect_outputs(job.id, Path(str(output_dir)))
            db.replace_outputs(job.id, outputs)
            logger.info("Registered chat sticker job %s with %d outputs", job.id, len(outputs))
        except Exception as exc:
            logger.warning("Failed to register chat sticker job in DB: %s", exc)
