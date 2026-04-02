"""Web-based Blog Agent — ReAct-style AI agent with tool calling.

Ported from the Feishu Blog Bot agent, adapted for HTTP/SSE web interface.
The agent receives user messages, maintains conversation memory per session,
and decides whether to respond directly or call tools using an LLM-driven
reasoning loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.logger import get_logger
from src.services.ai.openai_service import OpenAIService

logger = get_logger("tools.blog_agent")

# ── Tool Definitions ────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "generate_blog",
        "description": (
            "Generate a full SEO blog post about a sticker topic. "
            "Runs the Planner → Writer → Reviewer → Image Generation pipeline. "
            "Returns immediately with a task_id; progress is sent via SSE."
        ),
        "parameters": {
            "topic": {"type": "string", "description": "Blog topic, e.g. 'Cowboy Western Stickers'"},
            "keywords": {"type": "array", "items": {"type": "string"}, "description": "SEO keywords list"},
            "language": {"type": "string", "default": "en", "description": "Language: en or zh"},
            "additional_instructions": {"type": "string", "default": "", "description": "Extra writing instructions"},
        },
        "required": ["topic", "keywords"],
    },
    {
        "name": "revise_article",
        "description": (
            "Revise a previously generated blog article based on user instructions. "
            "Uses the Writer LLM to apply requested changes and updates the draft."
        ),
        "parameters": {
            "task_id": {"type": "string", "default": "latest", "description": "Task ID or 'latest'"},
            "instructions": {"type": "string", "description": "What to change, e.g. '标题改为XX', '第三段加更多细节'"},
        },
        "required": ["instructions"],
    },
    {
        "name": "regenerate_image",
        "description": (
            "Regenerate a specific image from a previously generated blog. "
            "Generates a new image with optional modifications to the prompt."
        ),
        "parameters": {
            "task_id": {"type": "string", "default": "latest", "description": "Task ID or 'latest'"},
            "image_index": {"type": "integer", "description": "Which image to regenerate (1-based)"},
            "new_instructions": {"type": "string", "default": "", "description": "Optional prompt modification"},
        },
        "required": ["image_index"],
    },
    {
        "name": "get_task_status",
        "description": "Check the status of a blog generation task.",
        "parameters": {
            "task_id": {"type": "string", "description": "Task ID or 'latest'"},
        },
        "required": ["task_id"],
    },
    {
        "name": "cancel_task",
        "description": "Cancel a running blog generation task.",
        "parameters": {
            "task_id": {"type": "string", "description": "Task ID or 'latest'"},
        },
        "required": ["task_id"],
    },
    {
        "name": "list_recent_blogs",
        "description": "List recently generated blog tasks for this session.",
        "parameters": {
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    },
    {
        "name": "list_local_blogs",
        "description": (
            "List blog articles saved locally on disk (output/blogs/*.md). "
            "Includes articles not yet published to Shopify."
        ),
        "parameters": {
            "limit": {"type": "integer", "default": 10},
        },
        "required": [],
    },
    {
        "name": "load_local_blog",
        "description": (
            "Load a locally saved blog markdown file into the current session "
            "so it can be modified or uploaded."
        ),
        "parameters": {
            "filename": {"type": "string", "description": "Markdown filename from list_local_blogs"},
        },
        "required": ["filename"],
    },
    {
        "name": "publish_to_shopify",
        "description": (
            "Upload or publish a blog article to Shopify. "
            "Can publish as draft or immediately make it live."
        ),
        "parameters": {
            "task_id": {"type": "string", "default": "latest", "description": "Task ID or 'latest'"},
            "published": {"type": "boolean", "default": False, "description": "If true, publish live. If false, save as draft."},
        },
        "required": [],
    },
    {
        "name": "list_shopify_articles",
        "description": "List articles on Shopify, optionally filtered by status.",
        "parameters": {
            "status": {"type": "string", "enum": ["all", "published", "draft"], "default": "all"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": [],
    },
    {
        "name": "view_shopify_article",
        "description": "View the full content of a specific Shopify article by ID.",
        "parameters": {
            "article_id": {"type": "string", "description": "Shopify article ID or 'latest'"},
        },
        "required": ["article_id"],
    },
]

AGENT_SYSTEM_PROMPT = """You are a blog management assistant for "Inkelligent", an AI-generated sticker e-commerce brand on Shopify.

