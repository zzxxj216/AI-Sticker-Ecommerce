"""Agent Tools — tool definitions and execution for the Feishu Blog Agent.

Each tool is defined with a JSON schema for the LLM and an executor function
that performs the actual work. Tools are registered in TOOL_REGISTRY.
"""

import asyncio
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

from src.core.logger import get_logger
from src.services.feishu.agent_memory import AgentMemory, TaskState

logger = get_logger("agent.tools")

TOOL_DEFINITIONS = [
    {
        "name": "generate_blog",
        "description": (
            "Generate a full SEO blog post about a sticker topic. "
            "Runs the Planner -> Writer -> Reviewer -> Image Generation pipeline. "
            "Returns immediately with a task_id; progress is sent in real-time."
        ),
        "parameters": {
            "topic": {"type": "string", "description": "Blog topic, e.g. 'Cowboy Western Stickers'"},
            "keywords": {
                "type": "array", "items": {"type": "string"},
                "description": "SEO keywords list",
            },
            "auto_publish": {
                "type": "boolean", "default": False,
                "description": "Auto-publish to Shopify as draft when done",
            },
        },
        "required": ["topic", "keywords"],
    },
    {
        "name": "schedule_task",
        "description": (
            "Schedule a blog generation task for a future time. "
            "The blog will be generated and optionally published at the specified time."
        ),
        "parameters": {
            "topic": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "scheduled_time": {
                "type": "string",
                "description": "ISO datetime string, e.g. '2026-03-22T10:00:00'",
            },
            "auto_publish": {"type": "boolean", "default": True},
        },
        "required": ["topic", "keywords", "scheduled_time"],
    },
    {
        "name": "get_task_status",
        "description": "Check the status of a blog generation task.",
        "parameters": {
            "task_id": {
                "type": "string",
                "description": "Task ID, or 'latest' for the most recent task",
            },
        },
        "required": ["task_id"],
    },
    {
        "name": "cancel_task",
        "description": "Cancel a running or scheduled blog generation task.",
        "parameters": {
            "task_id": {
                "type": "string",
                "description": "Task ID, or 'latest' for the most recent task",
            },
        },
        "required": ["task_id"],
    },
    {
        "name": "list_recent_blogs",
        "description": "List recently generated blog tasks for this user.",
        "parameters": {
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    },
    {
        "name": "publish_draft",
        "description": "Publish a Shopify draft article, making it live/public.",
        "parameters": {
            "article_id": {
                "type": "string",
                "description": "Shopify article ID, or 'latest' for the most recent task's article",
            },
        },
        "required": ["article_id"],
    },
    {
        "name": "unpublish_article",
        "description": "Hide/unpublish a published Shopify article (set back to draft).",
        "parameters": {
            "article_id": {
                "type": "string",
                "description": "Shopify article ID, or 'latest'",
            },
        },
        "required": ["article_id"],
    },
    {
        "name": "list_shopify_articles",
        "description": "List articles on Shopify, optionally filtered by status.",
        "parameters": {
            "status": {
                "type": "string",
                "enum": ["all", "published", "draft"],
                "default": "all",
            },
            "limit": {"type": "integer", "default": 10},
        },
        "required": [],
    },
    {
        "name": "view_shopify_article",
        "description": (
            "View the full content of a specific Shopify article by ID. "
            "Returns the article title, body HTML, tags, SEO info, and publish status."
        ),
        "parameters": {
            "article_id": {
                "type": "string",
                "description": "Shopify article ID, or 'latest' for the most recent task's article",
            },
        },
        "required": ["article_id"],
    },
    {
        "name": "revise_article",
        "description": (
            "Revise a previously generated blog article based on user instructions. "
            "Reads the original draft from history, applies the requested changes "
            "using the Writer LLM, and sends the updated preview."
        ),
        "parameters": {
            "task_id": {
                "type": "string", "default": "latest",
                "description": "Task ID of the blog to revise, or 'latest'",
            },
            "instructions": {
                "type": "string",
                "description": "What to change, e.g. '标题改为XX', '第三段需要更详细'",
            },
        },
        "required": ["instructions"],
    },
    {
        "name": "regenerate_image",
        "description": (
            "Regenerate a specific image from a previously generated blog. "
            "Reads the original image prompt from history, optionally modifies it, "
            "generates a new image, and sends it to the chat."
        ),
        "parameters": {
            "task_id": {
                "type": "string", "default": "latest",
                "description": "Task ID, or 'latest'",
            },
            "image_index": {
                "type": "integer",
                "description": "Which image to regenerate (1-based, e.g. 1 for the first image)",
            },
            "new_instructions": {
                "type": "string", "default": "",
                "description": "Optional modification to the image prompt, e.g. '背景换成木质桌面'",
            },
        },
        "required": ["image_index"],
    },
    {
        "name": "upload_to_shopify",
        "description": (
            "Upload a previously generated blog article (saved locally) to Shopify as a draft. "
            "Uses the saved draft content and images from the task history — no need to regenerate."
        ),
        "parameters": {
            "task_id": {
                "type": "string", "default": "latest",
                "description": "Task ID of the blog to upload, or 'latest'",
            },
            "published": {
                "type": "boolean", "default": False,
                "description": "If true, publish immediately. If false, save as draft.",
            },
        },
        "required": [],
    },
    {
        "name": "list_local_blogs",
        "description": (
            "List blog articles saved locally on disk (output/blogs/*.md). "
            "These are all previously generated blogs, including those not uploaded to Shopify. "
            "Use this when the user asks about previously generated articles that are not in memory."
        ),
        "parameters": {
            "limit": {"type": "integer", "default": 10},
        },
        "required": [],
    },
    {
        "name": "load_local_blog",
        "description": (
            "Load a locally saved blog markdown file into the current session so it can be "
            "modified (revise_article), uploaded (upload_to_shopify), or have images regenerated. "
            "Use this when the user refers to a previously generated article that is not in memory."
        ),
        "parameters": {
            "filename": {
                "type": "string",
                "description": "The markdown filename (from list_local_blogs), e.g. 'top-hot-dog-cartoon-sticker-ideas_20260321_163351.md'",
            },
        },
        "required": ["filename"],
    },
]


class AgentToolExecutor:
    """Executes agent tools and manages the blog generation pipeline."""

    def __init__(
        self,
        memory: AgentMemory,
        send_progress: Callable[[str, str], None],
        send_image: Optional[Callable[[str, Path], None]] = None,
    ):
        """
        Args:
            memory: Shared agent memory instance.
            send_progress: Callback(chat_id, message) to send text to Feishu.
            send_image: Callback(chat_id, image_path) to send an image to Feishu.
        """
        self.memory = memory
        self.send_progress = send_progress
        self.send_image = send_image
        self._scheduler = None
        self._cancel_flags: dict[str, threading.Event] = {}

    def set_scheduler(self, scheduler):
        """Set the scheduler manager (injected after construction to avoid circular imports)."""
        self._scheduler = scheduler

    def execute(
        self,
        tool_name: str,
        params: dict,
        user_id: str,
        chat_id: str,
    ) -> str:
        """Execute a tool by name and return the result as a string."""
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return f"Unknown tool: {tool_name}"

        try:
            return handler(params=params, user_id=user_id, chat_id=chat_id)
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
            return f"Tool execution failed: {e}"

    # ------------------------------------------------------------------
    # Tool: generate_blog
    # ------------------------------------------------------------------

    def _tool_generate_blog(self, params: dict, user_id: str, chat_id: str) -> str:
        topic = params.get("topic", "")
        keywords = params.get("keywords", [])
        auto_publish = params.get("auto_publish", False)

        if not topic or not keywords:
            return "Error: topic and keywords are required."

        task = self.memory.create_task(user_id, chat_id, topic, keywords)
        cancel_event = threading.Event()
        self._cancel_flags[task.task_id] = cancel_event

        thread = threading.Thread(
            target=self._run_blog_pipeline,
            args=(task, auto_publish, cancel_event),
            daemon=True,
        )
        thread.start()

        return (
            f"Blog generation started. task_id={task.task_id}, "
            f"topic='{topic}', keywords={keywords}. "
            f"Progress updates will be sent in real-time."
        )

    def _run_blog_pipeline(
        self,
        task: TaskState,
        auto_publish: bool,
        cancel_event: threading.Event,
    ):
        """Run the full blog pipeline in a background thread."""
        from src.models.blog import BusinessProfile, BlogInput, BlogDraft, ReviewResult
        from src.services.blog.planner_agent import PlannerAgent
        from src.services.blog.plan_reviewer_agent import PlanReviewerAgent
        from src.services.blog.writer_agent import WriterAgent
        from src.services.blog.reviewer_agent import ReviewerAgent
        from src.services.blog.blog_image_generator import BlogImageGenerator
        from src.services.blog.orchestrator import BlogOrchestrator
        from src.services.blog.shopify_publisher import ShopifyPublisher
        from src.services.ai.gemini_service import GeminiService

        chat_id = task.chat_id

        def progress(msg: str):
            task.update_status(task.status, msg)
            self.send_progress(chat_id, msg)

        def on_content_ready(event_type: str, data):
            """Send rich content to Feishu at key milestones."""
            try:
                if event_type == "plan_ready":
                    plan = data
                    task.content_plan = plan.model_dump()
                    lines = ["📋 文章大纲："]
                    for i, section in enumerate(plan.outline, 1):
                        title = section.get("title") or section.get("heading", "")
                        lines.append(f"  {i}. {title}")
                    if plan.image_plans:
                        lines.append(f"\n已规划 {len(plan.image_plans)} 张配图位置")
                    self.send_progress(chat_id, "\n".join(lines))

                elif event_type == "draft_ready":
                    draft = data
                    preview_len = 300
                    content_preview = draft.content[:preview_len]
                    if len(draft.content) > preview_len:
                        content_preview += "..."
                    msg = (
                        f"✅ 文章草稿完成\n"
                        f"标题: {draft.meta_title}\n"
                        f"摘要: {draft.meta_description}\n"
                        f"字数: {len(draft.content.split())}\n\n"
                        f"预览:\n{content_preview}"
                    )
                    self.send_progress(chat_id, msg)

                elif event_type == "finalized":
                    draft = data.get("draft")
                    image_records = data.get("image_records", [])
                    images_dir = data.get("images_dir")
                    markdown_path = data.get("markdown_path")

                    task.draft_content = draft.content
                    task.draft_meta = {
                        "meta_title": draft.meta_title,
                        "meta_description": draft.meta_description,
                        "url_slug": draft.url_slug,
                    }
                    task.image_records = image_records
                    task.images_dir = images_dir
                    task.markdown_path = markdown_path
                    self.memory.save_task(task)
            except Exception as e:
                logger.warning(f"on_content_ready error ({event_type}): {e}")

        def on_image_ready(image_path, alt_text: str, prompt: str, index: int):
            """Send each generated image to the Feishu chat."""
            if self.send_image:
                try:
                    self.send_image(chat_id, Path(image_path))
                except Exception as e:
                    logger.warning(f"Failed to send image {index + 1} to Feishu: {e}")

        try:
            gemini_text_model = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")
            profile = BusinessProfile.from_yaml()

            writer_llm = GeminiService(timeout=600, model=gemini_text_model)
            reviewer_llm = GeminiService(timeout=300, model=gemini_text_model)
            planner_llm = GeminiService(timeout=300, model=gemini_text_model)

            writer = WriterAgent(writer_llm, profile)
            reviewer = ReviewerAgent(reviewer_llm, profile)
            planner = PlannerAgent(planner_llm, profile)
            plan_reviewer = PlanReviewerAgent(planner_llm, profile)

            image_gemini = GeminiService()
            image_generator = BlogImageGenerator(image_gemini)

            store_domain = profile.platform.get("domain", "") or os.getenv("SHOPIFY_STORE_DOMAIN", "")
            store_url = f"https://{store_domain}" if store_domain else "https://your-store.myshopify.com"

            publisher = None
            if auto_publish:
                try:
                    publisher = ShopifyPublisher(
                        blog_handle=os.getenv("SHOPIFY_BLOG_HANDLE", "blog"),
                    )
                except ValueError as e:
                    progress(f"Shopify 发布器不可用: {e}")

            orchestrator = BlogOrchestrator(
                writer=writer,
                reviewer=reviewer,
                planner=planner,
                plan_reviewer=plan_reviewer,
                image_generator=image_generator,
                store_url=store_url,
                publisher=publisher,
                publish_live=False,
            )

            blog_input = BlogInput(
                topic=task.topic,
                seo_keywords=task.keywords,
            )

            def on_review(draft: BlogDraft, review: ReviewResult, iteration: int) -> str:
                if cancel_event.is_set():
                    return "abort"
                if review.passed:
                    return "accept"
                return "auto_revise"

            task.update_status("planning")
            self.memory.save_task(task)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                final_draft = loop.run_until_complete(
                    orchestrator.run(
                        blog_input, on_review, progress,
                        on_content_ready=on_content_ready,
                        on_image_ready=on_image_ready,
                    )
                )
            finally:
                loop.close()

            if cancel_event.is_set():
                task.update_status("cancelled", "Task was cancelled by user.")
                self.memory.save_task(task)
                progress("任务已取消。")
                return

            if final_draft:
                task.update_status("done", f"Blog complete: {final_draft.meta_title}")
                task.result = {
                    "meta_title": final_draft.meta_title,
                    "url_slug": final_draft.url_slug,
                    "word_count": len(final_draft.content.split()),
                    "iteration": final_draft.iteration,
                }
                if final_draft.shopify_article_id:
                    task.shopify_article_id = final_draft.shopify_article_id
                    task.result["shopify_article_id"] = final_draft.shopify_article_id

                self.memory.save_task(task)

                summary = (
                    f"🎉 博客生成完成！\n"
                    f"标题: {final_draft.meta_title}\n"
                    f"字数: {task.result['word_count']}\n"
                    f"迭代次数: {final_draft.iteration}"
                )
                if final_draft.shopify_article_id:
                    admin_url = f"https://{store_domain}/admin/articles/{final_draft.shopify_article_id}"
                    summary += f"\nShopify (草稿): {admin_url}"
                summary += "\n\n如需修改文章或重新生成图片，请直接告诉我。"
                progress(summary)
            else:
                task.update_status("failed", "Pipeline returned no draft.")
                self.memory.save_task(task)
                progress("博客生成失败 — 未产出草稿。")

        except Exception as e:
            logger.error(f"Pipeline error for task {task.task_id}: {e}", exc_info=True)
            task.update_status("failed", str(e))
            task.error = str(e)
            self.memory.save_task(task)
            progress(f"博客生成失败: {e}")
        finally:
            self._cancel_flags.pop(task.task_id, None)

    # ------------------------------------------------------------------
    # Tool: schedule_task
    # ------------------------------------------------------------------

    def _tool_schedule_task(self, params: dict, user_id: str, chat_id: str) -> str:
        topic = params.get("topic", "")
        keywords = params.get("keywords", [])
        scheduled_str = params.get("scheduled_time", "")
        auto_publish = params.get("auto_publish", True)

        if not topic or not keywords or not scheduled_str:
            return "Error: topic, keywords, and scheduled_time are all required."

        try:
            scheduled_time = datetime.fromisoformat(scheduled_str)
        except ValueError:
            return f"Error: invalid datetime format '{scheduled_str}'. Use ISO format like '2026-03-22T10:00:00'."

        if scheduled_time <= datetime.now():
            return "Error: scheduled_time must be in the future."

        task = self.memory.create_task(
            user_id, chat_id, topic, keywords, scheduled_time=scheduled_time,
        )

        if self._scheduler:
            self._scheduler.schedule(task, auto_publish=auto_publish)

        return (
            f"Task scheduled. task_id={task.task_id}, "
            f"topic='{topic}', scheduled_time={scheduled_time.strftime('%Y-%m-%d %H:%M')}, "
            f"auto_publish={auto_publish}."
        )

    # ------------------------------------------------------------------
    # Tool: get_task_status
    # ------------------------------------------------------------------

    def _tool_get_task_status(self, params: dict, user_id: str, chat_id: str) -> str:
        raw_id = params.get("task_id", "latest")
        task_id = self.memory.resolve_task_id(user_id, raw_id)
        if not task_id:
            return "No tasks found for this user."

        task = self.memory.get_task(task_id)
        if not task:
            return f"Task {task_id} not found."

        lines = [
            f"Task: {task.task_id}",
            f"Topic: {task.topic}",
            f"Status: {task.status}",
            f"Created: {task.created_at.strftime('%Y-%m-%d %H:%M')}",
        ]
        if task.scheduled_time:
            lines.append(f"Scheduled: {task.scheduled_time.strftime('%Y-%m-%d %H:%M')}")
        if task.shopify_article_id:
            lines.append(f"Shopify Article ID: {task.shopify_article_id}")
        if task.result:
            lines.append(f"Title: {task.result.get('meta_title', 'N/A')}")
            lines.append(f"Words: {task.result.get('word_count', 'N/A')}")
        if task.error:
            lines.append(f"Error: {task.error}")
        if task.progress_messages:
            lines.append(f"Last update: {task.progress_messages[-1]}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool: cancel_task
    # ------------------------------------------------------------------

    def _tool_cancel_task(self, params: dict, user_id: str, chat_id: str) -> str:
        raw_id = params.get("task_id", "latest")
        task_id = self.memory.resolve_task_id(user_id, raw_id)
        if not task_id:
            return "No tasks found for this user."

        task = self.memory.get_task(task_id)
        if not task:
            return f"Task {task_id} not found."

        if task.status in ("done", "failed", "cancelled"):
            return f"Task {task_id} is already {task.status}, cannot cancel."

        if task.status == "scheduled" and self._scheduler:
            self._scheduler.cancel(task_id)

        cancel_event = self._cancel_flags.get(task_id)
        if cancel_event:
            cancel_event.set()

        task.update_status("cancelled", "Cancelled by user.")
        self.memory.save_task(task)
        return f"Task {task_id} has been cancelled."

    # ------------------------------------------------------------------
    # Tool: list_recent_blogs
    # ------------------------------------------------------------------

    def _tool_list_recent_blogs(self, params: dict, user_id: str, chat_id: str) -> str:
        limit = params.get("limit", 5)
        tasks = self.memory.get_user_tasks(user_id, limit=limit)

        if not tasks:
            return "No blog tasks found for this user."

        lines = []
        for t in tasks:
            status_icon = {
                "done": "[done]", "failed": "[fail]", "cancelled": "[cancelled]",
                "scheduled": "[scheduled]", "pending": "[pending]",
            }.get(t.status, f"[{t.status}]")
            title = t.result.get("meta_title", t.topic) if t.result else t.topic
            lines.append(f"{status_icon} {t.task_id}: {title} ({t.created_at.strftime('%m-%d %H:%M')})")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool: publish_draft
    # ------------------------------------------------------------------

    def _tool_publish_draft(self, params: dict, user_id: str, chat_id: str) -> str:
        article_id = self._resolve_article_id(params.get("article_id", "latest"), user_id)
        if not article_id:
            return "No article found. Generate a blog first or provide an article ID."

        from src.services.blog.shopify_publisher import ShopifyPublisher
        try:
            pub = ShopifyPublisher()
            result = pub.update_article_status(int(article_id), published=True)
            title = result.get("title", "Unknown")
            return f"Article '{title}' (ID: {article_id}) is now published and visible."
        except Exception as e:
            return f"Failed to publish article: {e}"

    # ------------------------------------------------------------------
    # Tool: unpublish_article
    # ------------------------------------------------------------------

    def _tool_unpublish_article(self, params: dict, user_id: str, chat_id: str) -> str:
        article_id = self._resolve_article_id(params.get("article_id", "latest"), user_id)
        if not article_id:
            return "No article found. Provide an article ID."

        from src.services.blog.shopify_publisher import ShopifyPublisher
        try:
            pub = ShopifyPublisher()
            result = pub.update_article_status(int(article_id), published=False)
            title = result.get("title", "Unknown")
            return f"Article '{title}' (ID: {article_id}) has been set to draft (hidden)."
        except Exception as e:
            return f"Failed to unpublish article: {e}"

    # ------------------------------------------------------------------
    # Tool: list_shopify_articles
    # ------------------------------------------------------------------

    def _tool_list_shopify_articles(self, params: dict, user_id: str, chat_id: str) -> str:
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
                return "No articles found on Shopify."

            lines = []
            for a in articles:
                status_label = "published" if a.get("published") else "draft"
                created = a.get("created_at", "")[:10]
                lines.append(
                    f"[{status_label}] ID:{a['id']} — {a.get('title', 'Untitled')} ({created})"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Failed to list articles: {e}"

    # ------------------------------------------------------------------
    # Tool: view_shopify_article
    # ------------------------------------------------------------------

    def _tool_view_shopify_article(self, params: dict, user_id: str, chat_id: str) -> str:
        article_id = self._resolve_article_id(params.get("article_id", "latest"), user_id)
        if not article_id:
            return "未找到文章。请先生成一篇博客或提供文章 ID。"

        from src.services.blog.shopify_publisher import ShopifyPublisher
        try:
            pub = ShopifyPublisher()
            article = pub.get_article(int(article_id))

            if not article:
                return f"文章 {article_id} 不存在或已被删除。"

            status = "已发布" if article.get("published_at") else "草稿"
            tags = article.get("tags", "") or "无"
            created = (article.get("created_at") or "")[:10]

            body_html = article.get("body_html", "")
            import re as _re
            body_text = _re.sub(r"<[^>]+>", "", body_html)
            body_text = _re.sub(r"\s+", " ", body_text).strip()

            preview_len = 800
            preview = body_text[:preview_len]
            if len(body_text) > preview_len:
                preview += "..."

            msg = (
                f"📄 Shopify 文章详情\n"
                f"ID: {article.get('id')}\n"
                f"标题: {article.get('title', 'Untitled')}\n"
                f"状态: {status}\n"
                f"标签: {tags}\n"
                f"创建时间: {created}\n"
                f"字数: ~{len(body_text.split())}\n\n"
                f"内容预览:\n{preview}"
            )
            self.send_progress(chat_id, msg)
            return f"Article {article_id} displayed. Title: {article.get('title', '')}, Status: {status}"

        except Exception as e:
            return f"查看文章失败: {e}"

    # ------------------------------------------------------------------
    # Tool: upload_to_shopify
    # ------------------------------------------------------------------

    def _tool_upload_to_shopify(self, params: dict, user_id: str, chat_id: str) -> str:
        raw_id = params.get("task_id", "latest")
        published = params.get("published", False)

        task_id = self.memory.resolve_task_id(user_id, raw_id)
        if not task_id:
            return "未找到可上传的文章任务。请先生成一篇博客。"

        task = self.memory.get_task(task_id)
        if not task or not task.draft_content:
            return "该任务没有保存的草稿内容，无法上传。"

        from src.services.blog.shopify_converter import ShopifyConverter
        from src.services.blog.shopify_publisher import ShopifyPublisher
        from src.models.blog import BusinessProfile

        try:
            self.send_progress(chat_id, "正在上传文章到 Shopify...")

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

            publisher = ShopifyPublisher(
                blog_handle=os.getenv("SHOPIFY_BLOG_HANDLE", "blog"),
            )

            images_dir = Path(task.images_dir) if task.images_dir else None
            existing_id = task.shopify_article_id

            if existing_id:
                self.send_progress(chat_id, f"检测到已有 Shopify 文章 (ID: {existing_id})，正在更新...")
                result = publisher.update_article(
                    article_id=int(existing_id),
                    article=article,
                    images_dir=images_dir,
                    published=published,
                )
                action = "更新"
            else:
                result = publisher.publish(
                    article=article,
                    images_dir=images_dir,
                    published=published,
                )
                action = "上传"

            article_id = result.get("id") or existing_id
            task.shopify_article_id = article_id
            if task.result is None:
                task.result = {}
            task.result["shopify_article_id"] = article_id
            self.memory.save_task(task)

            mode = "已发布" if published else "草稿"
            admin_url = f"https://{store_domain}/admin/articles/{article_id}"
            msg = (
                f"✅ 文章已{action}到 Shopify ({mode})\n"
                f"标题: {meta.get('meta_title', task.topic)}\n"
                f"Shopify 后台: {admin_url}"
            )
            self.send_progress(chat_id, msg)

            return f"Article {action} to Shopify as {mode}. ID: {article_id}, admin: {admin_url}"

        except Exception as e:
            logger.error(f"Upload to Shopify failed: {e}", exc_info=True)
            return f"上传到 Shopify 失败: {e}"

    # ------------------------------------------------------------------
    # Tool: list_local_blogs
    # ------------------------------------------------------------------

    def _tool_list_local_blogs(self, params: dict, user_id: str, chat_id: str) -> str:
        limit = params.get("limit", 10)
        blogs_dir = Path("./output/blogs")
        if not blogs_dir.exists():
            return "本地没有找到任何已生成的博客文件。"

        md_files = sorted(
            (f for f in blogs_dir.iterdir() if f.suffix == ".md"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
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

            import re as _re
            ts_match = _re.search(r"_(\d{8}_\d{6})", f.name)
            date_str = ""
            if ts_match:
                raw = ts_match.group(1)
                date_str = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]} {raw[9:11]}:{raw[11:13]}"

            images_dir_name = f.stem
            images_dir = blogs_dir / "images" / images_dir_name
            img_count = 0
            if images_dir.exists():
                img_count = len([x for x in images_dir.iterdir() if x.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")])

            lines.append(f"  [{date_str}] {title}")
            lines.append(f"    文件: {f.name} | 图片: {img_count} 张")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool: load_local_blog
    # ------------------------------------------------------------------

    def _tool_load_local_blog(self, params: dict, user_id: str, chat_id: str) -> str:
        filename = params.get("filename", "")
        if not filename:
            return "Error: filename is required."

        blogs_dir = Path("./output/blogs")
        md_path = blogs_dir / filename

        if not md_path.exists():
            candidates = sorted(
                (f for f in blogs_dir.iterdir() if f.suffix == ".md" and filename.lower() in f.name.lower()),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                md_path = candidates[0]
            else:
                return f"文件未找到: {filename}。请使用 list_local_blogs 查看可用文件。"

        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"读取文件失败: {e}"

        meta_title = ""
        meta_description = ""
        url_slug = ""
        keywords = []

        import re as _re
        header_match = _re.match(r"<!--\s*\n(.*?)\n-->", content, _re.DOTALL)
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
            body_content = content[header_match.end():].strip()
        else:
            body_content = content

        images_dir_name = md_path.stem
        images_dir = blogs_dir / "images" / images_dir_name
        image_records = []
        if images_dir.exists():
            for img_file in sorted(images_dir.iterdir()):
                if img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    image_records.append({
                        "index": len(image_records),
                        "prompt": img_file.stem,
                        "alt_text": img_file.stem,
                        "path": str(img_file),
                    })

        topic = meta_title or md_path.stem
        task = self.memory.create_task(user_id, chat_id, topic, keywords)
        task.update_status("done", f"Loaded from local file: {md_path.name}")
        task.draft_content = body_content
        task.draft_meta = {
            "meta_title": meta_title,
            "meta_description": meta_description,
            "url_slug": url_slug,
        }
        task.markdown_path = str(md_path)
        task.images_dir = str(images_dir) if images_dir.exists() else None
        task.image_records = image_records
        task.result = {
            "meta_title": meta_title,
            "url_slug": url_slug,
            "word_count": len(body_content.split()),
        }
        self.memory.save_task(task)

        msg = (
            f"✅ 已加载本地博客到当前会话\n"
            f"标题: {meta_title}\n"
            f"字数: {len(body_content.split())}\n"
            f"图片: {len(image_records)} 张\n"
            f"Task ID: {task.task_id}\n\n"
            f"现在您可以：\n"
            f"• 修改文章内容（如：把标题改为XX）\n"
            f"• 重新生成图片（如：第2张图重新生成）\n"
            f"• 上传到 Shopify（如：上传到草稿箱）"
        )
        self.send_progress(chat_id, msg)

        return (
            f"Blog loaded. task_id={task.task_id}, title='{meta_title}', "
            f"words={len(body_content.split())}, images={len(image_records)}"
        )

    # ------------------------------------------------------------------
    # Tool: revise_article
    # ------------------------------------------------------------------

    def _tool_revise_article(self, params: dict, user_id: str, chat_id: str) -> str:
        raw_id = params.get("task_id", "latest")
        instructions = params.get("instructions", "")
        if not instructions:
            return "Error: instructions are required."

        task_id = self.memory.resolve_task_id(user_id, raw_id)
        if not task_id:
            return "未找到可修改的文章任务。请先生成一篇博客。"

        task = self.memory.get_task(task_id)
        if not task or not task.draft_content:
            return "该任务没有保存的草稿内容，无法修改。"

        from src.models.blog import BusinessProfile, BlogInput
        from src.services.ai.gemini_service import GeminiService

        try:
            self.send_progress(chat_id, "正在修改文章...")

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
                f"## User's Revision Instructions\n"
                f"{instructions}\n\n"
                f"## Requirements\n"
                f"- Apply ONLY the requested changes, keep everything else the same\n"
                f"- Maintain the same structure, formatting, and image references\n"
                f"- Return the COMPLETE revised article in markdown\n"
                f"- At the top, include a line: Title: <revised title>\n"
                f"- Then include: Description: <revised meta description>\n"
                f"- Then the full article content\n"
            )

            result = llm.generate(
                prompt=revision_prompt,
                system="You are a professional blog editor. Apply the requested revisions precisely.",
                max_tokens=8192,
                temperature=0.3,
            )
            result_text = result.get("text", "") if isinstance(result, dict) else str(result)

            revised_title = meta.get("meta_title", task.topic)
            revised_desc = meta.get("meta_description", "")
            revised_content = result_text

            lines = result_text.split("\n")
            content_start = 0
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.lower().startswith("title:"):
                    revised_title = stripped[6:].strip()
                    content_start = i + 1
                elif stripped.lower().startswith("description:"):
                    revised_desc = stripped[12:].strip()
                    content_start = i + 1
                elif stripped and not stripped.startswith("#") and i < 5:
                    continue
                else:
                    break
            revised_content = "\n".join(lines[content_start:]).strip()
            if not revised_content:
                revised_content = result_text

            task.draft_content = revised_content
            task.draft_meta = {
                "meta_title": revised_title,
                "meta_description": revised_desc,
                "url_slug": meta.get("url_slug", ""),
            }
            self.memory.save_task(task)

            if task.markdown_path:
                try:
                    Path(task.markdown_path).write_text(
                        f"<!--\nTitle: {revised_title}\nDescription: {revised_desc}\n-->\n\n"
                        + revised_content,
                        encoding="utf-8",
                    )
                except Exception as e:
                    logger.warning(f"Failed to update markdown file: {e}")

            preview_len = 300
            preview = revised_content[:preview_len]
            if len(revised_content) > preview_len:
                preview += "..."

            msg = (
                f"✅ 文章已修改完成\n"
                f"标题: {revised_title}\n"
                f"摘要: {revised_desc}\n"
                f"字数: {len(revised_content.split())}\n\n"
                f"预览:\n{preview}"
            )
            self.send_progress(chat_id, msg)

            return f"Article revised successfully. New title: {revised_title}, word count: {len(revised_content.split())}"

        except Exception as e:
            logger.error(f"Revise article failed: {e}", exc_info=True)
            return f"修改文章失败: {e}"

    # ------------------------------------------------------------------
    # Tool: regenerate_image
    # ------------------------------------------------------------------

    def _tool_regenerate_image(self, params: dict, user_id: str, chat_id: str) -> str:
        raw_id = params.get("task_id", "latest")
        image_index = params.get("image_index", 0)
        new_instructions = params.get("new_instructions", "")

        task_id = self.memory.resolve_task_id(user_id, raw_id)
        if not task_id:
            return "未找到可修改的文章任务。请先生成一篇博客。"

        task = self.memory.get_task(task_id)
        if not task or not task.image_records:
            return "该任务没有保存的图片记录，无法重新生成。"

        idx_0based = image_index - 1
        if idx_0based < 0 or idx_0based >= len(task.image_records):
            total = len(task.image_records)
            return f"图片编号无效。该文章共有 {total} 张图，请输入 1-{total}。"

        record = task.image_records[idx_0based]
        original_prompt = record.get("prompt", "")
        original_path = record.get("path", "")
        alt_text = record.get("alt_text", "")

        from src.services.ai.gemini_service import GeminiService
        from src.services.blog.blog_image_generator import (
            STICKER_IMAGE_STYLE_PREFIX, STICKER_IMAGE_STYLE_SUFFIX,
        )

        try:
            self.send_progress(chat_id, f"正在重新生成第 {image_index} 张图片...")

            if new_instructions:
                raw_prompt = f"{original_prompt}. Additional requirements: {new_instructions}"
            else:
                raw_prompt = original_prompt

            enhanced_prompt = f"{STICKER_IMAGE_STYLE_PREFIX}{raw_prompt}{STICKER_IMAGE_STYLE_SUFFIX}"

            gemini = GeminiService()
            output_path = Path(original_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            result = gemini.generate_image(
                prompt=enhanced_prompt,
                output_path=output_path,
            )

            if not result.get("success"):
                return f"图片重新生成失败: {result.get('error', 'unknown error')}"

            task.image_records[idx_0based]["prompt"] = raw_prompt
            self.memory.save_task(task)

            if self.send_image:
                try:
                    self.send_image(chat_id, output_path)
                except Exception as e:
                    logger.warning(f"Failed to send regenerated image: {e}")

            return (
                f"第 {image_index} 张图片已重新生成。"
                f"({result.get('size_kb', 0)} KB)"
            )

        except Exception as e:
            logger.error(f"Regenerate image failed: {e}", exc_info=True)
            return f"图片重新生成失败: {e}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_article_id(self, article_id_or_latest: str, user_id: str) -> Optional[str]:
        """Resolve 'latest' to the actual Shopify article ID from the user's latest task."""
        if article_id_or_latest.lower() == "latest":
            task = self.memory.get_latest_task(user_id)
            if task and task.shopify_article_id:
                return str(task.shopify_article_id)
            return None
        return article_id_or_latest
