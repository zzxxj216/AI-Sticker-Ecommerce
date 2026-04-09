"""Video Script Service — Stage 2: generate full storyboard from plan.

Takes an approved script plan and produces a production-ready
structured storyboard script with shots, text overlays, CTAs, etc.
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
from src.services.video.video_prompt_builder import VideoPromptBuilder, SCRIPT_SYSTEM_PROMPT

logger = get_logger("service.video.script")

_STORE_PROFILE_PATH = Path("config/store_profile.yaml")


class VideoScriptService:
    """Stage 2: generate the full script from an approved plan."""

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

    def generate_script(
        self,
        plan_id: str,
        *,
        created_by: str = "system",
    ) -> dict[str, Any]:
        """Generate a full script from a saved plan.

        Returns the saved script record dict.
        """
        plan_record = self.db.get_video_script_plan_v2(plan_id)
        if not plan_record:
            raise ValueError(f"Plan not found: {plan_id}")

        combo_id = plan_record["combo_id"]
        combo = self.combo_service.get_combo(combo_id)
        if not combo:
            raise ValueError(f"Combo not found: {combo_id}")

        type_configs = self.type_service.get_types_for_combo(combo.get("selected_types", []))
        script_input = plan_record.get("input_snapshot", {})
        plan = plan_record.get("plan", {})

        user_prompt = VideoPromptBuilder.build_script_prompt(
            script_input=script_input,
            combo=combo,
            type_configs=type_configs,
            plan=plan,
            brand_profile=self.brand_profile,
        )

        logger.info("Generating video script: plan=%s combo=%s", plan_id, combo_id)

        result = self.openai.generate(
            prompt=user_prompt,
            system=SCRIPT_SYSTEM_PROMPT,
            temperature=0.7,
        )
        raw_text = result.get("text", "")
        script_json = _parse_json(raw_text)

        script_json = self._validate_script(script_json, combo)

        script_id = f"vs_{uuid.uuid4().hex[:12]}"
        record = {
            "id": script_id,
            "combo_id": combo_id,
            "plan_id": plan_id,
            "job_id": plan_record.get("job_id", ""),
            "family_id": plan_record.get("family_id", ""),
            "design_id": plan_record.get("design_id", ""),
            "pack_id": plan_record.get("pack_id", ""),
            "hook_text": script_json.get("hook_text", ""),
            "cta_text": script_json.get("cta_text", ""),
            "caption_text": script_json.get("caption_text", ""),
            "title_options": script_json.get("title_options", []),
            "script": script_json,
            "status": "completed",
            "created_by": created_by,
        }
        self.db.insert_video_script(record)

        logger.info("Video script saved: %s (plan=%s)", script_id, plan_id)
        return self.db.get_video_script(script_id)

    def generate_from_combo(
        self,
        combo_id: str,
        script_input: dict[str, Any],
        *,
        job_id: str = "",
        created_by: str = "system",
    ) -> dict[str, Any]:
        """Convenience: run both stages (plan → script) in one call.

        Returns the final script record dict.
        """
        from src.services.video.video_plan_service import VideoPlanService

        plan_svc = VideoPlanService(self.db, self.openai)
        plan_record = plan_svc.generate_plan(
            combo_id, script_input, job_id=job_id, created_by=created_by,
        )
        return self.generate_script(plan_record["id"], created_by=created_by)

    def _validate_script(self, script: dict[str, Any], combo: dict[str, Any]) -> dict[str, Any]:
        """Post-generation validation with auto-fix hints."""
        issues: list[str] = []

        dr = combo.get("duration_range", {})
        dur = script.get("total_duration_sec", 0)
        if dur < dr.get("min", 0) or dur > dr.get("max", 999):
            issues.append(f"duration_out_of_range:{dur}")

        sr = combo.get("shot_count_range", {})
        shots = script.get("shots", [])
        if len(shots) < sr.get("min", 0) or len(shots) > sr.get("max", 999):
            issues.append(f"shot_count_out_of_range:{len(shots)}")

        if not script.get("hook_text"):
            issues.append("missing_hook_text")
        if not script.get("cta_text"):
            issues.append("missing_cta_text")

        selected = set(combo.get("selected_types", []))
        if "collection_flex" in selected:
            has_collection = any(
                "collection" in (s.get("sticker_action", "") + s.get("visual_description", "")).lower()
                for s in shots
            )
            if not has_collection:
                issues.append("collection_flex_missing_collection_sheet")

        if "commerce_scene" in selected:
            has_usecase = any(
                any(kw in (s.get("visual_description", "") + s.get("sticker_action", "")).lower()
                    for kw in ("laptop", "bottle", "journal", "kindle", "phone", "car", "use"))
                for s in shots
            )
            if not has_usecase:
                issues.append("commerce_scene_missing_use_case")

        if "comment_driver" in selected and shots:
            last_text = (shots[-1].get("on_screen_text", "") + shots[-1].get("voiceover", "")).strip()
            if "?" not in last_text:
                issues.append("comment_driver_last_shot_not_question")

        for s in shots:
            words = s.get("on_screen_text", "").split()
            if len(words) > 8:
                issues.append(f"on_screen_text_too_long:shot_{s.get('shot_index', '?')}")

        if issues:
            script["_validation_issues"] = issues
            logger.warning("Script validation issues: %s", issues)

        return script


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

    logger.warning("Failed to parse script JSON, returning raw text wrapper")
    return {"raw_text": text, "_parse_error": True}