## Your Capabilities
You help users generate, revise, publish, and manage SEO blog posts about sticker topics.
You have access to tools that can:
- Generate full blog posts (with AI planning, writing, image generation)
- Revise a previously generated article based on user instructions
- Regenerate specific images from a blog post
- Check task status, cancel tasks, and list recent blogs
- Upload/publish articles to Shopify
- List and view articles on Shopify
- List and load previously generated blogs from local disk

## Conversation Rules
1. Respond in the SAME language the user uses (Chinese → Chinese, English → English)
2. When the user provides a topic, suggest 3-5 SEO keywords and ask for confirmation before generating
3. When the user says "latest" or "刚才那篇", resolve it to the most recent task
4. Keep responses concise and informative
5. Always confirm the user's intent before executing destructive actions
6. When the user asks to modify/revise (e.g. "标题改一下", "第三段加点内容"), use revise_article
7. When the user asks to regenerate an image (e.g. "第3张图重新生成"), use regenerate_image
8. When the user asks to publish (e.g. "发布到Shopify", "上传到草稿箱"), use publish_to_shopify
9. When the user refers to a previously generated article, use list_local_blogs then load_local_blog
10. After generation completes, remind the user they can request modifications

## Response Format
You MUST respond with a JSON object in one of these two formats:

### Format 1: Call a tool
```json
{{"action": "tool_call", "tool": "<tool_name>", "params": {{<parameters>}}, "thinking": "<brief reasoning>"}}
```

### Format 2: Respond to user
```json
{{"action": "respond", "message": "<your message to the user>", "thinking": "<brief reasoning>"}}
```

## Available Tools
{tools_json}

## Critical Rules
- ALWAYS return valid JSON, nothing else. No markdown fences, no explanation outside JSON.
- One action per response.
- After a tool returns its result, you'll see it as a [Tool Result] message and must decide next.
- For generate_blog: pipeline runs in background; confirm it started.
- IMPORTANT: When the user's intent clearly maps to a tool, ALWAYS call the tool immediately.
  Do NOT describe what you could do — just DO it.
  Examples:
  - "列出本地博客" → call list_local_blogs
  - "帮我写一篇关于猫咪贴纸的博客" → suggest keywords, then call generate_blog
  - "修改标题" → call revise_article
  - "发布到Shopify" → call publish_to_shopify
  - "查看最近的任务" → call list_recent_blogs
