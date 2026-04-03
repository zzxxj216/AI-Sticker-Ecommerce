from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone, timedelta

_CN_TZ = timezone(timedelta(hours=8))
from pathlib import Path

from src.core.logger import get_logger
from src.models.ops import GenerationJob, GenerationOutput
from src.services.ai.gemini_service import GeminiService
from src.services.ai.openai_service import OpenAIService
from src.services.batch.sticker_pipeline import StickerPackPipeline
from src.services.ops.db import OpsDatabase

logger = get_logger("service.ops.jobs")


class JobService:
    def __init__(self, db: OpsDatabase | None = None):
        self.db = db or OpsDatabase()
        self._openai: OpenAIService | None = None
        self._gemini: GeminiService | None = None

    @property
    def openai(self) -> OpenAIService:
        if self._openai is None:
            self._openai = OpenAIService()
        return self._openai

    @property
    def gemini(self) -> GeminiService:
        if self._gemini is None:
            self._gemini = GeminiService()
        return self._gemini

    def create_job(self, trend_id: str, trend_name: str, created_by: str = "system") -> GenerationJob:
        job = GenerationJob(
            id=f"job_{uuid.uuid4().hex[:12]}",
            trend_id=trend_id,
            trend_name=trend_name,
            status="queued",
            created_by=created_by,
            created_at=datetime.now(_CN_TZ),
            updated_at=datetime.now(_CN_TZ),
        )
        self.db.create_job(job)
        self.db.set_trend_queue_status(trend_id, "queued")
        return job

    def start_job_async(self, job_id: str, brief: dict, trend_name: str) -> None:
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, brief, trend_name),
            daemon=True,
        )
        thread.start()

    def retry_job(self, job_id: str) -> dict:
        job = self.db.get_job(job_id)
        if not job:
            raise ValueError(f"Job not found: {job_id}")
        brief = self.db.get_brief(job["trend_id"])
        trend = self.db.get_trend(job["trend_id"])
        if not brief or not trend:
            raise ValueError("Missing trend or brief for retry")
        self.db.update_job(job_id, status="queued", error_message="", started_at=None, finished_at=None)
        self.db.set_trend_queue_status(job["trend_id"], "queued")
        self.start_job_async(job_id, brief["brief_json"], trend.get("trend_name") or trend.get("title") or job["trend_name"])
        return self.db.get_job(job_id) or job

    def _run_job(self, job_id: str, brief: dict, trend_name: str) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return

        self.db.update_job(job_id, status="running", started_at=datetime.now(_CN_TZ))
        self.db.set_trend_queue_status(job["trend_id"], "running")

        try:
            pipeline = StickerPackPipeline(
                openai_service=self.openai,
                gemini_service=self.gemini,
                output_dir="output/h5_jobs",
            )
            result = pipeline.run(theme=trend_name, trend_brief=brief)
            output_dir = str(pipeline.output_dir)
            outputs = self._collect_outputs(job_id, Path(output_dir))
            self.db.replace_outputs(job_id, outputs)
            self.db.update_job(
                job_id,
                status=result.status if result.status in {"completed", "failed"} else "completed",
                output_dir=output_dir,
                image_count=len(result.image_paths),
                error_message=result.error or "",
                finished_at=datetime.now(_CN_TZ),
            )
            self.db.set_trend_queue_status(job["trend_id"], result.status if result.status in {"completed", "failed"} else "completed")
        except Exception as exc:
            logger.error("Job %s failed: %s", job_id, exc, exc_info=True)
            self.db.update_job(
                job_id,
                status="failed",
                error_message=str(exc),
                finished_at=datetime.now(_CN_TZ),
            )
            self.db.set_trend_queue_status(job["trend_id"], "failed")

    @staticmethod
    def _collect_outputs(job_id: str, output_dir: Path) -> list[GenerationOutput]:
        if not output_dir.exists():
            return []
        outputs: list[GenerationOutput] = []
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file():
                continue
            output_type = JobService._detect_output_type(path)
            outputs.append(
                GenerationOutput(
                    id=f"out_{uuid.uuid4().hex[:12]}",
                    job_id=job_id,
                    output_type=output_type,
                    file_path=str(path),
                    preview_path=str(path) if output_type == "image" else "",
                    metadata_json={"name": path.name, "size": path.stat().st_size},
                    created_at=datetime.now(_CN_TZ),
                )
            )
        return outputs

    @staticmethod
    def _detect_output_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return "image"
        if suffix in {".txt", ".md", ".json"}:
            return "text"
        return "file"
