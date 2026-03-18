"""Reviewer Agent for multi-agent blog generation.

Reviews blog drafts across 5 dimensions specific to sticker e-commerce,
outputting structured JSON scores and actionable feedback.
"""

import asyncio
from typing import Optional, Callable

from src.core.logger import get_logger
from src.models.blog import (
    BusinessProfile,
    BlogInput,
    BlogDraft,
    ReviewDimension,
    ReviewResult,
)
from src.services.blog import blog_prompts

logger = get_logger("agent.reviewer")

REVIEW_DIMENSIONS_SPEC = [
    {"name": "SEO & Search Intent", "weight": 0.25},
    {"name": "Content Depth & Voice", "weight": 0.25},
    {"name": "Topic-Product Fit", "weight": 0.20},
    {"name": "Conversion Architecture", "weight": 0.15},
    {"name": "Format & Readability", "weight": 0.15},
]


class ReviewerAgent:
    """Reviews blog drafts with structured 5-dimension scoring."""

    def __init__(self, llm_service, business_profile: BusinessProfile):
        """
        Args:
            llm_service: An LLM service with generate_json() method
                         (ClaudeService or GeminiService).
            business_profile: Shared business context for review criteria.
        """
        self.llm = llm_service
        self.profile = business_profile
        self._system_prompt = blog_prompts.build_reviewer_system_prompt(self.profile)

    async def review(
        self,
        draft: BlogDraft,
        blog_input: BlogInput,
        pass_threshold: float = 80.0,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> ReviewResult:
        """Review a blog draft and return structured scores.

        Args:
            draft: The blog draft to review.
            blog_input: Original topic + keywords for context.
            pass_threshold: Score threshold for passing (default 80).
            progress_callback: Optional callback for progress updates.

        Returns:
            ReviewResult with dimension scores and overall assessment.
        """
        self._progress(progress_callback, "Reviewing draft...")

        user_prompt = blog_prompts.format_reviewer_prompt(
            topic=blog_input.topic,
            seo_keywords=blog_input.seo_keywords,
            draft=draft,
        )

        review_data = await self._call_llm_json(user_prompt)
        result = self._parse_review(review_data, pass_threshold)

        self._progress(
            progress_callback,
            f"Review complete: {result.overall_score:.0f}/100 "
            f"({'PASSED' if result.passed else 'NEEDS REVISION'})",
        )
        return result

    async def _call_llm_json(self, user_prompt: str) -> dict:
        """Call the LLM service for JSON output in a thread-safe way."""
        return await asyncio.to_thread(
            self.llm.generate_json,
            prompt=user_prompt,
            system=self._system_prompt,
            max_tokens=4096,
            temperature=0.3,
        )

    def _parse_review(self, data: dict, pass_threshold: float) -> ReviewResult:
        """Parse LLM JSON output into a validated ReviewResult."""
        dimensions = []
        raw_dims = data.get("dimensions", [])

        for i, spec in enumerate(REVIEW_DIMENSIONS_SPEC):
            if i < len(raw_dims):
                raw = raw_dims[i]
                score = max(1, min(10, int(raw.get("score", 5))))
                dim = ReviewDimension(
                    name=raw.get("name", spec["name"]),
                    score=score,
                    weight=spec["weight"],
                    issues=raw.get("issues", [])[:5],
                    suggestions=raw.get("suggestions", [])[:5],
                )
            else:
                dim = ReviewDimension(
                    name=spec["name"],
                    score=5,
                    weight=spec["weight"],
                    issues=["Dimension not evaluated by reviewer"],
                    suggestions=[],
                )
            dimensions.append(dim)

        overall = sum(d.score * d.weight * 10 for d in dimensions)
        summary = data.get("summary", "Review completed.")

        return ReviewResult(
            dimensions=dimensions,
            overall_score=round(overall, 1),
            summary=summary,
            passed=overall >= pass_threshold,
        )

    @staticmethod
    def _progress(callback: Optional[Callable], message: str):
        if callback:
            callback(message)
        logger.info(message)