- Only use "respond" when you genuinely need more information or to confirm parameters.
"""


# ── Session Memory ──────────────────────────────────────────

class TaskState:
    __slots__ = (
        "task_id", "session_id", "topic", "keywords", "status",
        "created_at", "draft_content", "draft_meta", "image_records",
        "images_dir", "markdown_path", "shopify_article_id", "result", "error",
    )

    def __init__(self, task_id: str, session_id: str, topic: str, keywords: list):
        self.task_id = task_id
        self.session_id = session_id
        self.topic = topic
        self.keywords = keywords
        self.status = "pending"
        self.created_at = datetime.now()
        self.draft_content: str = ""
        self.draft_meta: dict = {}
        self.image_records: list = []
        self.images_dir: Optional[str] = None
        self.markdown_path: Optional[str] = None
        self.shopify_article_id: Optional[str] = None
        self.result: Optional[dict] = None
        self.error: Optional[str] = None


class SessionMemory:
    """Lightweight per-session conversation + task memory."""

    def __init__(self):
        self._histories: Dict[str, list] = {}
        self._tasks: Dict[str, TaskState] = {}
        self._session_tasks: Dict[str, list] = {}

    def add_message(self, session_id: str, role: str, content: str):
        if session_id not in self._histories:
            self._histories[session_id] = []
        self._histories[session_id].append({"role": role, "content": content})
        if len(self._histories[session_id]) > 50:
            self._histories[session_id] = self._histories[session_id][-40:]

    def get_history(self, session_id: str) -> list:
        return self._histories.get(session_id, [])

    def reset(self, session_id: str):
        self._histories.pop(session_id, None)

    def create_task(self, session_id: str, topic: str, keywords: list) -> TaskState:
        task_id = f"blog_{uuid.uuid4().hex[:12]}"
        task = TaskState(task_id, session_id, topic, keywords)
        self._tasks[task_id] = task
        self._session_tasks.setdefault(session_id, []).append(task_id)
        return task

    def get_task(self, task_id: str) -> Optional[TaskState]:
        return self._tasks.get(task_id)

    def resolve_task_id(self, session_id: str, raw: str) -> Optional[str]:
        if raw.lower() == "latest":
            ids = self._session_tasks.get(session_id, [])
            return ids[-1] if ids else None
        return raw if raw in self._tasks else None

    def get_session_tasks(self, session_id: str, limit: int = 5) -> list[TaskState]:
        ids = self._session_tasks.get(session_id, [])
        return [self._tasks[tid] for tid in reversed(ids[-limit:]) if tid in self._tasks]


# ── Blog Agent Service ──────────────────────────────────────

class BlogAgentService:
    """ReAct agent that processes user messages with tool calling, adapted for web."""

    AGENT_TYPE = "blog"
    MAX_STEPS = 5

    def __init__(self):
        self._memory = SessionMemory()
        self._task_queues: Dict[str, queue.Queue] = {}
        self._cancel_flags: Dict[str, threading.Event] = {}
        self._system_prompt = AGENT_SYSTEM_PROMPT.replace(
            "{tools_json}", json.dumps(TOOL_DEFINITIONS, indent=2, ensure_ascii=False)
        )
        self._openai = OpenAIService()
        self._openai_tools = self._build_openai_tools()

    def _db(self):
        from src.services.ops.db import OpsDatabase
        return OpsDatabase()

    def _persist(self, session_id: str, role: str, content: str, created_by: str = "system",
                  tool_name: str = "", tool_call_id: str = "") -> None:
        try:
            db = self._db()
            db.ensure_chat_session(session_id, self.AGENT_TYPE, created_by=created_by)
            db.insert_chat_message(session_id, role, content, tool_name=tool_name, tool_call_id=tool_call_id)
        except Exception as e:
            logger.warning("Persist chat message failed: %s", e)

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
            if role == "tool":
                tool_name = row.get("tool_name", "")
                tool_call_id = row.get("tool_call_id", "")
                self._memory.add_message(session_id, "tool", json.dumps({
                    "call_id": tool_call_id, "name": tool_name, "content": content,
                }, ensure_ascii=False))
            else:
                self._memory.add_message(session_id, role, content)
        return rows

    def get_history(self, session_id: str) -> list:
        return self._memory.get_history(session_id)

    def get_task_queue(self, task_id: str) -> Optional[queue.Queue]:
        return self._task_queues.get(task_id)

    def get_task(self, task_id: str) -> Optional[dict]:
        task = self._memory.get_task(task_id)
        if not task:
            return None
        return {
            "id": task.task_id, "status": task.status,
            "topic": task.topic, "result": task.result,
        }

    @staticmethod
    def _build_openai_tools() -> list:
        """Convert TOOL_DEFINITIONS to OpenAI function calling format."""
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
                if pdef.get("type") == "array" and "items" in pdef:
                    prop["items"] = pdef["items"]
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

    def process_message(self, session_id: str, message: str, created_by: str = "system") -> dict:
        """Process a user message through the agent loop using OpenAI function calling.

        Returns dict with keys:
          - reply: str (text to show user)
          - task_id: Optional[str] (if async tool was triggered)
          - tool_results: list[dict] (intermediate tool results for UI)
        """
        self._memory.add_message(session_id, "user", message)
        self._persist(session_id, "user", message, created_by=created_by)
        tool_results = []
        final_reply = ""
        task_id_started = None

        AGENT_SYSTEM = (
            "You are a blog management assistant for 'Inkelligent', an AI-generated sticker e-commerce brand.\n"
            "Help users generate, revise, publish, and manage SEO blog posts.\n"
            "Rules:\n"
            "- Respond in the SAME language the user uses\n"
            "- When the user provides a blog topic, suggest 3-5 SEO keywords and confirm before generating\n"
            "- When user intent clearly maps to a tool, call it immediately\n"
            "- After generation completes, remind user they can revise, regenerate images, or publish\n"
            "- Keep responses concise"
        )

        messages = [{"role": "system", "content": AGENT_SYSTEM}]
        for msg in self._memory.get_history(session_id):
            role = msg["role"]
            if role == "tool":
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
                    max_tokens=2048,
                    temperature=0.3,
                )
            except Exception as e:
                logger.error("Agent LLM call failed at step %d: %s", step, e, exc_info=True)
                final_reply = "抱歉，处理消息时出现错误，请重试。"
                break

            choice = response.choices[0]
            msg_obj = choice.message

            if msg_obj.tool_calls:
                messages.append(msg_obj)

                for tc in msg_obj.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_params = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_params = {}

                    logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_params, ensure_ascii=False)[:200])

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

                if task_id_started:
                    continue
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
            logger.error("Tool %s failed: %s", name, e, exc_info=True)
            return f"Tool execution failed: {e}"

    # ── Tool: generate_blog ─────────────────────────────────

    def _tool_generate_blog(self, params: dict, session_id: str, created_by: str) -> dict:
        topic = params.get("topic", "")
        keywords = params.get("keywords", [])
        if not topic or not keywords:
            return "Error: topic and keywords are required."

        task = self._memory.create_task(session_id, topic, keywords)
        q: queue.Queue = queue.Queue()
        self._task_queues[task.task_id] = q
        cancel_event = threading.Event()
        self._cancel_flags[task.task_id] = cancel_event

        thread = threading.Thread(
            target=self._run_blog_pipeline,
            args=(task, params, q, cancel_event, created_by),
            daemon=True,
        )
        thread.start()

        return {
            "async": True,
            "task_id": task.task_id,
            "message": (
                f"Blog generation started. task_id={task.task_id}, "
                f"topic='{topic}', keywords={keywords}. "
                f"Progress will stream via SSE."
            ),
        }

    def _run_blog_pipeline(
        self, task: TaskState, params: dict,
        q: queue.Queue, cancel_event: threading.Event,
        created_by: str,
    ) -> None:
        try:
            q.put({"type": "progress", "data": {"step": "init", "message": "正在初始化博客生成管线..."}})

            from src.models.blog import BusinessProfile, BlogInput, BlogDraft, ReviewResult
            from src.services.blog import WriterAgent, ReviewerAgent, BlogOrchestrator
            from src.services.blog.planner_agent import PlannerAgent
            from src.services.blog.plan_reviewer_agent import PlanReviewerAgent
            from src.services.blog.blog_image_generator import BlogImageGenerator
            from src.services.ai.gemini_service import GeminiService

            gemini_text_model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")

            def _make_llm(timeout: int):
                return GeminiService(timeout=timeout, model=gemini_text_model)

            profile = BusinessProfile.from_yaml()

            q.put({"type": "progress", "data": {"step": "agents", "message": "正在创建 AI Agent..."}})

            writer = WriterAgent(_make_llm(600), profile)
            reviewer = ReviewerAgent(_make_llm(300), profile)
            planner = PlannerAgent(_make_llm(300), profile)
            plan_reviewer = PlanReviewerAgent(_make_llm(300), profile)

            image_generator = BlogImageGenerator(GeminiService())

            blog_input = BlogInput(
                topic=params["topic"],
                seo_keywords=params.get("keywords", []),
                additional_instructions=params.get("additional_instructions", ""),
                language=params.get("language", "en"),
            )

            def on_review(draft: BlogDraft, review: ReviewResult, iteration: int) -> str:
                if cancel_event.is_set():
                    return "abort"
                q.put({"type": "review", "data": {
                    "iteration": iteration, "score": review.overall_score,
                    "passed": review.passed, "summary": review.summary,
                }})
                return "accept" if review.passed else "auto_revise"

            def progress_cb(msg: str):
                q.put({"type": "progress", "data": {"message": msg}})

            def on_content_ready(event_type: str, data):
                if event_type == "plan_ready":
                    task.draft_meta["content_plan"] = data.model_dump() if hasattr(data, "model_dump") else {}
                    q.put({"type": "plan_ready", "data": {"message": "文章规划完成"}})
                elif event_type == "draft_ready":
                    q.put({"type": "draft_ready", "data": {"message": "初稿完成"}})
                elif event_type == "finalized":
                    draft = data.get("draft")
                    if draft:
                        task.draft_content = draft.content
                        task.draft_meta = {
                            "meta_title": draft.meta_title,
                            "meta_description": draft.meta_description,
                            "url_slug": draft.url_slug,
                        }
                    task.image_records = data.get("image_records", [])
                    task.images_dir = data.get("images_dir")
                    task.markdown_path = data.get("markdown_path")

            store_domain = profile.platform.get("domain", "") or os.getenv("SHOPIFY_STORE_DOMAIN", "")
            store_url = f"https://{store_domain}" if store_domain else "https://your-store.myshopify.com"

            orchestrator = BlogOrchestrator(
                writer=writer, reviewer=reviewer,
                planner=planner, plan_reviewer=plan_reviewer,
                max_iterations=3, pass_threshold=75.0,
                image_generator=image_generator, store_url=store_url,
            )

            q.put({"type": "progress", "data": {"step": "generating", "message": "开始生成博客文章..."}})

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                final_draft = loop.run_until_complete(
                    orchestrator.run(
                        blog_input, on_review=on_review,
                        progress_callback=progress_cb,
                        on_content_ready=on_content_ready,
                    )
                )
            finally:
                loop.close()

            if cancel_event.is_set():
                task.status = "cancelled"
                q.put({"type": "error", "data": {"error": "任务已取消"}})
                return

            if final_draft:
                task.status = "completed"
                task.draft_content = final_draft.content
                task.draft_meta = {
                    "meta_title": final_draft.meta_title,
                    "meta_description": final_draft.meta_description,
                    "url_slug": final_draft.url_slug,
                }
                task.result = {
                    "title": final_draft.meta_title,
                    "description": final_draft.meta_description,
                    "slug": final_draft.url_slug,
                    "content": final_draft.content,
                    "iteration": final_draft.iteration,
                }

                self._save_blog_to_db(task, params, final_draft, created_by)

                q.put({"type": "complete", "data": {
                    "status": "completed",
                    "title": final_draft.meta_title,
                    "description": final_draft.meta_description,
                    "slug": final_draft.url_slug,
                    "content": final_draft.content,
                    "iteration": final_draft.iteration,
                }})
            else:
                task.status = "failed"
                q.put({"type": "error", "data": {"error": "博客生成未产出最终稿件"}})

        except Exception as exc:
            logger.error("Blog pipeline failed: %s", exc, exc_info=True)
            task.status = "failed"
            task.error = str(exc)
            q.put({"type": "error", "data": {"error": str(exc)}})
        finally:
            self._cancel_flags.pop(task.task_id, None)
            q.put(None)

    def _save_blog_to_db(self, task: TaskState, params: dict, draft, created_by: str) -> None:
        try:
            from src.services.ops.db import OpsDatabase
            db = OpsDatabase()
            db.insert_blog_draft({
                "id": task.task_id,
                "session_id": task.session_id,
                "topic": params.get("topic", ""),
                "seo_keywords": params.get("keywords", []),
                "meta_title": draft.meta_title,
                "meta_description": draft.meta_description,
                "url_slug": draft.url_slug,
                "content": draft.content,
                "language": params.get("language", "en"),
                "status": "completed",
                "publish_status": "unpublished",
                "iteration": getattr(draft, "iteration", 0),
                "created_by": created_by,
            })
            logger.info("Saved blog draft %s to DB", task.task_id)
        except Exception as exc:
            logger.warning("Failed to save blog draft to DB: %s", exc)

    # ── Tool: revise_article ────────────────────────────────

    def _tool_revise_article(self, params: dict, session_id: str, **_) -> str:
        raw_id = params.get("task_id", "latest")
        instructions = params.get("instructions", "")
        if not instructions:
            return "Error: instructions are required."

        task_id = self._memory.resolve_task_id(session_id, raw_id)
        if not task_id:
            return "未找到可修改的文章任务。请先生成一篇博客或使用 load_local_blog 加载。"

        task = self._memory.get_task(task_id)
        if not task or not task.draft_content:
            return "该任务没有保存的草稿内容，无法修改。"

        from src.services.ai.gemini_service import GeminiService

        try:
            gemini_text_model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")
            llm = GeminiService(timeout=600, model=gemini_text_model)

            meta = task.draft_meta or {}
            revision_prompt = (
                f"Below is the original blog article. Please revise it according to "
                f"the user's instructions.\n\n"
                f"## Original Article\n"
                f"Title: {meta.get('meta_title', task.topic)}\n"
                f"Description: {meta.get('meta_description', '')}\n\n"
                f"{task.draft_content}\n\n"
                f"## User's Revision Instructions\n{instructions}\n\n"
                f"## Requirements\n"
                f"- Apply ONLY the requested changes, keep everything else the same\n"
                f"- Maintain the same structure, formatting, and image references\n"
                f"- Return the COMPLETE revised article in markdown\n"
                f"- First line: Title: <revised title>\n"
                f"- Second line: Description: <revised meta description>\n"
                f"- Then the full article content\n"
            )

            result = llm.generate(
                prompt=revision_prompt,
                system="You are a professional blog editor. Apply the requested revisions precisely.",
                max_tokens=8192, temperature=0.3,
            )
            result_text = result.get("text", "") if isinstance(result, dict) else str(result)

            revised_title = meta.get("meta_title", task.topic)
            revised_desc = meta.get("meta_description", "")
            revised_content = result_text

            lines = result_text.split("\n")
            content_start = 0
            for i, line in enumerate(lines[:5]):
                stripped = line.strip()
                if stripped.lower().startswith("title:"):
                    revised_title = stripped[6:].strip()
                    content_start = i + 1
                elif stripped.lower().startswith("description:"):
                    revised_desc = stripped[12:].strip()
                    content_start = i + 1
            revised_content = "\n".join(lines[content_start:]).strip() or result_text

            task.draft_content = revised_content
            task.draft_meta = {
                "meta_title": revised_title,
                "meta_description": revised_desc,
                "url_slug": meta.get("url_slug", ""),
            }

            if task.markdown_path:
                try:
                    Path(task.markdown_path).write_text(
                        f"<!--\nTitle: {revised_title}\nDescription: {revised_desc}\n-->\n\n"
                        + revised_content,
                        encoding="utf-8",
                    )
                except Exception as e:
                    logger.warning("Failed to update markdown file: %s", e)

            preview = revised_content[:300] + ("..." if len(revised_content) > 300 else "")
            return (
                f"文章已修改完成\n"
                f"标题: {revised_title}\n"
                f"字数: {len(revised_content.split())}\n\n"
                f"预览:\n{preview}"
            )

        except Exception as e:
            logger.error("Revise article failed: %s", e, exc_info=True)
            return f"修改文章失败: {e}"

    # ── Tool: regenerate_image ──────────────────────────────

    def _tool_regenerate_image(self, params: dict, session_id: str, **_) -> str:
        raw_id = params.get("task_id", "latest")
        image_index = params.get("image_index", 0)
        new_instructions = params.get("new_instructions", "")

        task_id = self._memory.resolve_task_id(session_id, raw_id)
        if not task_id:
            return "未找到可修改的文章任务。"

        task = self._memory.get_task(task_id)
        if not task or not task.image_records:
            return "该任务没有保存的图片记录，无法重新生成。"

        idx = image_index - 1
        if idx < 0 or idx >= len(task.image_records):
            return f"图片编号无效。该文章共有 {len(task.image_records)} 张图，请输入 1-{len(task.image_records)}。"

        record = task.image_records[idx]
        original_prompt = record.get("prompt", "")

        from src.services.ai.gemini_service import GeminiService
        from src.services.blog.blog_image_generator import (
            STICKER_IMAGE_STYLE_PREFIX, STICKER_IMAGE_STYLE_SUFFIX,
        )

        try:
            raw_prompt = f"{original_prompt}. Additional: {new_instructions}" if new_instructions else original_prompt
            enhanced = f"{STICKER_IMAGE_STYLE_PREFIX}{raw_prompt}{STICKER_IMAGE_STYLE_SUFFIX}"

            gemini = GeminiService()
            output_path = Path(record.get("path", f"output/blogs/images/regen_{uuid.uuid4().hex[:8]}.png"))
            output_path.parent.mkdir(parents=True, exist_ok=True)

            result = gemini.generate_image(prompt=enhanced, output_path=output_path)
            if not result.get("success"):
                return f"图片重新生成失败: {result.get('error', 'unknown')}"

            task.image_records[idx]["prompt"] = raw_prompt
            return f"第 {image_index} 张图片已重新生成 ({result.get('size_kb', 0)} KB)"

        except Exception as e:
            return f"图片重新生成失败: {e}"

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
            f"Topic: {task.topic}",
            f"Status: {task.status}",
            f"Created: {task.created_at.strftime('%Y-%m-%d %H:%M')}",
        ]
        if task.result:
            lines.append(f"Title: {task.result.get('title', 'N/A')}")
        if task.error:
            lines.append(f"Error: {task.error}")
        return "\n".join(lines)

    # ── Tool: cancel_task ───────────────────────────────────

    def _tool_cancel_task(self, params: dict, session_id: str, **_) -> str:
        raw_id = params.get("task_id", "latest")
        task_id = self._memory.resolve_task_id(session_id, raw_id)
        if not task_id:
            return "当前会话没有找到任何任务。"

        task = self._memory.get_task(task_id)
        if not task:
            return f"Task {task_id} not found."

        if task.status in ("completed", "failed", "cancelled"):
            return f"Task {task_id} already {task.status}, cannot cancel."

        cancel_event = self._cancel_flags.get(task_id)
        if cancel_event:
            cancel_event.set()

        task.status = "cancelled"
        return f"Task {task_id} has been cancelled."

    # ── Tool: list_recent_blogs ─────────────────────────────

    def _tool_list_recent_blogs(self, params: dict, session_id: str, **_) -> str:
        limit = params.get("limit", 5)
        tasks = self._memory.get_session_tasks(session_id, limit=limit)
        if not tasks:
            return "当前会话没有生成过博客。可以使用 list_local_blogs 查看本地已保存的博客。"

        lines = []
        for t in tasks:
            title = t.result.get("title", t.topic) if t.result else t.topic
            lines.append(f"[{t.status}] {t.task_id}: {title} ({t.created_at.strftime('%m-%d %H:%M')})")
        return "\n".join(lines)

    # ── Tool: list_local_blogs ──────────────────────────────

    def _tool_list_local_blogs(self, params: dict, **_) -> str:
        limit = params.get("limit", 10)
        blogs_dir = Path("./output/blogs")
        if not blogs_dir.exists():
            return "本地没有找到任何已生成的博客文件。"

        md_files = sorted(
            (f for f in blogs_dir.iterdir() if f.suffix == ".md"),
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        if not md_files:
            return "本地没有找到任何已生成的博客文件。"

        lines = [f"本地共有 {len(md_files)} 篇博客，最近 {min(limit, len(md_files))} 篇：\n"]
        for f in md_files[:limit]:
            title = f.stem
            try:
                head = f.read_text(encoding="utf-8")[:500]
                for line in head.split("\n"):
                    if line.strip().lower().startswith("title:"):
                        title = line.strip()[6:].strip()
                        break
            except Exception:
                pass

            ts_match = re.search(r"_(\d{8}_\d{6})", f.name)
            date_str = ""
            if ts_match:
                raw = ts_match.group(1)
                date_str = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]} {raw[9:11]}:{raw[11:13]}"

            images_dir = blogs_dir / "images" / f.stem
            img_count = len([x for x in images_dir.iterdir() if x.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]) if images_dir.exists() else 0

            lines.append(f"  [{date_str}] {title}")
            lines.append(f"    文件: {f.name} | 图片: {img_count} 张")

        return "\n".join(lines)

    # ── Tool: load_local_blog ───────────────────────────────

    def _tool_load_local_blog(self, params: dict, session_id: str, **_) -> str:
        filename = params.get("filename", "")
        if not filename:
            return "Error: filename is required."

        blogs_dir = Path("./output/blogs")
        md_path = blogs_dir / filename

        if not md_path.exists():
            candidates = sorted(
                (f for f in blogs_dir.iterdir() if f.suffix == ".md" and filename.lower() in f.name.lower()),
                key=lambda f: f.stat().st_mtime, reverse=True,
            )
            md_path = candidates[0] if candidates else None
            if not md_path:
                return f"文件未找到: {filename}。请使用 list_local_blogs 查看可用文件。"

        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"读取文件失败: {e}"

        meta_title, meta_description, url_slug = "", "", ""
        keywords = []

        header_match = re.match(r"<!--\s*\n(.*?)\n-->", content, re.DOTALL)
        if header_match:
            for line in header_match.group(1).split("\n"):
                line = line.strip()
                if line.lower().startswith("title:"):
                    meta_title = line[6:].strip()
                elif line.lower().startswith("description:"):
                    meta_description = line[12:].strip()
                elif line.lower().startswith("url slug:"):
                    url_slug = line[9:].strip()
                elif line.lower().startswith("keywords:"):
                    keywords = [k.strip() for k in line[9:].split(",") if k.strip()]
            body = content[header_match.end():].strip()
        else:
            body = content

        images_dir = blogs_dir / "images" / md_path.stem
        image_records = []
        if images_dir.exists():
            for img in sorted(images_dir.iterdir()):
                if img.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    image_records.append({
                        "index": len(image_records),
                        "prompt": img.stem, "alt_text": img.stem,
                        "path": str(img),
                    })

        task = self._memory.create_task(session_id, meta_title or md_path.stem, keywords)
        task.status = "completed"
        task.draft_content = body
        task.draft_meta = {
            "meta_title": meta_title,
            "meta_description": meta_description,
            "url_slug": url_slug,
        }
        task.markdown_path = str(md_path)
        task.images_dir = str(images_dir) if images_dir.exists() else None
        task.image_records = image_records
        task.result = {
            "title": meta_title, "slug": url_slug,
            "content": body, "description": meta_description,
        }

        return (
            f"已加载本地博客到当前会话\n"
            f"标题: {meta_title}\n"
            f"字数: {len(body.split())}\n"
            f"图片: {len(image_records)} 张\n"
            f"Task ID: {task.task_id}\n\n"
            f"现在可以：修改文章 / 重新生成图片 / 上传到 Shopify"
        )

    # ── Tool: publish_to_shopify ────────────────────────────

    def _tool_publish_to_shopify(self, params: dict, session_id: str, **_) -> str:
        raw_id = params.get("task_id", "latest")
        published = params.get("published", False)

        task_id = self._memory.resolve_task_id(session_id, raw_id)
        if not task_id:
            return "未找到可上传的文章任务。请先生成或加载一篇博客。"

        task = self._memory.get_task(task_id)
        if not task or not task.draft_content:
            return "该任务没有保存的草稿内容，无法上传。"

        from src.services.blog.shopify_converter import ShopifyConverter
        from src.services.blog.shopify_publisher import ShopifyPublisher
        from src.models.blog import BusinessProfile

        try:
            meta = task.draft_meta or {}
            profile = BusinessProfile.from_yaml()
            store_domain = profile.platform.get("domain", "") or os.getenv("SHOPIFY_STORE_DOMAIN", "")
            store_url = f"https://{store_domain}" if store_domain else "https://your-store.myshopify.com"

            converter = ShopifyConverter(store_url=store_url)
            article = converter.convert_draft(
                content_md=task.draft_content,
                meta_title=meta.get("meta_title", task.topic),
                meta_description=meta.get("meta_description", ""),
                url_slug=meta.get("url_slug", ""),
                keywords=task.keywords,
            )

            publisher = ShopifyPublisher(blog_handle=os.getenv("SHOPIFY_BLOG_HANDLE", "blog"))
            images_dir = Path(task.images_dir) if task.images_dir else None

            if task.shopify_article_id:
                result = publisher.update_article(
                    article_id=int(task.shopify_article_id),
                    article=article, images_dir=images_dir, published=published,
                )
                action = "更新"
            else:
                result = publisher.publish(
                    article=article, images_dir=images_dir, published=published,
                )
                action = "上传"

            article_id = result.get("id") or task.shopify_article_id
            task.shopify_article_id = article_id

            mode = "已发布" if published else "草稿"
            admin_url = f"https://{store_domain}/admin/articles/{article_id}"

            return (
                f"文章已{action}到 Shopify ({mode})\n"
                f"标题: {meta.get('meta_title', task.topic)}\n"
                f"Shopify 后台: {admin_url}"
            )

        except Exception as e:
            return f"上传到 Shopify 失败: {e}"

    # ── Tool: list_shopify_articles ─────────────────────────

    def _tool_list_shopify_articles(self, params: dict, **_) -> str:
        status_filter = params.get("status", "all")
        limit = params.get("limit", 10)

        from src.services.blog.shopify_publisher import ShopifyPublisher
        try:
            pub = ShopifyPublisher()
            articles = pub.list_articles(
                status=status_filter if status_filter != "all" else None,
                limit=limit,
            )
            if not articles:
                return "Shopify 上没有找到文章。"

            lines = []
            for a in articles:
                status = "published" if a.get("published") else "draft"
                lines.append(f"[{status}] ID:{a['id']} — {a.get('title', 'Untitled')} ({a.get('created_at', '')[:10]})")
            return "\n".join(lines)
        except Exception as e:
            return f"查询 Shopify 文章失败: {e}"

    # ── Tool: view_shopify_article ──────────────────────────

    def _tool_view_shopify_article(self, params: dict, session_id: str, **_) -> str:
        article_id = params.get("article_id", "")
        if article_id.lower() == "latest":
            task_id = self._memory.resolve_task_id(session_id, "latest")
            task = self._memory.get_task(task_id) if task_id else None
            if task and task.shopify_article_id:
                article_id = str(task.shopify_article_id)
            else:
                return "未找到最近的 Shopify 文章 ID。"

        from src.services.blog.shopify_publisher import ShopifyPublisher
        try:
            pub = ShopifyPublisher()
            article = pub.get_article(int(article_id))
            if not article:
                return f"文章 {article_id} 不存在。"

            status = "已发布" if article.get("published_at") else "草稿"
            body_text = re.sub(r"<[^>]+>", "", article.get("body_html", ""))
            body_text = re.sub(r"\s+", " ", body_text).strip()
            preview = body_text[:800] + ("..." if len(body_text) > 800 else "")

            return (
                f"Shopify 文章详情\n"
                f"ID: {article.get('id')}\n"
                f"标题: {article.get('title', 'Untitled')}\n"
                f"状态: {status}\n"
                f"标签: {article.get('tags', '') or '无'}\n"
                f"字数: ~{len(body_text.split())}\n\n"
                f"预览:\n{preview}"
            )
        except Exception as e:
            return f"查看文章失败: {e}"
