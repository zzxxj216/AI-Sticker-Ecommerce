"""Batch sticker generation module.

This package contains two pipeline systems and supporting utilities:

Multi-agent Pipeline (newer, used by scripts/batch_run.py):
    - StickerPackPipeline  — orchestrates planner → designer → prompt builder → image gen
    - sticker_prompts      — system prompts, prompt builders, and parsers for each agent

Legacy Feishu Pipeline (used by Feishu bot):
    - BatchPipeline  — plan → content → ideas → previews → images
    - BatchPlanner   — topic/concept planning
    - BatchSession   — conversational session wrapping BatchPipeline
    - batch_prompts  — prompt templates for legacy pipeline

Evaluation & QC:
    - planner_plan          — thin wrapper for running planner + scoring
    - planner_rubric        — scoring dimensions and weights
    - planner_eval_analysis — anti-templating heuristics, batch report

Image Generation:
    - image_generation      — prompts and parsers for image gen + QC steps

Trend Brief:
    - trend_brief_schema    — field validation for trend brief Markdown
"""

# --- Multi-agent pipeline (primary) ---
from src.services.batch.sticker_pipeline import StickerPackPipeline

# --- Legacy Feishu pipeline ---
from src.services.batch.batch_planner import BatchPlanner
from src.services.batch.batch_pipeline import BatchPipeline
from src.services.batch.batch_session import BatchSession, BatchSessionResponse

__all__ = [
    # Multi-agent pipeline
    "StickerPackPipeline",
    # Legacy pipeline
    "BatchPlanner",
    "BatchPipeline",
    "BatchSession",
    "BatchSessionResponse",
]
