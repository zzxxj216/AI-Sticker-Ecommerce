"""Blog Orchestrator for multi-agent blog generation.

Manages the two-phase pipeline:
  Phase 1: Planner <-> Plan Reviewer loop  (content-image coherence)
  Phase 2: Writer  <-> Reviewer loop       (article quality)
with human-in-the-loop decision points between each cycle.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Literal

from src.core.logger import get_logger
from src.models.blog import (
    BlogInput, BlogDraft, ReviewResult,
    ContentPlan, PlanReviewResult,
)
from src.services.blog.writer_agent import WriterAgent
from src.services.blog.reviewer_agent import ReviewerAgent
from src.services.blog.planner_agent import PlannerAgent
from src.services.blog.plan_reviewer_agent import PlanReviewerAgent
from src.services.blog.blog_image_generator import BlogImageGenerator
from src.services.blog.shopify_converter import ShopifyConverter
from src.services.blog.shopify_publisher import ShopifyPublisher
from src.utils.file_utils import ensure_dir, save_json
from src.utils.text_utils import sanitize_filename

logger = get_logger("orchestrator")

UserDecision = Literal["auto_revise", "accept", "abort"]


class BlogOrchestrator:
    """Orchestrates the Planner -> Writer pipeline with review loops."""

    def __init__(
        self,
        writer: WriterAgent,
        reviewer: ReviewerAgent,
        planner: Optional[PlannerAgent] = None,
        plan_reviewer: Optional[PlanReviewerAgent] = None,
        max_iterations: int = 5,
        max_plan_iterations: int = 3,
        pass_threshold: float = 80.0,
        plan_pass_threshold: int = 70,
        output_dir: str = "./output/blogs",
        image_generator: Optional[BlogImageGenerator] = None,
        store_url: str = "https://your-store.myshopify.com",
        publisher: Optional[ShopifyPublisher] = None,
        publish_live: bool = False,
    ):
        self.writer = writer
        self.reviewer = reviewer
        self.planner = planner
        self.plan_reviewer = plan_reviewer
        self.max_iterations = max_iterations
        self.max_plan_iterations = max_plan_iterations
        self.pass_threshold = pass_threshold
        self.plan_pass_threshold = plan_pass_threshold
        self.output_dir = Path(output_dir)
        self.image_generator = image_generator
        self.shopify_converter = ShopifyConverter(store_url=store_url)
        self.publisher = publisher
        self.publish_live = publish_live
        self.session_history: list[dict] = []
        self._on_content_ready = None
        self._on_image_ready = None

    async def run(
        self,
        blog_input: BlogInput,
        on_review: Callable[[BlogDraft, ReviewResult, int], UserDecision],
        progress_callback: Optional[Callable[[str], None]] = None,
        on_content_ready: Optional[Callable[[str, any], None]] = None,
        on_image_ready: Optional[Callable] = None,
    ) -> Optional[BlogDraft]:
        """Run the full two-phase pipeline.

        Args:
            blog_input: Topic + keywords.
            on_review: Callback invoked after each review.
            progress_callback: Optional progress updates callback.
            on_content_ready: Optional callback(event_type, data) fired at
                key milestones: "plan_ready", "draft_ready", "finalized".
            on_image_ready: Optional callback(path, alt_text, prompt, index)
                forwarded to BlogImageGenerator.

        Returns:
            The accepted BlogDraft, or None if aborted.
        """
        self._on_content_ready = on_content_ready
        self._on_image_ready = on_image_ready

        content_plan: Optional[ContentPlan] = None
        if self.planner and self.plan_reviewer:
            content_plan = await self._run_plan_loop(
                blog_input, progress_callback
            )
            if content_plan is None:
                self._progress(progress_callback, "规划阶段未产出方案，继续直接撰写。")
            else:
                self._fire_content("plan_ready", content_plan)

        return await self._run_write_loop(
            blog_input, content_plan, on_review, progress_callback
        )

    async def _run_plan_loop(
        self,
        blog_input: BlogInput,
        progress_callback: Optional[Callable[[str], None]],
    ) -> Optional[ContentPlan]:
        """Phase 1: Planner <-> Plan Reviewer iteration loop."""
        self._progress(progress_callback, "正在规划文章结构...")

        plan: Optional[ContentPlan] = None
        review: Optional[PlanReviewResult] = None

        for iteration in range(1, self.max_plan_iterations + 1):
            if plan is None:
                plan = await self.planner.plan(blog_input, progress_callback)
            else:
                plan = await self.planner.revise(
                    blog_input, plan, review, progress_callback
                )

            review = await self.plan_reviewer.review(
                plan, blog_input, self.plan_pass_threshold, progress_callback
            )

            self.session_history.append({
                "phase": "planning",
                "iteration": iteration,
                "plan": plan.model_dump(),
                "plan_review": review.model_dump(),
                "timestamp": datetime.now().isoformat(),
            })

            if review.passed:
                logger.info(f"Plan approved at iteration {iteration} ({review.overall_score:.0f}/100)")
                return plan

            if iteration >= self.max_plan_iterations:
                logger.info(f"Plan max iterations reached. Using best plan ({review.overall_score:.0f}/100).")
                return plan

        return plan

    async def _run_write_loop(
        self,
        blog_input: BlogInput,
        content_plan: Optional[ContentPlan],
        on_review: Callable[[BlogDraft, ReviewResult, int], UserDecision],
        progress_callback: Optional[Callable[[str], None]],
    ) -> Optional[BlogDraft]:
        """Phase 2: Writer <-> Reviewer iteration loop."""
        self._progress(progress_callback, "正在撰写文章...")

        draft: Optional[BlogDraft] = None
        review: Optional[ReviewResult] = None

        for iteration in range(1, self.max_iterations + 1):
            if draft is None:
                draft = await self.writer.generate(
                    blog_input, content_plan=content_plan,
                    progress_callback=progress_callback,
                )
            else:
                draft = await self.writer.revise(
                    blog_input, draft, review, progress_callback
                )

            review = await self.reviewer.review(
                draft, blog_input, self.pass_threshold, progress_callback
            )

            self.session_history.append({
                "phase": "writing",
                "iteration": iteration,
                "draft": draft.model_dump(),
                "review": review.model_dump(),
                "timestamp": datetime.now().isoformat(),
            })

            decision = on_review(draft, review, iteration)

            if decision == "accept":
                logger.info(f"Draft accepted at iteration {iteration}")
                self._fire_content("draft_ready", draft)
                draft = await self._finalize(
                    draft, blog_input, content_plan, progress_callback
                )
                return draft

            if decision == "abort":
                logger.info("Generation aborted by user")
                self._progress(progress_callback, "已取消生成。")
                return None

            if iteration >= self.max_iterations:
                logger.warning(f"Max iterations ({self.max_iterations}) reached")
                self._fire_content("draft_ready", draft)
                draft = await self._finalize(
                    draft, blog_input, content_plan, progress_callback
                )
                return draft

        return draft

    async def _finalize(
        self,
        draft: BlogDraft,
        blog_input: BlogInput,
        content_plan: Optional[ContentPlan],
        progress_callback: Optional[Callable[[str], None]],
    ) -> BlogDraft:
        """Generate images, save markdown, produce paste-ready HTML, and optionally publish."""
        slug = sanitize_filename(draft.url_slug.strip("/") or blog_input.topic)[:50]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        images_dir = None
        image_records = []
        if self.image_generator:
            images_dir = self.output_dir / "images" / f"{slug}_{timestamp}"

            refined_prompts = None
            if content_plan:
                refined_prompts = self.image_generator.build_refined_prompts_from_plan(
                    content=draft.content,
                    content_plan=content_plan,
                )

            updated_content, image_records = await self.image_generator.generate_images(
                content=draft.content,
                images_dir=images_dir,
                refined_prompts=refined_prompts,
                progress_callback=progress_callback,
                on_image_ready=self._on_image_ready,
            )
            draft = draft.model_copy(update={"content": updated_content})

        saved_path = self._save_final(draft, blog_input, slug, timestamp)
        self._save_session(blog_input)

        html_path = self._save_paste_ready_html(draft, blog_input, slug, timestamp)
        logger.info(f"Saved markdown to {saved_path}")
        logger.info(f"Paste-ready HTML -> {html_path}")

        if self.publisher:
            publish_result = self._publish_to_shopify(
                draft, blog_input, images_dir, progress_callback
            )
            if publish_result:
                draft = draft.model_copy(
                    update={"shopify_article_id": publish_result.get("id")}
                )

        self._fire_content("finalized", {
            "draft": draft,
            "images_dir": str(images_dir) if images_dir else None,
            "image_records": image_records,
            "markdown_path": saved_path,
        })

        return draft

    def _publish_to_shopify(
        self,
        draft: BlogDraft,
        blog_input: BlogInput,
        images_dir: Optional[Path],
        progress_callback: Optional[Callable[[str], None]],
    ) -> Optional[dict]:
        """Upload images and create the article on Shopify."""
        logger.info("Publishing to Shopify...")

        article = self.shopify_converter.convert_draft(
            content_md=draft.content,
            meta_title=draft.meta_title,
            meta_description=draft.meta_description,
            url_slug=draft.url_slug,
            keywords=blog_input.seo_keywords,
        )

        try:
            result = self.publisher.publish(
                article=article,
                images_dir=images_dir,
                published=self.publish_live,
            )
            article_id = result.get("id")
            handle = result.get("handle")
            admin_url = f"https://{self.publisher.shop_domain}/admin/articles/{article_id}"
            mode = "LIVE" if self.publish_live else "draft"
            self._progress(
                progress_callback,
                f"Published to Shopify ({mode}): {admin_url}",
            )
            logger.info(f"Shopify article created: id={article_id}, handle={handle}")
            return result
        except Exception as e:
            logger.error(f"Shopify publish failed: {e}", exc_info=True)
            self._progress(
                progress_callback,
                f"Shopify publish failed: {e} (local files saved successfully)",
            )
            return None

    def _save_final(
        self,
        draft: BlogDraft,
        blog_input: BlogInput,
        slug: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> str:
        """Save the final blog draft as markdown."""
        ensure_dir(str(self.output_dir))

        if slug is None:
            slug = sanitize_filename(draft.url_slug.strip("/") or blog_input.topic)[:50]
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{slug}_{timestamp}.md"
        filepath = self.output_dir / filename

        header = (
            f"<!--\n"
            f"Title: {draft.meta_title}\n"
            f"Description: {draft.meta_description}\n"
            f"URL Slug: {draft.url_slug}\n"
            f"Keywords: {', '.join(blog_input.seo_keywords)}\n"
            f"Generated: {datetime.now().isoformat()}\n"
            f"Iteration: {draft.iteration}\n"
            f"-->\n\n"
        )

        filepath.write_text(header + draft.content, encoding="utf-8")
        logger.info(f"Saved blog to {filepath}")
        return str(filepath)

    def _save_paste_ready_html(
        self,
        draft: BlogDraft,
        blog_input: BlogInput,
        slug: str,
        timestamp: str,
    ) -> str:
        """Convert the final draft to a paste-ready HTML file."""
        content_md = self._fix_image_paths_for_html(draft.content)

        article = self.shopify_converter.convert_draft(
            content_md=content_md,
            meta_title=draft.meta_title,
            meta_description=draft.meta_description,
            url_slug=draft.url_slug,
            keywords=blog_input.seo_keywords,
        )

        shopify_dir = self.output_dir / "shopify_ready"
        html_filename = f"{slug}_{timestamp}.html"
        html_path = shopify_dir / html_filename

        saved = self.shopify_converter.generate_paste_ready_html(
            article=article,
            output_path=str(html_path),
        )

        logger.info(f"Paste-ready HTML saved to {saved}")
        return saved

    @staticmethod
    def _fix_image_paths_for_html(content: str) -> str:
        """Rewrite image paths so they resolve from shopify_ready/ to images/.

        Markdown images use paths relative to output/blogs/ (e.g.
        ``slug/image.png``). The paste-ready HTML lives one level
        deeper in ``shopify_ready/``, so we prepend ``../images/``.
        """
        import re
        def replacer(m: re.Match) -> str:
            alt, src = m.group(1), m.group(2)
            if src.startswith(("http://", "https://", "../")):
                return m.group(0)
            return f"![{alt}](../images/{src})"
        return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replacer, content)

    def _save_session(self, blog_input: BlogInput):
        """Save the full session history for debugging."""
        sessions_dir = self.output_dir / "sessions"
        ensure_dir(str(sessions_dir))

        slug = sanitize_filename(blog_input.topic)[:30]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_file = sessions_dir / f"session_{slug}_{timestamp}.json"

        save_json(
            {
                "input": blog_input.model_dump(),
                "history": self.session_history,
            },
            str(session_file),
        )
        logger.info(f"Session saved to {session_file}")

    def _fire_content(self, event_type: str, data):
        """Fire on_content_ready callback if registered."""
        cb = getattr(self, "_on_content_ready", None)
        if cb:
            try:
                cb(event_type, data)
            except Exception as e:
                logger.warning(f"on_content_ready callback error ({event_type}): {e}")

    @staticmethod
    def _progress(callback: Optional[Callable], message: str):
        if callback:
            callback(message)
        logger.info(message)
