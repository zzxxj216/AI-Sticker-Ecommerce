"""Blog Orchestrator for multi-agent blog generation.

Manages the Writer <-> Reviewer iteration loop with human-in-the-loop
decision points between each cycle.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Literal

from src.core.logger import get_logger
from src.models.blog import BlogInput, BlogDraft, ReviewResult
from src.services.blog.writer_agent import WriterAgent
from src.services.blog.reviewer_agent import ReviewerAgent
from src.services.blog.blog_image_generator import BlogImageGenerator
from src.services.blog.shopify_converter import ShopifyConverter
from src.utils.file_utils import ensure_dir, save_json
from src.utils.text_utils import sanitize_filename

logger = get_logger("orchestrator")

UserDecision = Literal["auto_revise", "accept", "abort"]


class BlogOrchestrator:
    """Orchestrates the Writer <-> Reviewer iteration loop."""

    def __init__(
        self,
        writer: WriterAgent,
        reviewer: ReviewerAgent,
        max_iterations: int = 5,
        pass_threshold: float = 80.0,
        output_dir: str = "./output/blogs",
        image_generator: Optional[BlogImageGenerator] = None,
        store_url: str = "https://your-store.myshopify.com",
    ):
        self.writer = writer
        self.reviewer = reviewer
        self.max_iterations = max_iterations
        self.pass_threshold = pass_threshold
        self.output_dir = Path(output_dir)
        self.image_generator = image_generator
        self.shopify_converter = ShopifyConverter(store_url=store_url)
        self.session_history: list[dict] = []

    async def run(
        self,
        blog_input: BlogInput,
        on_review: Callable[[BlogDraft, ReviewResult, int], UserDecision],
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[BlogDraft]:
        """Run the full generation + review loop.

        Args:
            blog_input: Topic + keywords.
            on_review: Callback invoked after each review. Receives
                       (draft, review_result, iteration) and must return
                       a UserDecision: "auto_revise", "accept", or "abort".
            progress_callback: Optional progress updates callback.

        Returns:
            The accepted BlogDraft, or None if aborted.
        """
        draft: Optional[BlogDraft] = None
        review: Optional[ReviewResult] = None

        for iteration in range(1, self.max_iterations + 1):
            # --- Generate or Revise ---
            if draft is None:
                draft = await self.writer.generate(blog_input, progress_callback)
            else:
                draft = await self.writer.revise(
                    blog_input, draft, review, progress_callback
                )

            # --- Review ---
            review = await self.reviewer.review(
                draft, blog_input, self.pass_threshold, progress_callback
            )

            self.session_history.append({
                "iteration": iteration,
                "draft": draft.model_dump(),
                "review": review.model_dump(),
                "timestamp": datetime.now().isoformat(),
            })

            # --- Human Decision ---
            decision = on_review(draft, review, iteration)

            if decision == "accept":
                logger.info(f"Draft accepted at iteration {iteration}")
                draft = await self._finalize(draft, blog_input, progress_callback)
                return draft

            if decision == "abort":
                logger.info("Generation aborted by user")
                self._progress(progress_callback, "Aborted.")
                return None

            if iteration >= self.max_iterations:
                logger.warning(f"Max iterations ({self.max_iterations}) reached")
                self._progress(
                    progress_callback,
                    f"Max iterations reached. Last score: {review.overall_score}/100",
                )
                draft = await self._finalize(draft, blog_input, progress_callback)
                return draft

        return draft

    async def _finalize(
        self,
        draft: BlogDraft,
        blog_input: BlogInput,
        progress_callback: Optional[Callable[[str], None]],
    ) -> BlogDraft:
        """Generate images, save markdown, and produce paste-ready HTML."""
        slug = sanitize_filename(draft.url_slug.strip("/") or blog_input.topic)[:50]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if self.image_generator:
            images_dir = self.output_dir / "images" / f"{slug}_{timestamp}"
            updated_content = await self.image_generator.generate_images(
                content=draft.content,
                images_dir=images_dir,
                progress_callback=progress_callback,
            )
            draft = draft.model_copy(update={"content": updated_content})

        saved_path = self._save_final(draft, blog_input, slug, timestamp)
        self._save_session(blog_input)

        html_path = self._save_paste_ready_html(draft, blog_input, slug, timestamp)
        self._progress(progress_callback, f"Saved markdown to {saved_path}")
        self._progress(progress_callback, f"Paste-ready HTML -> {html_path}")
        return draft

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

    @staticmethod
    def _progress(callback: Optional[Callable], message: str):
        if callback:
            callback(message)
        logger.info(message)
