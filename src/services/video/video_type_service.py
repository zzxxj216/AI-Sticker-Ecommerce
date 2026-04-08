"""Video Type Service — manages video type configurations.

Reads type configs, validates required_inputs, and provides type rules
for prompt building and script validation.
"""

from __future__ import annotations

from typing import Any

from src.core.logger import get_logger
from src.services.ops.db import OpsDatabase

logger = get_logger("service.video.type")


class VideoTypeService:
    """CRUD + validation for video types."""

    def __init__(self, db: OpsDatabase):
        self.db = db

    def list_types(self, active_only: bool = False) -> list[dict[str, Any]]:
        return self.db.list_video_types(active_only=active_only)

    def get_type(self, type_id: str) -> dict[str, Any] | None:
        return self.db.get_video_type(type_id)

    def save_type(self, data: dict[str, Any]) -> None:
        if not data.get("type_id") or not data.get("name"):
            raise ValueError("type_id and name are required")
        self.db.upsert_video_type(data)
        logger.info("Video type saved: %s", data["type_id"])

    def toggle_active(self, type_id: str, is_active: bool) -> bool:
        ok = self.db.toggle_video_type(type_id, is_active)
        if ok:
            logger.info("Video type %s set active=%s", type_id, is_active)
        return ok

    def get_types_for_combo(self, type_ids: list[str]) -> list[dict[str, Any]]:
        """Return type configs for a given list of type_ids (preserving order)."""
        all_types = {t["type_id"]: t for t in self.db.list_video_types(active_only=True)}
        result = []
        for tid in type_ids:
            cfg = all_types.get(tid)
            if cfg:
                result.append(cfg)
            else:
                logger.warning("Video type not found or inactive: %s", tid)
        return result

    def validate_inputs(self, type_id: str, available_inputs: set[str]) -> list[str]:
        """Check if all required_inputs for a type are present.

        Returns list of missing input keys (empty = OK).
        """
        cfg = self.db.get_video_type(type_id)
        if not cfg:
            return [f"type_not_found:{type_id}"]
        required = set(cfg.get("required_inputs", []))
        return sorted(required - available_inputs)
