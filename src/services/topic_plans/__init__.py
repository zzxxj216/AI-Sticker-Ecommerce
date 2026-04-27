"""Topic plan service — A.2 of the V2 pipeline.

Two-step generation:
  Step 1 (gpt-5.4-pro)  → free-form markdown plan (creative, no JSON pressure)
  Step 2 (gpt-4o-mini)  → structured JSON extracted from the markdown
"""

from src.services.topic_plans.service import (
    TopicPlanService,
    get_topic_plan_service,
)

__all__ = ["TopicPlanService", "get_topic_plan_service"]
