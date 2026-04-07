"""Video Script Agent — generates TikTok video scripts for sticker packs.

Given a sticker pack (job_id) and a script template (template_id),
assembles context from existing pipeline outputs and calls OpenAI
to produce a structured video script plan.
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
from src.services.video.script_prompts import VIDEO_SCRIPT_SYSTEM, build_video_script_prompt

logger = get_logger("service.video.script_agent")

_STORE_PROFILE_PATH = Path("config/store_profile.yaml")


class VideoScriptAgent:
    """Generates video script plans for sticker packs."""

    def __init__(
        self,
        db: OpsDatabase,
        openai_service: OpenAIService | None = None,
    ):
        self.db = db
        self.openai = openai_service or OpenAIService()
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
        job_id: str,
        template_id: str,
        created_by: str = "system",
    ) -> dict[str, Any]:
        """Generate a video script plan for a single (job, template) pair.

        Returns the saved plan dict including 'id' and 'plan'.
        """
        template = self.db.get_video_script_template(template_id)
        if not template:
            raise ValueError(f"Template not found: {template_id}")

        job = self.db.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")

        trend_brief = self._load_trend_brief(job.get("trend_id", ""))
        sticker_descs = self._load_sticker_descriptions(job_id)

        user_prompt = build_video_script_prompt(
            template=template,
            job_info=job,
            trend_brief=trend_brief,
            sticker_descriptions=sticker_descs,
            brand_profile=self.brand_profile,
        )

        logger.info(
            "Generating video script: job=%s template=%s",
            job_id, template_id,
        )

        result = self.openai.generate(
            prompt=user_prompt,
            system=VIDEO_SCRIPT_SYSTEM,
            temperature=0.7,
        )
        raw_text = result.get("text", "")
        plan_json = self._parse_json_response(raw_text)

        plan_id = f"vp_{uuid.uuid4().hex[:12]}"
        plan_record = {
            "id": plan_id,
            "job_id": job_id,
            "template_id": template_id,
            "plan_json": plan_json,
            "status": "completed",
            "created_by": created_by,
        }
        self.db.insert_video_plan(plan_record)

        logger.info("Video plan saved: %s (job=%s, template=%s)", plan_id, job_id, template_id)
        return self.db.get_video_plan(plan_id)

    def generate_batch(
        self,
        job_ids: list[str],
        template_ids: list[str],
        created_by: str = "system",
    ) -> list[dict[str, Any]]:
        """Generate plans for all (job, template) combinations."""
        plans: list[dict[str, Any]] = []
        for jid in job_ids:
            for tid in template_ids:
                try:
                    plan = self.generate_plan(jid, tid, created_by=created_by)
                    plans.append(plan)
                except Exception as e:
                    logger.error("Failed to generate plan job=%s template=%s: %s", jid, tid, e)
                    plans.append({
                        "job_id": jid,
                        "template_id": tid,
                        "status": "failed",
                        "error": str(e),
                    })
        return plans

    def _load_trend_brief(self, trend_id: str) -> dict[str, Any] | None:
        if not trend_id or trend_id.startswith("chat:"):
            return None
        brief_row = self.db.get_brief(trend_id)
        if not brief_row:
            return None
        return brief_row.get("brief_json")

    def _load_sticker_descriptions(self, job_id: str) -> list[str]:
        """Extract short descriptions from generation outputs metadata."""
        outputs = self.db.list_outputs(job_id)
        descriptions: list[str] = []
        for out in outputs:
            if out.get("output_type") != "image":
                continue
            meta = out.get("metadata_json", {})
            name = meta.get("name", "")
            prompt = meta.get("prompt", "")
            if name:
                desc = name
                if prompt:
                    desc += f" — {prompt[:120]}"
                descriptions.append(desc)
            elif prompt:
                descriptions.append(prompt[:150])
            else:
                fp = out.get("file_path", "")
                descriptions.append(Path(fp).stem if fp else "sticker")
        return descriptions

    @staticmethod
    def _parse_json_response(text: str) -> dict[str, Any]:
        """Extract JSON from LLM response, handling markdown fences."""
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

        logger.warning("Failed to parse video script JSON, returning raw text")
        return {"raw_text": text, "_parse_error": True}
