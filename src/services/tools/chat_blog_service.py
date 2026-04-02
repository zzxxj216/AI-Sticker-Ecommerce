"""Chat-based blog generation service.

Combines multi-turn conversation (via ChatService/Claude) with the
BlogOrchestrator pipeline to let users plan and generate SEO blog
articles through natural language dialogue.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from src.core.logger import get_logger
from src.services.ai.chat_service import ChatService
from src.services.ai.claude_service import ClaudeService
from src.services.ai.openai_service import OpenAIService

logger = get_logger("tools.chat_blog")

BLOG_CHAT_SYSTEM = """\
你是一位专业的电商博客内容策略师，为贴纸/卡贴电商店铺提供 SEO 博客规划服务。

你的职责：
1. 了解用户想要写的博客主题、目标关键词和内容方向
2. 提供 SEO 优化建议，帮助用户制定合理的关键词策略
3. 当信息足够时，总结写作方案供用户确认

沟通风格：
- 专业、高效、结构清晰
- 主动给出关键词建议和内容框架
- 结合贴纸/卡贴电商的业务特点给建议

你可以帮助的维度：
- 博客主题（Topic）：围绕什么话题展开
- SEO 关键词（Keywords）：目标长尾词和语义词
- 内容方向（Direction）：产品推荐、趋势解读、使用场景、节日营销等
- 特殊要求：语言、字数、风格、CTA 等

注意：你只负责内容策略咨询，实际的博客生成由系统的「生成 Blog」按钮触发。\
"""

EXTRACT_SYSTEM = """\
You are a parameter extraction assistant. Given a conversation between a user and a blog content strategist, \
extract the blog generation parameters as a JSON object.

Output ONLY a valid JSON object with these fields:
{
  "topic": "the blog topic (required, in English)",
  "seo_keywords": ["keyword1", "keyword2", "keyword3"],
  "additional_instructions": "any extra instructions like tone, CTA, word count (optional)",
  "language": "en or zh (default: en)",
  "summary_zh": "一句话中文总结这篇博客的写作方案"
}

