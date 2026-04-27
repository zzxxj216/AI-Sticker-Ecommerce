"""Topic plan service — generate, list, query, manage A.2 topic plans.

Two-step generation:
  Step 1: AIRouter.text_complete (gpt-5.4-pro) → free-form markdown
  Step 2: AIRouter.extract_json (gpt-4o-mini) → structured series_payload

The markdown is preserved verbatim in topic_plans.main_raw_text. The JSON
goes into topic_plans.series_payload AND is fanned out to per-series rows
in pack_series so the operator can toggle is_selected per series before
A.3 spends image-gen budget on previews.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router
from src.services.hot_topics import get_hot_topic_service
from src.services.topic_plans.prompts import (
    EXTRACT_INSTRUCTIONS,
    MAIN_SYSTEM_PROMPT,
    build_extract_schema,
    build_main_prompt,
)

logger = get_logger("service.topic_plans")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Defaults — operator-adjustable in the /v2/topic-plans/new form
# ---------------------------------------------------------------------------

DEFAULT_SERIES_COUNT = 5
DEFAULT_PREVIEWS_PER_SERIES = 5
DEFAULT_STICKERS_PER_PREVIEW = 10


class TopicPlanService:
    def __init__(
        self,
        router: Optional[AIRouter] = None,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.router = router or get_router()
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------

    def generate_plan(
        self,
        topic_id: int,
        *,
        series_count: int = DEFAULT_SERIES_COUNT,
        previews_per_series: int = DEFAULT_PREVIEWS_PER_SERIES,
        stickers_per_preview: int = DEFAULT_STICKERS_PER_PREVIEW,
        extra_brief: str = "",
        main_model: Optional[str] = None,
        extract_model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run the two-step generation and persist plan + series rows.

        Returns ``{'plan_id': int, 'series_count': int, 'main_chars': int,
        'extract_ok': bool, 'extract_error': str}``. On extraction failure
        the plan row is still created (status='draft_extract_failed') so
        the operator can read main_raw_text and retry.
        """
        topics = get_hot_topic_service()
        topic = topics.get_topic(topic_id)
        if not topic:
            raise ValueError(f"hot_topic #{topic_id} not found")

        config = {
            "series_count":         series_count,
            "previews_per_series":  previews_per_series,
            "stickers_per_preview": stickers_per_preview,
            "main_model":           main_model or self.router.DEFAULT_TEXT_MODEL,
            "extract_model":        extract_model or self.router.DEFAULT_EXTRACT_MODEL,
            "extra_brief":          extra_brief,
        }
        now = int(time.time())

        # Insert plan row early so AICallLog rows can reference it.
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO topic_plans
                    (topic_id, config, main_raw_text, series_payload,
                     status, created_at, updated_at)
                VALUES (?, ?, '', '{}', 'generating', ?, ?)
                """,
                (topic_id, json.dumps(config, ensure_ascii=False), now, now),
            )
            plan_id = cur.lastrowid
            conn.commit()

        # ---- Step 1: main creative model -----------------------------
        main_prompt = build_main_prompt(
            topic_name=topic["topic_name"],
            topic_query=topic.get("query"),
            topic_evidence_urls=topic.get("evidence_urls") or [],
            series_count=series_count,
            previews_per_series=previews_per_series,
            stickers_per_preview=stickers_per_preview,
            region=topic.get("region") or "US",
            extra_brief=extra_brief,
        )
        try:
            main_text = self.router.text_complete(
                main_prompt,
                model=main_model,
                system=MAIN_SYSTEM_PROMPT,
                temperature=0.8,
                task="topic_plan:main",
                related_table="topic_plans",
                related_id=plan_id,
            )
        except Exception as e:
            logger.exception("main generation failed for plan %d", plan_id)
            self._mark_status(plan_id, "draft_main_failed", error=str(e))
            raise

        # ---- Step 2: extract JSON -----------------------------------
        schema = build_extract_schema(
            series_count=series_count,
            previews_per_series=previews_per_series,
            stickers_per_preview=stickers_per_preview,
        )
        extract_ok = True
        extract_error = ""
        series_payload: dict[str, Any] = {}
        try:
            series_payload = self.router.extract_json(
                main_text,
                schema=schema,
                instructions=EXTRACT_INSTRUCTIONS,
                model=extract_model,
                max_retries=1,
                task="topic_plan:extract",
                related_table="topic_plans",
                related_id=plan_id,
            )
        except Exception as e:
            logger.warning("extract failed for plan %d: %s", plan_id, e)
            extract_ok = False
            extract_error = str(e)[:500]

        # ---- Persist results ----------------------------------------
        status = "ready" if extract_ok else "draft_extract_failed"
        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE topic_plans
                   SET main_raw_text = ?, series_payload = ?,
                       status = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    main_text,
                    json.dumps(series_payload, ensure_ascii=False),
                    status,
                    int(time.time()),
                    plan_id,
                ),
            )
            if extract_ok:
                self._insert_series_rows(conn, plan_id, series_payload)
            conn.commit()

        return {
            "plan_id":       plan_id,
            "series_count":  len(series_payload.get("series", [])) if extract_ok else 0,
            "main_chars":    len(main_text),
            "extract_ok":    extract_ok,
            "extract_error": extract_error,
            "status":        status,
        }

    @staticmethod
    def _insert_series_rows(
        conn: sqlite3.Connection,
        plan_id: int,
        payload: dict[str, Any],
    ) -> None:
        for idx, s in enumerate(payload.get("series", []), 1):
            metadata = {
                "positioning_cn":      s.get("positioning_cn", ""),
                "target_users_cn":     s.get("target_users_cn", []),
                "title_en":            s.get("title_en", ""),
                "target_audience_en":  s.get("target_audience_en", ""),
                "preview_briefs":      s.get("preview_briefs", []),
            }
            conn.execute(
                """
                INSERT INTO pack_series
                    (plan_id, series_idx, series_name, style_anchor, palette,
                     pack_archetype, priority, metadata_json, is_selected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    plan_id, idx,
                    (s.get("series_name") or f"series_{idx}")[:200],
                    s.get("style_anchor", ""),
                    s.get("palette", ""),
                    s.get("pack_archetype", ""),
                    s.get("priority") or "medium",
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )

    def _mark_status(self, plan_id: int, status: str, *, error: str = "") -> None:
        with _open_db(self.db_path) as conn:
            # error is captured in series_payload._error so we don't need a column.
            payload = json.dumps({"_error": error[:500]}, ensure_ascii=False) if error else "{}"
            conn.execute(
                """
                UPDATE topic_plans
                   SET status = ?, series_payload = ?, updated_at = ?
                 WHERE id = ?
                """,
                (status, payload, int(time.time()), plan_id),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Read / list
    # ------------------------------------------------------------------

    def list_plans(
        self,
        *,
        topic_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        clauses, params = [], []
        if topic_id is not None:
            clauses.append("p.topic_id = ?")
            params.append(topic_id)
        if status and status != "all":
            clauses.append("p.status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        with _open_db(self.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM topic_plans p {where}", tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT p.id, p.topic_id, p.config, p.status,
                       p.created_at, p.updated_at,
                       t.topic_name, t.source AS topic_source,
                       (SELECT COUNT(*) FROM pack_series s WHERE s.plan_id = p.id) AS series_count,
                       (SELECT COUNT(*) FROM pack_series s WHERE s.plan_id = p.id AND s.is_selected = 1) AS selected_count
                  FROM topic_plans p
                  LEFT JOIN hot_topics t ON t.id = p.topic_id
                  {where}
                 ORDER BY p.id DESC
                 LIMIT ? OFFSET ?
                """,
                tuple(params) + (limit, offset),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["config"] = json.loads(d["config"] or "{}")
            except Exception:
                d["config"] = {}
            out.append(d)
        return out, total

    def get_plan(self, plan_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                """
                SELECT p.id, p.topic_id, p.config, p.main_raw_text,
                       p.series_payload, p.status,
                       p.created_at, p.updated_at,
                       t.topic_name, t.source AS topic_source,
                       t.query AS topic_query, t.region AS topic_region,
                       t.evidence_urls AS topic_evidence_urls
                  FROM topic_plans p
                  LEFT JOIN hot_topics t ON t.id = p.topic_id
                 WHERE p.id = ?
                """,
                (plan_id,),
            ).fetchone()
            if not r:
                return None
            plan = dict(r)
            for f in ("config", "series_payload"):
                try:
                    plan[f] = json.loads(plan.get(f) or "{}")
                except Exception:
                    plan[f] = {}
            try:
                plan["topic_evidence_urls"] = json.loads(plan.get("topic_evidence_urls") or "[]")
            except Exception:
                plan["topic_evidence_urls"] = []

            series_rows = conn.execute(
                """
                SELECT id, series_idx, series_name, style_anchor, palette,
                       pack_archetype, priority, metadata_json, is_selected
                  FROM pack_series
                 WHERE plan_id = ?
                 ORDER BY series_idx
                """,
                (plan_id,),
            ).fetchall()
        series = []
        for sr in series_rows:
            sd = dict(sr)
            try:
                sd["metadata"] = json.loads(sd.get("metadata_json") or "{}")
            except Exception:
                sd["metadata"] = {}
            series.append(sd)
        plan["series"] = series
        return plan

    # ------------------------------------------------------------------
    # Mutate
    # ------------------------------------------------------------------

    def toggle_series_selection(self, series_id: int, is_selected: bool) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE pack_series SET is_selected = ? WHERE id = ?",
                (1 if is_selected else 0, series_id),
            )
            conn.commit()
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[TopicPlanService] = None


def get_topic_plan_service() -> TopicPlanService:
    global _svc
    if _svc is None:
        _svc = TopicPlanService()
    return _svc
