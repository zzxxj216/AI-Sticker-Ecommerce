"""Plan Reviewer Agent for multi-agent blog generation.

Reviews ContentPlans across 3 dimensions (coherence, coverage, specificity)
to ensure images and content are tightly coupled before writing begins.
"""

import asyncio
from typing import Optional, Callable

from src.core.logger import get_logger
from src.models.blog import (
    BusinessProfile,
    BlogInput,
    ContentPlan,
    PlanReviewResult,
)
from src.services.blog import blog_prompts

logger = get_logger("agent.plan_reviewer")

COHERENCE_WEIGHT = 0.40
COVERAGE_WEIGHT = 0.30
SPECIFICITY_WEIGHT = 0.30


class PlanReviewerAgent:
    """Reviews content plans for content-image coherence."""

    def __init__(self, llm_service, business_profile: BusinessProfile):
        self.llm = llm_service
        self.profile = business_profile
        self._system_prompt = blog_prompts.build_plan_reviewer_system_prompt(
            self.profile
        )

    async def review(
        self,
        plan: ContentPlan,
        blog_input: BlogInput,
        pass_threshold: int = 70,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> PlanReviewResult:
        """Review a content plan for content-image coherence.

        Args:
            plan: The content plan to review.
            blog_input: Original topic + keywords for context.
            pass_threshold: Score threshold (0-100) for passing.
            progress_callback: Optional callback for progress updates.

        Returns:
            PlanReviewResult with scores and feedback.
        """
        logger.info("Reviewing content plan...")

        user_prompt = blog_prompts.format_plan_reviewer_prompt(
            plan=plan,
            topic=blog_input.topic,
            seo_keywords=blog_input.seo_keywords,
            threshold=pass_threshold,
        )

        data = await self._call_llm_json(user_prompt)
        result = self._parse_review(data, pass_threshold)

        logger.info(
            f"Plan review: {result.overall_score:.0f}/100 "
            f"(C={result.coherence_score} Cv={result.coverage_score} "
            f"S={result.specificity_score}) "
            f"{'PASSED' if result.passed else 'NEEDS REVISION'}"
        )
        return result

    async def _call_llm_json(self, user_prompt: str) -> dict:
        return await asyncio.to_thread(
            self.llm.generate_json,
            prompt=user_prompt,
            system=self._system_prompt,
            max_tokens=4096,
            temperature=0.3,
        )

    def _parse_review(self, data: dict, pass_threshold: int) -> PlanReviewResult:
        """Parse LLM JSON output into a validated PlanReviewResult."""
        coherence = max(1, min(10, int(data.get("coherence_score", 5))))
        coverage = max(1, min(10, int(data.get("coverage_score", 5))))
        specificity = max(1, min(10, int(data.get("specificity_score", 5))))

        overall = (
            coherence * COHERENCE_WEIGHT
            + coverage * COVERAGE_WEIGHT
            + specificity * SPECIFICITY_WEIGHT
        ) * 10

        issues = data.get("issues", [])[:10]
        suggestions = data.get("suggestions", [])[:10]
        summary = data.get("summary", "Plan reviewed.")

        return PlanReviewResult(
            coherence_score=coherence,
            coverage_score=coverage,
            specificity_score=specificity,
            overall_score=round(overall, 1),
            issues=issues,
            suggestions=suggestions,
            passed=overall >= pass_threshold,
            summary=summary,
        )

    @staticmethod
    def _progress(callback: Optional[Callable], message: str):
        if callback:
            callback(message)
        logger.info(message)