If the conversation lacks sufficient information for a topic, set topic to an empty string.\
"""


class ChatBlogService:
    """Manages chat sessions and blog generation tasks."""

    def __init__(self):
        self._chat = ChatService(
            claude_service=ClaudeService(),
            system_prompt=BLOG_CHAT_SYSTEM,
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
        """Extract structured blog parameters from conversation history."""
        history = self.get_history(session_id)
        if not history:
            return {"topic": "", "seo_keywords": [], "additional_instructions": ""}

        conversation_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
            for m in history[-20:]
        )

        try:
            openai = OpenAIService()
            result = openai.generate_json(
                prompt=f"Conversation:\n{conversation_text}\n\nExtract the blog parameters:",
                system=EXTRACT_SYSTEM,
                temperature=0.2,
            )
            if isinstance(result, dict):
                if isinstance(result.get("seo_keywords"), str):
                    result["seo_keywords"] = [
                        k.strip() for k in result["seo_keywords"].split(",") if k.strip()
                    ]
                return result
        except Exception as e:
            logger.warning("Blog parameter extraction failed: %s", e)

        return {"topic": "", "seo_keywords": [], "additional_instructions": ""}

    def start_generation(
        self,
        session_id: str,
        params: Optional[dict] = None,
        created_by: str = "system",
    ) -> str:
        """Start blog generation in background. Returns task_id."""
        if params is None:
            params = self.extract_params(session_id)

        topic = params.get("topic", "").strip()
        if not topic:
            raise ValueError("无法从对话中提取有效的博客主题，请继续描述你的需求。")

        keywords = params.get("seo_keywords", [])
        if not keywords:
            raise ValueError("缺少 SEO 关键词，请在对话中说明目标关键词。")

        task_id = f"blog_{uuid.uuid4().hex[:12]}"
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
            target=self._run_blog_pipeline,
            args=(task_id, params),
            daemon=True,
        )
        thread.start()
        return task_id

    def get_task_queue(self, task_id: str) -> Optional[queue.Queue]:
        return self._task_queues.get(task_id)

    def get_task(self, task_id: str) -> Optional[dict]:
        return self._tasks.get(task_id)

    def _run_blog_pipeline(self, task_id: str, params: dict) -> None:
        q = self._task_queues[task_id]

        try:
            q.put({"type": "progress", "data": {"step": "init", "message": "正在初始化博客生成管线..."}})

            from src.models.blog import BusinessProfile, BlogInput, BlogDraft, ReviewResult
            from src.services.blog import (
                WriterAgent, ReviewerAgent, BlogOrchestrator,
            )
            from src.services.blog.planner_agent import PlannerAgent
            from src.services.blog.plan_reviewer_agent import PlanReviewerAgent
            from src.services.blog.blog_image_generator import BlogImageGenerator
            from src.services.ai.gemini_service import GeminiService

            gemini_text_model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")

            def _make_llm(timeout: int):
                return GeminiService(timeout=timeout, model=gemini_text_model)

            profile = BusinessProfile.from_yaml()

            q.put({"type": "progress", "data": {"step": "agents", "message": "正在创建 AI Agent..."}})

            writer_service = _make_llm(600)
            reviewer_service = _make_llm(300)
            planner_service = _make_llm(300)

            writer = WriterAgent(writer_service, profile)
            reviewer = ReviewerAgent(reviewer_service, profile)
            planner = PlannerAgent(planner_service, profile)
            plan_reviewer = PlanReviewerAgent(planner_service, profile)

            gemini_for_images = GeminiService()
            image_generator = BlogImageGenerator(gemini_for_images)

            blog_input = BlogInput(
                topic=params["topic"],
                seo_keywords=params.get("seo_keywords", []),
                additional_instructions=params.get("additional_instructions", ""),
                language=params.get("language", "en"),
            )

            def on_review(draft: BlogDraft, review: ReviewResult, iteration: int) -> str:
                q.put({
                    "type": "review",
                    "data": {
                        "iteration": iteration,
                        "score": review.overall_score,
                        "passed": review.passed,
                        "summary": review.summary,
                    },
                })
                return "accept" if review.passed else "auto_revise"

            def progress_cb(msg: str):
                q.put({"type": "progress", "data": {"message": msg}})

            def on_content_ready(event_type: str, data):
                if event_type == "plan_ready":
                    q.put({"type": "plan_ready", "data": {"message": "文章规划完成"}})
                elif event_type == "draft_ready":
                    q.put({"type": "draft_ready", "data": {"message": "初稿完成"}})

            store_domain = profile.platform.get("domain", "") or os.getenv("SHOPIFY_STORE_DOMAIN", "")
            store_url = f"https://{store_domain}" if store_domain else "https://your-store.myshopify.com"

            orchestrator = BlogOrchestrator(
                writer=writer,
                reviewer=reviewer,
                planner=planner,
                plan_reviewer=plan_reviewer,
                max_iterations=3,
                pass_threshold=75.0,
                image_generator=image_generator,
                store_url=store_url,
            )

            q.put({"type": "progress", "data": {"step": "generating", "message": "开始生成博客文章..."}})

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                final_draft = loop.run_until_complete(
                    orchestrator.run(
                        blog_input,
                        on_review=on_review,
                        progress_callback=progress_cb,
                        on_content_ready=on_content_ready,
                    )
                )
            finally:
                loop.close()

            if final_draft:
                self._tasks[task_id]["status"] = "completed"
                self._tasks[task_id]["result"] = {
                    "title": final_draft.meta_title,
                    "description": final_draft.meta_description,
                    "slug": final_draft.url_slug,
                    "content": final_draft.content,
                    "iteration": final_draft.iteration,
                }

                self._save_blog_to_db(task_id, params, final_draft)

                q.put({
                    "type": "complete",
                    "data": {
                        "status": "completed",
                        "title": final_draft.meta_title,
                        "description": final_draft.meta_description,
                        "slug": final_draft.url_slug,
                        "content": final_draft.content,
                        "iteration": final_draft.iteration,
                    },
                })
            else:
                self._tasks[task_id]["status"] = "failed"
                q.put({
                    "type": "error",
                    "data": {"error": "博客生成未产出最终稿件"},
                })

        except Exception as exc:
            logger.error("Chat blog generation failed: %s", exc, exc_info=True)
            self._tasks[task_id]["status"] = "failed"
            q.put({
                "type": "error",
                "data": {"error": str(exc)},
            })
        finally:
            q.put(None)  # sentinel

    def _save_blog_to_db(self, task_id: str, params: dict, draft) -> None:
        """Persist the completed blog draft into blog_drafts table."""
        try:
            from src.services.ops.db import OpsDatabase

            task = self._tasks.get(task_id, {})
            db = OpsDatabase()
            db.insert_blog_draft({
                "id": task_id,
                "session_id": task.get("session_id", ""),
                "topic": params.get("topic", ""),
                "seo_keywords": params.get("seo_keywords", []),
                "meta_title": draft.meta_title,
                "meta_description": draft.meta_description,
                "url_slug": draft.url_slug,
                "content": draft.content,
                "language": params.get("language", "en"),
                "status": "completed",
                "publish_status": "unpublished",
                "iteration": getattr(draft, "iteration", 0),
                "created_by": task.get("created_by", "system"),
            })
            logger.info("Saved blog draft %s to DB", task_id)
        except Exception as exc:
            logger.warning("Failed to save blog draft to DB: %s", exc)
