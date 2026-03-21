"""Writer Agent for multi-agent blog generation.

Generates SEO blog drafts based on BusinessProfile, and revises
them based on ReviewerAgent feedback.
"""

import asyncio
import re
from typing import Optional, Callable

from src.core.logger import get_logger
from src.models.blog import (
    BusinessProfile,
    BlogInput,
    BlogDraft,
    ReviewResult,
    ContentPlan,
)
from src.services.blog import blog_prompts

logger = get_logger("agent.writer")


class WriterAgent:
    """Generates and revises blog drafts driven by BusinessProfile."""

    def __init__(self, llm_service, business_profile: BusinessProfile):
        """
        Args:
            llm_service: An LLM service with generate() and generate_json() methods
                         (ClaudeService or GeminiService).
            business_profile: Shared business context for prompt injection.
        """
        self.llm = llm_service
        self.profile = business_profile
        self._system_prompt = blog_prompts.build_writer_system_prompt(self.profile)

    async def generate(
        self,
        blog_input: BlogInput,
        content_plan: Optional[ContentPlan] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> BlogDraft:
        """Generate a new blog draft from scratch.

        Args:
            blog_input: Topic + keywords from trend research.
            content_plan: Optional approved content plan to follow.
            progress_callback: Optional callback for progress updates.

        Returns:
            BlogDraft with metadata and full markdown content.
        """
        if content_plan:
            self._progress(progress_callback, "Generating blog draft from approved plan...")
            user_prompt = blog_prompts.format_writer_generate_with_plan_prompt(
                topic=blog_input.topic,
                seo_keywords=blog_input.seo_keywords,
                plan=content_plan,
                language=blog_input.language,
                additional_instructions=blog_input.additional_instructions,
            )
        else:
            self._progress(progress_callback, "Generating blog draft...")
            user_prompt = blog_prompts.format_writer_generate_prompt(
                topic=blog_input.topic,
                seo_keywords=blog_input.seo_keywords,
                language=blog_input.language,
                additional_instructions=blog_input.additional_instructions,
            )

        raw_text = await self._call_llm(user_prompt)
        draft = self._parse_draft(raw_text, iteration=1)

        self._progress(progress_callback, f"Draft #1 ready ({self._word_count(draft.content)} words)")
        return draft

    async def revise(
        self,
        blog_input: BlogInput,
        previous_draft: BlogDraft,
        review: ReviewResult,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> BlogDraft:
        """Revise a draft based on reviewer feedback.

        Args:
            blog_input: Original topic + keywords.
            previous_draft: The draft that was reviewed.
            review: Structured review result with issues and suggestions.
            progress_callback: Optional callback for progress updates.

        Returns:
            Revised BlogDraft with incremented iteration number.
        """
        suggestion_count = sum(len(d.suggestions) for d in review.dimensions)
        self._progress(
            progress_callback,
            f"Revising based on {suggestion_count} suggestions...",
        )

        user_prompt = blog_prompts.format_writer_revise_prompt(
            topic=blog_input.topic,
            seo_keywords=blog_input.seo_keywords,
            previous_draft=previous_draft,
            review=review,
        )

        raw_text = await self._call_llm(user_prompt)
        new_iteration = previous_draft.iteration + 1
        draft = self._parse_draft(raw_text, iteration=new_iteration)

        self._progress(
            progress_callback,
            f"Draft #{new_iteration} ready ({self._word_count(draft.content)} words)",
        )
        return draft

    async def _call_llm(self, user_prompt: str) -> str:
        """Call the LLM service in a thread-safe way."""
        result = await asyncio.to_thread(
            self.llm.generate,
            prompt=user_prompt,
            system=self._system_prompt,
            max_tokens=8192,
            temperature=0.8,
        )
        return result["text"]

    def _parse_draft(self, raw_text: str, iteration: int) -> BlogDraft:
        """Parse LLM output into a structured BlogDraft."""
        meta_title = self._extract_field(raw_text, "META_TITLE") or "Untitled"
        meta_description = self._extract_field(raw_text, "META_DESCRIPTION") or ""
        url_slug = self._extract_field(raw_text, "URL_SLUG") or "/untitled"

        separator_match = re.search(r"\n---\n", raw_text)
        if separator_match:
            content = raw_text[separator_match.end():].strip()
        else:
            lines = raw_text.split("\n")
            content_start = 0
            for i, line in enumerate(lines):
                if line.startswith("# "):
                    content_start = i
                    break
            content = "\n".join(lines[content_start:]).strip()

        if not content:
            content = raw_text

        return BlogDraft(
            meta_title=meta_title[:60],
            meta_description=meta_description[:160],
            url_slug=url_slug,
            content=content,
            iteration=iteration,
        )

    @staticmethod
    def _extract_field(text: str, field_name: str) -> Optional[str]:
        """Extract a metadata field value like META_TITLE: value."""
        pattern = rf"^{field_name}:\s*(.+)$"
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _word_count(text: str) -> int:
        return len(text.split())

    @staticmethod
    def _progress(callback: Optional[Callable], message: str):
        if callback:
            callback(message)
        logger.info(message)
