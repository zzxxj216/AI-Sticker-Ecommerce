"""Web-based Sticker Agent — AI agent with tool calling for sticker pack generation.

Ported from the Feishu Sticker Bot, adapted for HTTP/SSE web interface.
The agent guides users through sticker pack design, then triggers generation
via the StickerPackPipeline.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from datetime import datetime, timezone, timedelta

_CN_TZ = timezone(timedelta(hours=8))
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.logger import get_logger
from src.services.ai.openai_service import OpenAIService

logger = get_logger("tools.sticker_agent")

# ── Tool Definitions ────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "generate_sticker_pack",
        "description": (
            "Generate a sticker pack with the given theme and style. "
            "Runs the full pipeline: prompt design → image generation → QC. "
            "Returns immediately with a task_id; progress streams via SSE."
        ),
        "parameters": {
            "theme": {"type": "string", "description": "Core theme, e.g. 'Cute Cat Emotions'"},
            "style": {"type": "string", "default": "", "description": "Visual style: cute, minimalist, retro, cyberpunk, watercolor, etc."},
            "color_mood": {"type": "string", "default": "", "description": "Color palette & mood: warm pastel, dark cyberpunk, etc."},
            "extras": {"type": "string", "default": "", "description": "Additional requirements: text, elements, use case"},
        },
        "required": ["theme"],
    },
    {
        "name": "get_task_status",
        "description": "Check the status of a sticker generation task.",
        "parameters": {
            "task_id": {"type": "string", "description": "Task ID or 'latest'"},
        },
        "required": ["task_id"],
    },
    {
        "name": "list_recent_tasks",
        "description": "List recently generated sticker tasks for this session.",
        "parameters": {
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    },
    {
        "name": "list_sticker_gallery",
        "description": "List completed sticker packs from the gallery database.",
        "parameters": {
            "limit": {"type": "integer", "default": 10},
        },
        "required": [],
    },
    {
        "name": "download_pack",
        "description": "Get the download URL for a sticker pack ZIP.",
        "parameters": {
            "task_id": {"type": "string", "description": "Task ID or 'latest'"},
        },
        "required": ["task_id"],
    },
]


# ── Session & Task State ────────────────────────────────────

class TaskState:
    __slots__ = (
        "task_id", "session_id", "theme", "status",
        "created_at", "image_count", "image_urls", "output_dir", "error",
    )

    def __init__(self, task_id: str, session_id: str, theme: str):
        self.task_id = task_id
        self.session_id = session_id
        self.theme = theme
        self.status = "pending"
        self.created_at = datetime.now()
        self.image_count: int = 0
        self.image_urls: list = []
        self.output_dir: str = ""
        self.error: Optional[str] = None


class SessionMemory:
    """Thread-safe per-session conversation + task memory."""

    def __init__(self):
        self._lock = threading.Lock()
        self._histories: Dict[str, list] = {}
        self._tasks: Dict[str, TaskState] = {}
        self._session_tasks: Dict[str, list] = {}

    def add_message(self, session_id: str, role: str, content: str):
        with self._lock:
            if session_id not in self._histories:
                self._histories[session_id] = []
            self._histories[session_id].append({"role": role, "content": content})
            if len(self._histories[session_id]) > 40:
                self._histories[session_id] = self._histories[session_id][-30:]

    def get_history(self, session_id: str) -> list:
        with self._lock:
            return list(self._histories.get(session_id, []))

    def reset(self, session_id: str):
        with self._lock:
            self._histories.pop(session_id, None)

    def create_task(self, session_id: str, theme: str) -> TaskState:
        with self._lock:
            task_id = f"stk_{uuid.uuid4().hex[:12]}"
            task = TaskState(task_id, session_id, theme)
            self._tasks[task_id] = task
            self._session_tasks.setdefault(session_id, []).append(task_id)
            return task

    def get_task(self, task_id: str) -> Optional[TaskState]:
        with self._lock:
            return self._tasks.get(task_id)

    def resolve_task_id(self, session_id: str, raw: str) -> Optional[str]:
        with self._lock:
            if raw.lower() == "latest":
                ids = self._session_tasks.get(session_id, [])
                return ids[-1] if ids else None
            return raw if raw in self._tasks else None

    def get_session_tasks(self, session_id: str, limit: int = 5) -> list[TaskState]:
        with self._lock:
            ids = self._session_tasks.get(session_id, [])
            return [self._tasks[tid] for tid in reversed(ids[-limit:]) if tid in self._tasks]


# ── Sticker Agent Service ───────────────────────────────────

class StickerAgentService:
    """Agent for sticker pack generation using OpenAI function calling."""

    AGENT_TYPE = "sticker"
    MAX_STEPS = 5

    def __init__(self):
        self._memory = SessionMemory()
        self._task_queues: Dict[str, queue.Queue] = {}
        self._openai = OpenAIService()
        self._gemini = None
        self._openai_tools = self._build_openai_tools()

    def _get_gemini(self):
        if self._gemini is None:
            from src.services.ai.gemini_service import GeminiService
            self._gemini = GeminiService()
        return self._gemini

    def _db(self):
        from src.services.ops.db import OpsDatabase
        return OpsDatabase()

    def reset(self, session_id: str):
        self._memory.reset(session_id)

    def restore_session(self, session_id: str) -> list:
        """Load messages from DB into memory and return them for the frontend."""
        db = self._db()
        rows = db.get_chat_messages(session_id)
        if not rows:
            return []
        self._memory.reset(session_id)
        for row in rows:
            role = row["role"]
            content = row["content"]
            if role == "assistant_tool_calls":
                self._memory.add_message(session_id, "assistant_tool_calls", content)
            elif role == "tool":
                tool_name = row.get("tool_name", "")
                tool_call_id = row.get("tool_call_id", "")
                self._memory.add_message(session_id, "tool", json.dumps({
                    "call_id": tool_call_id, "name": tool_name, "content": content,
                }, ensure_ascii=False))
            else:
                self._memory.add_message(session_id, role, content)
        return rows

    def get_task_queue(self, task_id: str) -> Optional[queue.Queue]:
        return self._task_queues.get(task_id)

    def get_task(self, task_id: str) -> Optional[dict]:
        task = self._memory.get_task(task_id)
        if not task:
            return None
        return {"id": task.task_id, "status": task.status, "theme": task.theme}

    @staticmethod
    def _build_openai_tools() -> list:
        tools = []
        for td in TOOL_DEFINITIONS:
            properties = {}
            required = td.get("required", [])
            for pname, pdef in td.get("parameters", {}).items():
                prop: dict = {"type": pdef.get("type", "string")}
                if "description" in pdef:
                    prop["description"] = pdef["description"]
                if "enum" in pdef:
                    prop["enum"] = pdef["enum"]
                properties[pname] = prop
            tools.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td["description"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return tools

    def _persist(self, session_id: str, role: str, content: str, created_by: str = "system",
                  tool_name: str = "", tool_call_id: str = "") -> None:
        try:
            db = self._db()
            db.ensure_chat_session(session_id, self.AGENT_TYPE, created_by=created_by)
            db.insert_chat_message(session_id, role, content, tool_name=tool_name, tool_call_id=tool_call_id)
        except Exception as e:
            logger.warning("Persist chat message failed: %s", e)

    def process_message(self, session_id: str, message: str, created_by: str = "system") -> dict:
        """Process user message through agent loop with function calling."""
        self._memory.add_message(session_id, "user", message)
        self._persist(session_id, "user", message, created_by=created_by)
        tool_results = []
        final_reply = ""
        task_id_started = None

        SYSTEM = (
            "You are a sticker pack design assistant for 'Inkelligent', an AI sticker e-commerce brand.\n"
            "Our customers are overseas (English-speaking markets).\n"
            "Help users design and generate sticker packs.\n\n"
            "Rules:\n"
            "- Respond in the SAME language the user uses (Chinese is fine for conversation)\n"
            "- When user describes a sticker idea, help refine it: suggest theme, style, colors\n"
            "- When design is clear enough, confirm with user then call generate_sticker_pack\n"
            "- IMPORTANT: When user's intent clearly maps to a tool, call it immediately\n"
            "- Keep responses concise and friendly\n"
            "- After generation completes, remind user they can check gallery or generate more\n\n"
            "CRITICAL — Language rule for tool calls:\n"
            "- When calling generate_sticker_pack, ALL parameters (theme, style, color_mood, extras) "
            "MUST be written in English, even if the user speaks Chinese.\n"
            "- Translate and adapt the user's Chinese description into natural English.\n"
            "- Sticker images are for overseas customers — no Chinese characters allowed in any sticker content.\n"
            "- If the user's idea includes Chinese text for stickers, translate it into English equivalents.\n\n"
            "Design dimensions you can help with:\n"
            "- Theme: core topic (e.g. cute cats, coffee lover, space adventure)\n"
            "- Style: visual style (cute/kawaii, minimalist, retro, watercolor, flat, cyberpunk)\n"
            "- Color & Mood: warm pastel, dark neon, black & white, rainbow bright\n"
            "- Extras: text, specific elements, usage scenario"
        )

        messages = [{"role": "system", "content": SYSTEM}]
        for msg in self._memory.get_history(session_id):
            role = msg["role"]
            if role == "assistant_tool_calls":
                try:
                    calls = json.loads(msg["content"])
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": c["id"],
                                "type": "function",
                                "function": {"name": c["name"], "arguments": c["arguments"]},
                            }
                            for c in calls
                        ],
                    })
                except (json.JSONDecodeError, TypeError):
                    pass
            elif role == "tool":
                try:
                    td = json.loads(msg["content"])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": td.get("call_id", "call_0"),
                        "content": td.get("content", msg["content"]),
                    })
                except (json.JSONDecodeError, TypeError):
                    messages.append({"role": "assistant", "content": f"[Tool result]: {msg['content']}"})
            else:
                messages.append({"role": role, "content": msg["content"]})

        for step in range(self.MAX_STEPS):
            try:
                response = self._openai.client.chat.completions.create(
                    model=self._openai.model,
                    messages=messages,
                    tools=self._openai_tools,
                    tool_choice="auto",
                    temperature=0.4,
                )
            except Exception as e:
                logger.error("Sticker agent LLM failed step %d: %s", step, e, exc_info=True)
                final_reply = "抱歉，处理消息时出现错误，请重试。"
                break

            choice = response.choices[0]
            msg_obj = choice.message

            if msg_obj.tool_calls:
                messages.append(msg_obj)

                # Persist the assistant message that contains tool_calls
                tc_payload = json.dumps([
                    {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                    for tc in msg_obj.tool_calls
                ], ensure_ascii=False)
                self._memory.add_message(session_id, "assistant_tool_calls", tc_payload)
                self._persist(session_id, "assistant_tool_calls", tc_payload, created_by=created_by)

                for tc in msg_obj.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_params = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_params = {}

                    logger.info("Sticker tool call: %s(%s)", tool_name, json.dumps(tool_params, ensure_ascii=False)[:200])
                    result = self._execute_tool(tool_name, tool_params, session_id, created_by)

                    if isinstance(result, dict) and result.get("async"):
                        task_id_started = result["task_id"]
                        result_str = result["message"]
                    else:
                        result_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

                    tool_results.append({"tool": tool_name, "result": result_str})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    })
                    self._memory.add_message(session_id, "tool", json.dumps({
                        "call_id": tc.id, "name": tool_name, "content": result_str,
                    }, ensure_ascii=False))
                    self._persist(session_id, "tool", result_str, tool_name=tool_name, tool_call_id=tc.id)

                continue

            final_reply = msg_obj.content or ""
            break
        else:
            final_reply = final_reply or "处理步骤过多，请简化你的请求。"

        if final_reply:
            self._memory.add_message(session_id, "assistant", final_reply)
            self._persist(session_id, "assistant", final_reply)

        return {
            "reply": final_reply,
            "task_id": task_id_started,
            "tool_results": tool_results,
        }

    # ── Tool Dispatch ───────────────────────────────────────

    def _execute_tool(self, name: str, params: dict, session_id: str, created_by: str) -> Any:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"Unknown tool: {name}"
        try:
            return handler(params=params, session_id=session_id, created_by=created_by)
        except Exception as e:
            logger.error("Sticker tool %s failed: %s", name, e, exc_info=True)
            return f"Tool execution failed: {e}"

    # ── Tool: generate_sticker_pack ─────────────────────────

    def _tool_generate_sticker_pack(self, params: dict, session_id: str, created_by: str) -> dict:
        theme = params.get("theme", "").strip()
        if not theme:
            return "Error: theme is required."

        task = self._memory.create_task(session_id, theme)
        q: queue.Queue = queue.Queue()
        self._task_queues[task.task_id] = q

        thread = threading.Thread(
            target=self._run_pipeline,
            args=(task, params, q, created_by),
            daemon=True,
        )
        thread.start()

        return {
            "async": True,
            "task_id": task.task_id,
            "message": (
                f"Sticker generation started. task_id={task.task_id}, "
                f"theme='{theme}'. Progress will stream via SSE."
            ),
        }

    def _run_pipeline(self, task: TaskState, params: dict, q: queue.Queue, created_by: str) -> None:
        from src.services.batch.sticker_pipeline import StickerPackPipeline

        def on_progress(event: str, data: dict):
            q.put({"type": event, "data": data})

        try:
            pipeline = StickerPackPipeline(
                openai_service=self._openai,
                gemini_service=self._get_gemini(),
                output_dir="output/h5_jobs",
            )
            result = pipeline.run(
                theme=params.get("theme", "sticker pack"),
                user_style=params.get("style") or None,
                user_color_mood=params.get("color_mood") or None,
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

            task.status = result.status
            task.image_count = len(result.image_paths)
            task.image_urls = image_urls
            task.output_dir = str(pipeline.output_dir)

            self._register_job_in_db(task, params, result, pipeline.output_dir, created_by)

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
            logger.error("Sticker pipeline failed: %s", exc, exc_info=True)
            task.status = "failed"
            task.error = str(exc)
            q.put({"type": "error", "data": {"error": str(exc)}})
        finally:
            q.put(None)

    def _register_job_in_db(self, task: TaskState, params: dict, result, output_dir, created_by: str) -> None:
        try:
            from src.services.ops.db import OpsDatabase
            from src.services.ops.job_service import JobService
            from src.models.ops import GenerationJob

            db = OpsDatabase()
            job = GenerationJob(
                id=f"job_{uuid.uuid4().hex[:12]}",
                trend_id=f"chat:{task.session_id}",
                trend_name=params.get("theme", "Chat Sticker Pack"),
                status=result.status if result.status in {"completed", "failed"} else "completed",
                output_dir=str(output_dir),
                image_count=len(result.image_paths),
                error_message=result.error or "",
                created_by=created_by,
                created_at=datetime.now(_CN_TZ),
                started_at=datetime.now(_CN_TZ),
                finished_at=datetime.now(_CN_TZ),
                updated_at=datetime.now(_CN_TZ),
            )
            db.create_job(job)

            outputs = JobService._collect_outputs(job.id, Path(str(output_dir)))
            db.replace_outputs(job.id, outputs)
            logger.info("Registered sticker job %s with %d outputs", job.id, len(outputs))
        except Exception as exc:
            logger.warning("Failed to register sticker job in DB: %s", exc)

    # ── Tool: get_task_status ───────────────────────────────

    def _tool_get_task_status(self, params: dict, session_id: str, **_) -> str:
        raw_id = params.get("task_id", "latest")
        task_id = self._memory.resolve_task_id(session_id, raw_id)
        if not task_id:
            return "当前会话没有找到任何任务。"

        task = self._memory.get_task(task_id)
        if not task:
            return f"Task {task_id} not found."

        lines = [
            f"Task: {task.task_id}",
            f"Theme: {task.theme}",
            f"Status: {task.status}",
            f"Created: {task.created_at.strftime('%Y-%m-%d %H:%M')}",
        ]
        if task.image_count:
            lines.append(f"Images: {task.image_count}")
        if task.error:
            lines.append(f"Error: {task.error}")
        return "\n".join(lines)

    # ── Tool: list_recent_tasks ─────────────────────────────

    def _tool_list_recent_tasks(self, params: dict, session_id: str, **_) -> str:
        limit = params.get("limit", 5)
        tasks = self._memory.get_session_tasks(session_id, limit=limit)
        if not tasks:
            return "当前会话没有生成过卡贴包。"

        lines = []
        for t in tasks:
            lines.append(f"[{t.status}] {t.task_id}: {t.theme} ({t.created_at.strftime('%m-%d %H:%M')})")
            if t.image_count:
                lines.append(f"  Images: {t.image_count}")
        return "\n".join(lines)

    # ── Tool: list_sticker_gallery ──────────────────────────

    def _tool_list_sticker_gallery(self, params: dict, **_) -> str:
        limit = params.get("limit", 10)
        try:
            from src.services.ops.db import OpsDatabase
            db = OpsDatabase()
            jobs = db.list_completed_jobs_with_images()[:limit]
            if not jobs:
                return "卡贴画廊暂无数据。"

            lines = [f"卡贴画廊共有 {len(jobs)} 个已完成卡包：\n"]
            for j in jobs:
                name = j.get("trend_name", "Unknown")
                count = j.get("image_count", 0)
                source = "AI 对话" if j.get("source_type") == "chat" else "趋势"
                lines.append(f"  {j['id']}: {name} ({count} 张, 来源: {source})")
            return "\n".join(lines)
        except Exception as e:
            return f"查询画廊失败: {e}"

    # ── Tool: download_pack ─────────────────────────────────

    def _tool_download_pack(self, params: dict, session_id: str, **_) -> str:
        raw_id = params.get("task_id", "latest")
        task_id = self._memory.resolve_task_id(session_id, raw_id)
        if not task_id:
            return "当前会话没有找到任何任务。"

        task = self._memory.get_task(task_id)
        if not task or task.status != "completed":
            return "任务未完成或不存在，无法下载。"

        return (
            f"卡贴包已就绪！\n"
            f"主题: {task.theme}\n"
            f"图片数: {task.image_count}\n"
            f"你可以在「卡贴画廊」或「卡包管理」页面下载 ZIP 文件。\n"
            f"卡贴画廊: /stickers\n"
            f"卡包管理: /packs"
        )
