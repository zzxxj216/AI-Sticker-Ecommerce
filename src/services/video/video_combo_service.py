"""Video Combo Service — manages video type combination configurations.

Reads combo configs, validates combo legality (types compatibility,
duration/shot ranges), and returns constraints for prompt building.
"""

from __future__ import annotations

from typing import Any

from src.core.logger import get_logger
from src.services.ops.db import OpsDatabase

logger = get_logger("service.video.combo")


class VideoComboService:
    """CRUD + validation for video type combos."""

    def __init__(self, db: OpsDatabase):
        self.db = db

    def list_combos(self, active_only: bool = False) -> list[dict[str, Any]]:
        return self.db.list_video_type_combos(active_only=active_only)

    def get_combo(self, combo_id: str) -> dict[str, Any] | None:
        return self.db.get_video_type_combo(combo_id)

    def save_combo(self, data: dict[str, Any]) -> None:
        if not data.get("combo_id") or not data.get("name"):
            raise ValueError("combo_id and name are required")
        selected = data.get("selected_types", [])
        if not selected:
            raise ValueError("selected_types must contain at least one type")
        if len(selected) > 4:
            raise ValueError("selected_types should not exceed 4 for V1")
        self.db.upsert_video_type_combo(data)
        logger.info("Video combo saved: %s", data["combo_id"])

    def toggle_active(self, combo_id: str, is_active: bool) -> bool:
        ok = self.db.toggle_video_type_combo(combo_id, is_active)
        if ok:
            logger.info("Video combo %s set active=%s", combo_id, is_active)
        return ok

    def delete_combo(self, combo_id: str) -> bool:
        ok = self.db.delete_video_type_combo(combo_id)
        if ok:
            logger.info("Video combo deleted: %s", combo_id)
        return ok

    def validate_combo(self, combo_id: str) -> list[str]:
        """Validate a combo: check types exist, are active, and can pair.

        Returns list of issues (empty = valid).
        """
        combo = self.db.get_video_type_combo(combo_id)
        if not combo:
            return [f"combo_not_found:{combo_id}"]

        issues: list[str] = []
        all_types = {t["type_id"]: t for t in self.db.list_video_types(active_only=True)}
        selected = combo.get("selected_types", [])

        for tid in selected:
            if tid not in all_types:
                issues.append(f"type_missing_or_inactive:{tid}")

        for tid in selected:
            cfg = all_types.get(tid)
            if not cfg:
                continue
            can_pair = set(cfg.get("can_pair_with", []))
            for other_tid in selected:
                if other_tid != tid and other_tid not in can_pair:
                    issues.append(f"incompatible_pair:{tid}<->{other_tid}")

        dr = combo.get("duration_range", {})
        if dr.get("min", 0) > dr.get("max", 999):
            issues.append("duration_range_invalid")

        sr = combo.get("shot_count_range", {})
        if sr.get("min", 0) > sr.get("max", 999):
            issues.append("shot_count_range_invalid")

        return issues
