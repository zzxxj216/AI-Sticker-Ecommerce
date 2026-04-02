from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from trend_fetcher.trend_db import TrendDB

from src.core.logger import get_logger
from src.models.ops import TrendBriefRecord, TrendItem
from src.services.ops.db import OpsDatabase

logger = get_logger("service.ops.sync")


class OpsSyncService:
    def __init__(
        self,
        db: OpsDatabase | None = None,
        trend_output_dir: str | Path | None = None,
        tiktok_db_path: str | Path | None = None,
    ):
        self.db = db or OpsDatabase()
        resolved_trend_output_dir = trend_output_dir or os.getenv("TREND_OUTPUT_DIR", "trend_fetcher/output")
        resolved_tiktok_db_path = tiktok_db_path or os.getenv("TIKTOK_DB_PATH", "data/ops_workbench.db")
        self.trend_output_dir = Path(resolved_trend_output_dir)
        self.tiktok_db_path = Path(resolved_tiktok_db_path)

    def sync_all(self, run_tiktok: bool = True) -> dict[str, int]:
        total = {"tiktok": 0, "total": 0}
        if run_tiktok:
            tiktok_count = self.sync_tiktok_pipeline()
            total["tiktok"] = tiktok_count
            total["total"] += tiktok_count
        return total

    def sync_tiktok_pipeline(self) -> int:
        if not self.tiktok_db_path.exists():
            logger.info("TikTok DB not found: %s", self.tiktok_db_path)
            return 0

        trend_db = TrendDB(self.tiktok_db_path)
        count = 0
        try:
            rows = trend_db.list_tk_hashtags(limit=0)
            reviews = self._fetch_review_map(trend_db)
            briefs = self._fetch_brief_map(trend_db)

            for row in rows:
                review = reviews.get(row["hashtag_id"], {})
                brief = briefs.get(row["hashtag_id"])
                trend_id = f"tiktok:{row['hashtag_id']}"
                title = row.get("hashtag_name", "")
                item = TrendItem(
                    id=trend_id,
                    source_type="tiktok",
                    source_item_id=row.get("hashtag_id", ""),
                    title=title,
                    summary=review.get("one_line_interpretation", ""),
                    trend_name=review.get("normalized_theme", title),
                    trend_type=review.get("theme_type", ""),
                    score=float(review.get("score_total", 0) or 0),
                    heat_score=float(row.get("video_views", 0) or 0),
                    fit_level=review.get("sticker_fit_level", ""),
                    pack_archetype=review.get("pack_archetype", ""),
                    review_status=self._map_tiktok_review_status(row, review),
                    queue_status="idle",
                    decision=self._normalize_tiktok_decision(review.get("decision", "")),
                    platform=self._split_csv(review.get("best_platform", "")),
                    risk_flags=self._split_csv(review.get("risk_flags", "")),
                    visual_symbols=self._split_csv(review.get("visual_symbols", "")),
                    emotional_core=self._split_csv(review.get("emotional_hooks", "")),
                    raw_payload={"hashtag": row, "review": review, "brief": brief or {}},
                    source_url="",
                )
                self.db.upsert_trend_item(item)
                if brief:
                    self.db.upsert_brief(
                        TrendBriefRecord(
                            trend_id=trend_id,
                            brief_status="ready" if brief.get("brief_status") == "ready" else "generated",
                            brief_json=self._convert_tiktok_brief(brief),
                            source_ref=str(self.tiktok_db_path),
                            created_at=datetime.utcnow(),
                            updated_at=datetime.utcnow(),
                        )
                    )
                count += 1
        finally:
            trend_db.close()

        logger.info("Synced %d TikTok trends", count)
        return count

    @staticmethod
    def _read_json_list(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []

    @staticmethod
    def _split_csv(value: str) -> list[str]:
        if not value:
            return []
        return [part.strip() for part in str(value).split(",") if part.strip()]

    @staticmethod
    def _map_review_status(decision: str) -> str:
        """AI decisions map to pending — human must manually approve."""
        if decision in ("recommend", "watchlist", "review"):
            return "pending"
        return "skipped"

    @staticmethod
    def _normalize_tiktok_decision(decision: str) -> str:
        if decision == "approve":
            return "recommend"
        if decision == "watchlist":
            return "review"
        return decision or ""

    def _map_tiktok_review_status(self, row: dict[str, Any], review: dict[str, Any]) -> str:
        if row.get("review_status") != "reviewed":
            return "pending"
        decision = self._normalize_tiktok_decision(review.get("decision", ""))
        if decision == "recommend":
            return "approved"
        if decision == "review":
            return "pending"
        return "skipped"

    @staticmethod
    def _convert_tiktok_brief(brief: dict[str, Any]) -> dict[str, Any]:
        def _to_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(v).strip() for v in value if str(v).strip()]
            if not value:
                return []
            return [part.strip() for part in str(value).split(",") if part.strip()]

        audience = brief.get("target_audience")
        if isinstance(audience, str):
            audience = {
                "profile": audience,
                "age_range": "",
                "gender_tilt": "",
                "usage_scenarios": [],
            }

        return {
            "trend_name": brief.get("trend_name", ""),
            "trend_type": brief.get("trend_type", ""),
            "one_line_explanation": brief.get("brief_text", "")[:200],
            "why_now": brief.get("brief_text", "")[:300],
            "lifecycle": brief.get("lifecycle", ""),
            "platform": _to_list(brief.get("platform", "")),
            "product_goal": _to_list(brief.get("product_goal", "")),
            "target_audience": audience or {"profile": "", "usage_scenarios": []},
            "emotional_core": _to_list(brief.get("emotional_core", "")),
            "visual_symbols": _to_list(brief.get("visual_symbols", "")),
            "must_avoid": [],
            "pack_size_goal": brief.get("pack_size_goal", {}),
            "risk_notes": [],
            "_raw": brief,
        }

    @staticmethod
    def _fetch_review_map(trend_db: TrendDB) -> dict[str, dict[str, Any]]:
        rows = trend_db.conn.execute(
            "SELECT * FROM tk_topic_reviews ORDER BY id DESC"
        ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = dict(row)
            result.setdefault(payload["hashtag_id"], payload)
        return result

    @staticmethod
    def _fetch_brief_map(trend_db: TrendDB) -> dict[str, dict[str, Any]]:
        rows = trend_db.conn.execute(
            "SELECT * FROM tk_topic_briefs ORDER BY id DESC"
        ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = dict(row)
            payload["target_audience"] = OpsSyncService._safe_json(payload.get("target_audience"))
            payload["pack_size_goal"] = OpsSyncService._safe_json(payload.get("pack_size_goal"))
            result.setdefault(payload["hashtag_id"], payload)
        return result

    @staticmethod
    def _safe_json(raw: Any) -> Any:
        if raw in (None, ""):
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return raw
