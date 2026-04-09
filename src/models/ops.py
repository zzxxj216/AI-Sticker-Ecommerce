from __future__ import annotations

from datetime import datetime, timezone, timedelta

_CN_TZ = timezone(timedelta(hours=8))

def _now() -> datetime:
    return datetime.now(_CN_TZ)
from typing import Any, Literal

from pydantic import BaseModel, Field


TrendSource = Literal["news", "tiktok"]
TrendReviewStatus = Literal["pending", "approved", "skipped"]
QueueStatus = Literal["idle", "queued", "running", "completed", "failed"]
JobStatus = Literal["queued", "running", "completed", "failed"]


class TrendItem(BaseModel):
    id: str
    source_type: TrendSource
    source_item_id: str = ""
    title: str
    summary: str = ""
    trend_name: str = ""
    trend_type: str = ""
    score: float = 0.0
    heat_score: float = 0.0
    fit_level: str = ""
    pack_archetype: str = ""
    review_status: TrendReviewStatus = "pending"
    queue_status: QueueStatus = "idle"
    decision: str = ""
    platform: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    visual_symbols: list[str] = Field(default_factory=list)
    emotional_core: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    source_url: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class TrendBriefRecord(BaseModel):
    trend_id: str
    brief_status: str = "missing"
    brief_json: dict[str, Any] = Field(default_factory=dict)
    source_ref: str = ""
    edited_by: str = ""
    edited_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GenerationJob(BaseModel):
    id: str
    trend_id: str
    trend_name: str
    status: JobStatus = "queued"
    output_dir: str = ""
    image_count: int = 0
    error_message: str = ""
    created_by: str = "system"
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=_now)
    family_id: str | None = None
    subtheme_id: int | None = None
    variant_label: str | None = None


class GenerationOutput(BaseModel):
    id: str
    job_id: str
    output_type: str
    file_path: str
    preview_path: str = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


PlanningRegion = Literal["us", "ca", "eu"]
EventCategory = Literal["holiday", "event", "cultural", "sports", ""]


class PlanningEvent(BaseModel):
    id: str
    title: str
    category: EventCategory = ""
    region: PlanningRegion
    start_date: str
    end_date: str | None = None
    short_description: str = ""
    source: str = ""
    fetch_batch: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
