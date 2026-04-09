"""Video Plan Service — Stage 1: generate a script plan.

Given a sticker pack context and a video type combo, produces a
structured plan that decides type ordering, shot allocation, and intent.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import yaml

from src.core.config import config
from src.core.logger import get_logger
from src.services.ai.openai_service import OpenAIService
from src.services.ops.db import OpsDatabase
from src.services.video.video_type_service import VideoTypeService
from src.services.video.video_combo_service import VideoComboService
from src.services.video.video_prompt_builder import VideoPromptBuilder, PLAN_SYSTEM_PROMPT

logger = get_logger("service.video.plan")

_STORE_PROFILE_PATH = Path("config/store_profile.yaml")


class VideoPlanService:
    """Stage 1: generate a script plan from a combo + context."""

    def __init__(
        self,
        db: OpsDatabase,
        openai_service: OpenAIService | None = None,
    ):
        self.db = db
        self.openai = openai_service or OpenAIService()
        self.type_service = VideoTypeService(db)
        self.combo_service = VideoComboService(db)
        self._brand_profile: dict[str, Any] | None = None

    @property
    def brand_profile(self) -> dict[str, Any]:
        if self._brand_profile is None:
            self._brand_profile = self._load_brand_profile()
        return self._brand_profile

    @staticmethod
    def _load_brand_profile() -> dict[str, Any]:
        try:
            if _STORE_PROFILE_PATH.exists():
                return yaml.safe_load(_STORE_PROFILE_PATH.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("Failed to load store_profile.yaml: %s", e)
        return {}

    def generate_plan(
        self,
        combo_id: str,
        script_input: dict[str, Any],
        *,
        job_id: str = "",
        created_by: str = "system",
    ) -> dict[str, Any]:
        """Generate a script plan for (combo, input).

        Args:
            combo_id: the video_type_combo to use
            script_input: VideoScriptInput-like dict assembled from pipeline assets
            job_id: optional link to a generation job
            created_by: who triggered this

        Returns:
            The saved plan record dict.
        """
        combo = self.combo_service.get_combo(combo_id)
        if not combo:
            raise ValueError(f"Combo not found: {combo_id}")

        type_configs = self.type_service.get_types_for_combo(combo.get("selected_types", []))
        if not type_configs:
            raise ValueError(f"No active types found for combo: {combo_id}")

        user_prompt = VideoPromptBuilder.build_plan_prompt(
            script_input=script_input,
            combo=combo,
            type_configs=type_configs,
            brand_profile=self.brand_profile,
        )

        logger.info("Generating video plan: combo=%s job=%s", combo_id, job_id)

        result = self.openai.generate(
            prompt=user_prompt,
            system=PLAN_SYSTEM_PROMPT,
            temperature=0.7,
        )
        raw_text = result.get("text", "")
        plan_json = _parse_json(raw_text)

        plan_json = self._validate_plan(plan_json, combo)

        plan_id = f"vsp_{uuid.uuid4().hex[:12]}"
        family_id = script_input.get("family_id", "")
        record = {
            "id": plan_id,
            "combo_id": combo_id,
            "job_id": job_id,
            "family_id": family_id,
            "design_id": script_input.get("design_id", ""),
            "pack_id": script_input.get("pack_id", ""),
            "selected_types": combo.get("selected_types", []),
            "input_snapshot": script_input,
            "plan": plan_json,
            "status": "completed",
            "created_by": created_by,
        }
        self.db.insert_video_script_plan_v2(record)

        logger.info("Video plan saved: %s (combo=%s)", plan_id, combo_id)
        return self.db.get_video_script_plan_v2(plan_id)

    def _validate_plan(self, plan: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        """Light post-generation validation and auto-fix."""
        issues: list[str] = []

        dr = combo.get("duration_range", {})
        dur = plan.get("total_duration_sec", 0)
        if dur < dr.get("min", 0) or dur > dr.get("max", 999):
            clamped = max(dr.get("min", 7), min(dr.get("max", 12), dur))
            plan["total_duration_sec"] = clamped
            issues.append(f"duration_clamped:{dur}->{clamped}")

        sr = combo.get("shot_count_range", {})
        shots = plan.get("shot_plan", [])
        if len(shots) < sr.get("min", 0) or len(shots) > sr.get("max", 999):
            issues.append(f"shot_count_out_of_range:{len(shots)}")

        allowed_types = set(combo.get("selected_types", []))
        plan_types = set(plan.get("type_order", []))
        extra = plan_types - allowed_types
        if extra:
            issues.append(f"unexpected_types:{extra}")

        if issues:
            plan["_validation_issues"] = issues
            logger.warning("Plan validation issues: %s", issues)

        return plan


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        last_fence = cleaned.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            cleaned = cleaned[first_nl + 1:last_fence].strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        if start != -1:
            decoder = json.JSONDecoder()
            try:
                obj, _ = decoder.raw_decode(cleaned[start:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass

    logger.warning("Failed to parse plan JSON, returning raw text wrapper")
    return {"raw_text": text, "_parse_error": True}
