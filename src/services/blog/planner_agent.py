"""Planner Agent for multi-agent blog generation.

Generates a ContentPlan (article outline + image plans) to ensure
content and images are designed together from the start. Revises
plans based on PlanReviewerAgent feedback.
"""

import asyncio
from typing import Optional, Callable

from src.core.logger import get_logger
from src.models.blog import (
    BusinessProfile,
    BlogInput,
    ContentPlan,
    ImagePlan,
    PlanReviewResult,
)
from src.services.blog import blog_prompts

logger = get_logger("agent.planner")


class PlannerAgent:
    """Generates and revises content plans with tightly-coupled image descriptions."""

    def __init__(self, llm_service, business_profile: BusinessProfile):
        self.llm = llm_service
        self.profile = business_profile
        self._system_prompt = blog_prompts.build_planner_system_prompt(self.profile)

    async def plan(
        self,
        blog_input: BlogInput,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> ContentPlan:
        """Generate a new content plan from scratch.

        Returns:
            ContentPlan with outline structure and image plans.
        """
        self._progress(progress_callback, "Planning article structure and images...")

        user_prompt = blog_prompts.format_planner_generate_prompt(
            topic=blog_input.topic,
            seo_keywords=blog_input.seo_keywords,
            language=blog_input.language,
            additional_instructions=blog_input.additional_instructions,
        )

        data = await self._call_llm_json(user_prompt)
        content_plan = self._parse_plan(data, iteration=1)

        n_sections = len(content_plan.outline)
        n_images = len(content_plan.image_plans)
        self._progress(
            progress_callback,
            f"Plan #1 ready ({n_sections} sections, {n_images} images)",
        )
        return content_plan

    async def revise(
        self,
        blog_input: BlogInput,
        plan: ContentPlan,
        review: PlanReviewResult,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> ContentPlan:
        """Revise a plan based on plan reviewer feedback.

        Returns:
            Revised ContentPlan with incremented iteration.
        """
        self._progress(
            progress_callback,
            f"Revising plan (was {review.overall_score:.0f}/100)...",
        )

        user_prompt = blog_prompts.format_planner_revise_prompt(
            plan=plan,
            review=review,
            topic=blog_input.topic,
            seo_keywords=blog_input.seo_keywords,
        )

        data = await self._call_llm_json(user_prompt)
        new_iteration = plan.iteration + 1
        content_plan = self._parse_plan(data, iteration=new_iteration)

        n_images = len(content_plan.image_plans)
        self._progress(
            progress_callback,
            f"Plan #{new_iteration} ready ({n_images} images)",
        )
        return content_plan

    async def _call_llm_json(self, user_prompt: str) -> dict:
        return await asyncio.to_thread(
            self.llm.generate_json,
            prompt=user_prompt,
            system=self._system_prompt,
            max_tokens=4096,
            temperature=0.7,
        )

    def _parse_plan(self, data: dict, iteration: int) -> ContentPlan:
        """Parse LLM JSON output into a validated ContentPlan."""
        outline = data.get("outline", [])

        raw_images = data.get("image_plans", [])
        image_plans = []
        for img in raw_images:
            image_plans.append(ImagePlan(
                section_title=img.get("section_title", ""),
                placement=img.get("placement", ""),
                description=img.get("description", ""),
                alt_text=img.get("alt_text", ""),
                content_connection=img.get("content_connection", ""),
            ))

        target_word_count = int(data.get("target_word_count", 2200))
        seo_strategy = data.get("seo_strategy", "")

        return ContentPlan(
            outline=outline,
            image_plans=image_plans,
            target_word_count=target_word_count,
            seo_strategy=seo_strategy,
            iteration=iteration,
        )

    @staticmethod
    def _progress(callback: Optional[Callable], message: str):
        if callback:
            callback(message)
        logger.info(message)
