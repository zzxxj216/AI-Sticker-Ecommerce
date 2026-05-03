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
from src.services.preview_gen.prompts import build_preview_prompt

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

        plan_id = self._insert_draft_plan(topic_id, config, now)
        return self._run_ai_steps(plan_id, topic, config)

    def _insert_draft_plan(self, topic_id: int, config: dict, now: int) -> int:
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
            conn.commit()
            return cur.lastrowid

    def start_plan(
        self,
        topic_id: int,
        *,
        series_count: int = DEFAULT_SERIES_COUNT,
        previews_per_series: int = DEFAULT_PREVIEWS_PER_SERIES,
        stickers_per_preview: int = DEFAULT_STICKERS_PER_PREVIEW,
        extra_brief: str = "",
        main_model: Optional[str] = None,
        extract_model: Optional[str] = None,
    ) -> int:
        """Insert the draft topic_plans row and return its ID immediately.

        Caller is expected to schedule ``continue_plan(plan_id, ...)`` in
        a background thread to actually run the two AI steps.
        """
        topic = get_hot_topic_service().get_topic(topic_id)
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
        return self._insert_draft_plan(topic_id, config, int(time.time()))

    def continue_plan(self, plan_id: int) -> dict:
        """Run the AI steps for a draft plan_id created by start_plan.

        Reads config from the existing row; safe to call from a background
        thread.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT topic_id, config FROM topic_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"plan #{plan_id} not found")
        cfg = json.loads(row["config"] or "{}")
        topic_id = row["topic_id"]
        topic = get_hot_topic_service().get_topic(topic_id)
        if not topic:
            self._mark_status(plan_id, "draft_main_failed",
                              error=f"parent topic #{topic_id} disappeared")
            raise ValueError(f"hot_topic #{topic_id} not found")

        return self._run_ai_steps(plan_id, topic, cfg)

    def _run_ai_steps(self, plan_id: int, topic: dict, config: dict) -> dict:
        """Inner AI execution shared between generate_plan (sync) and
        continue_plan (called from background thread)."""
        series_count = int(config.get("series_count") or DEFAULT_SERIES_COUNT)
        previews_per_series = int(config.get("previews_per_series") or DEFAULT_PREVIEWS_PER_SERIES)
        stickers_per_preview = int(config.get("stickers_per_preview") or DEFAULT_STICKERS_PER_PREVIEW)
        extra_brief = config.get("extra_brief") or ""
        main_model = config.get("main_model")
        extract_model = config.get("extract_model")

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
                main_prompt, model=main_model, system=MAIN_SYSTEM_PROMPT,
                temperature=0.8, task="topic_plan:main",
                related_table="topic_plans", related_id=plan_id,
            )
        except Exception as e:
            logger.exception("main generation failed for plan %d", plan_id)
            self._mark_status(plan_id, "draft_main_failed", error=str(e))
            raise

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
                main_text, schema=schema, instructions=EXTRACT_INSTRUCTIONS,
                model=extract_model, max_retries=1,
                task="topic_plan:extract",
                related_table="topic_plans", related_id=plan_id,
            )
        except Exception as e:
            logger.warning("extract failed for plan %d: %s", plan_id, e)
            extract_ok = False
            extract_error = str(e)[:500]

        status = "ready" if extract_ok else "draft_extract_failed"
        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE topic_plans
                   SET main_raw_text = ?, series_payload = ?,
                       status = ?, updated_at = ?
                 WHERE id = ?
                """,
                (main_text, json.dumps(series_payload, ensure_ascii=False),
                 status, int(time.time()), plan_id),
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
        query_substring: Optional[str] = None,
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
        if query_substring:
            clauses.append("(t.topic_name LIKE ? OR t.source LIKE ? OR p.status LIKE ?)")
            like = f"%{query_substring}%"
            params.extend([like, like, like])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        with _open_db(self.db_path) as conn:
            total = conn.execute(
                f"""
                SELECT COUNT(*)
                  FROM topic_plans p
                  LEFT JOIN hot_topics t ON t.id = p.topic_id
                  {where}
                """,
                tuple(params),
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
                SELECT s.id, s.series_idx, s.series_name, s.style_anchor, s.palette,
                       s.pack_archetype, s.priority, s.metadata_json, s.is_selected,
                       s.pack_uid,
                       (SELECT p.id FROM packs p WHERE p.series_id = s.id LIMIT 1) AS pack_id,
                       (SELECT COUNT(*) FROM pack_previews pp
                         WHERE pp.series_id = s.id) AS preview_total_count,
                       (SELECT COUNT(*) FROM pack_previews pp
                         WHERE pp.series_id = s.id
                           AND pp.generation_status = 'ok') AS preview_ok_count,
                       (SELECT COUNT(*) FROM pack_previews pp
                         WHERE pp.series_id = s.id
                           AND pp.generation_status = 'pending') AS preview_pending_count,
                       (SELECT COUNT(*) FROM pack_previews pp
                         WHERE pp.series_id = s.id
                           AND pp.generation_status = 'generating') AS preview_generating_count,
                       (SELECT COUNT(*) FROM pack_previews pp
                         WHERE pp.series_id = s.id
                           AND pp.generation_status = 'error') AS preview_error_count,
                       (SELECT COUNT(*) FROM pack_stickers ps
                         JOIN pack_previews pp ON pp.id = ps.preview_id
                        WHERE pp.series_id = s.id
                          AND ps.generation_status = 'ok') AS sticker_ok_count,
                       (SELECT COUNT(*) FROM pack_stickers ps
                         JOIN pack_previews pp ON pp.id = ps.preview_id
                        WHERE pp.series_id = s.id
                          AND ps.generation_status = 'generating') AS sticker_generating_count
                  FROM pack_series s
                 WHERE s.plan_id = ?
                 ORDER BY s.series_idx
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
            sd["preview_expected_count"] = len(sd["metadata"].get("preview_briefs") or [])
            series.append(sd)
        plan["series"] = series
        return plan

    # ------------------------------------------------------------------
    # Mutate
    # ------------------------------------------------------------------

    def retry_extract(self, plan_id: int, *, extract_model: Optional[str] = None) -> dict[str, Any]:
        """Re-run only the extract step against the existing main_raw_text.

        Useful when extract_failed but the main markdown looks salvageable.
        Saves a full main-step call (~30s + most of the cost).
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, main_raw_text, config
                  FROM topic_plans WHERE id = ?
                """,
                (plan_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"plan #{plan_id} not found")
            if not (row["main_raw_text"] or "").strip():
                raise ValueError("plan has no main_raw_text — run generate_plan first")
            cfg = json.loads(row["config"] or "{}")

        schema = build_extract_schema(
            series_count=cfg.get("series_count") or DEFAULT_SERIES_COUNT,
            previews_per_series=cfg.get("previews_per_series") or DEFAULT_PREVIEWS_PER_SERIES,
            stickers_per_preview=cfg.get("stickers_per_preview") or DEFAULT_STICKERS_PER_PREVIEW,
        )
        try:
            payload = self.router.extract_json(
                row["main_raw_text"],
                schema=schema,
                instructions=EXTRACT_INSTRUCTIONS,
                model=extract_model,
                max_retries=1,
                task="topic_plan:extract:retry",
                related_table="topic_plans",
                related_id=plan_id,
            )
        except Exception as e:
            return {"plan_id": plan_id, "extract_ok": False, "error": str(e)[:500]}

        with _open_db(self.db_path) as conn:
            # Refuse if downstream A.3 work already exists — overwriting
            # would orphan pack_previews / pack_stickers (FK violation
            # via cascade, or worse, lose generated images on disk).
            downstream = conn.execute(
                """
                SELECT COUNT(*) FROM pack_previews
                 WHERE series_id IN (SELECT id FROM pack_series WHERE plan_id = ?)
                """,
                (plan_id,),
            ).fetchone()[0]
            if downstream:
                raise ValueError(
                    f"plan #{plan_id} has {downstream} pack_previews already — "
                    "would orphan generated images. Delete them manually first."
                )
            conn.execute("DELETE FROM pack_series WHERE plan_id = ?", (plan_id,))
            conn.execute(
                """
                UPDATE topic_plans
                   SET series_payload = ?, status = 'ready', updated_at = ?
                 WHERE id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), int(time.time()), plan_id),
            )
            self._insert_series_rows(conn, plan_id, payload)
            conn.commit()
        return {"plan_id": plan_id, "extract_ok": True,
                "series_count": len(payload.get("series", []))}

    def toggle_series_selection(self, series_id: int, is_selected: bool) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE pack_series SET is_selected = ? WHERE id = ?",
                (1 if is_selected else 0, series_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def add_manual_series(
        self,
        plan_id: int,
        *,
        series_name: str,
        style_anchor: str = "",
        palette: str = "",
        pack_archetype: str = "",
        priority: str = "medium",
        positioning_cn: str = "",
        title_en: str = "",
        target_users_cn: list[str] | None = None,
        target_audience_en: str = "",
        preview_briefs: list[dict[str, Any]] | None = None,
        is_selected: bool = True,
    ) -> dict[str, Any]:
        """Append one operator-authored series to an existing topic plan.

        This is intentionally lightweight: it creates the pack_series row
        directly, without calling AI. The preview_briefs structure is the same
        one consumed by PreviewGenService.prepare_previews().
        """
        series_name = (series_name or "").strip()[:200]
        if not series_name:
            raise ValueError("series_name required")
        if priority not in {"high", "medium", "low"}:
            priority = "medium"
        preview_briefs = preview_briefs or []
        metadata = {
            "positioning_cn":      positioning_cn or "",
            "target_users_cn":     target_users_cn or [],
            "title_en":            title_en or "",
            "target_audience_en":  target_audience_en or "",
            "preview_briefs":      preview_briefs,
            "manual":              True,
        }
        with _open_db(self.db_path) as conn:
            plan = conn.execute(
                "SELECT id, series_payload FROM topic_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            if not plan:
                raise ValueError(f"plan #{plan_id} not found")
            next_idx = (
                conn.execute(
                    "SELECT COALESCE(MAX(series_idx), 0) + 1 FROM pack_series WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone()[0]
                or 1
            )
            cur = conn.execute(
                """
                INSERT INTO pack_series
                    (plan_id, series_idx, series_name, style_anchor, palette,
                     pack_archetype, priority, metadata_json, is_selected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    int(next_idx),
                    series_name,
                    style_anchor or "",
                    palette or "",
                    pack_archetype or "",
                    priority,
                    json.dumps(metadata, ensure_ascii=False),
                    1 if is_selected else 0,
                ),
            )

            # Keep series_payload roughly in sync for raw/detail inspection.
            try:
                payload = json.loads(plan["series_payload"] or "{}")
            except Exception:
                payload = {}
            series_list = list(payload.get("series") or [])
            series_list.append(
                {
                    "series_name": series_name,
                    "style_anchor": style_anchor or "",
                    "palette": palette or "",
                    "pack_archetype": pack_archetype or "",
                    "priority": priority,
                    "positioning_cn": positioning_cn or "",
                    "target_users_cn": target_users_cn or [],
                    "title_en": title_en or "",
                    "target_audience_en": target_audience_en or "",
                    "preview_briefs": preview_briefs,
                    "manual": True,
                }
            )
            payload["series"] = series_list
            conn.execute(
                """
                UPDATE topic_plans
                   SET series_payload = ?,
                       status = CASE WHEN status = 'generating' THEN 'ready' ELSE status END,
                       updated_at = ?
                 WHERE id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), int(time.time()), plan_id),
            )
            conn.commit()
            return {"series_id": int(cur.lastrowid), "series_idx": int(next_idx)}

    def update_series(
        self,
        series_id: int,
        *,
        series_name: str,
        style_anchor: str = "",
        palette: str = "",
        pack_archetype: str = "",
        priority: str = "medium",
        preview_briefs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Edit one existing series without re-running topic-plan AI."""
        series_name = (series_name or "").strip()[:200]
        if not series_name:
            raise ValueError("series_name required")
        if priority not in {"high", "medium", "low"}:
            priority = "medium"
        preview_briefs = preview_briefs or []

        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, plan_id, series_idx, metadata_json
                  FROM pack_series
                 WHERE id = ?
                """,
                (series_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"series #{series_id} not found")
            plan_id = int(row["plan_id"])
            series_idx = int(row["series_idx"] or 0)

            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            metadata["preview_briefs"] = preview_briefs
            metadata["edited"] = True
            metadata["edited_at"] = int(time.time())

            conn.execute(
                """
                UPDATE pack_series
                   SET series_name = ?,
                       style_anchor = ?,
                       palette = ?,
                       pack_archetype = ?,
                       priority = ?,
                       metadata_json = ?
                 WHERE id = ?
                """,
                (
                    series_name,
                    style_anchor or "",
                    palette or "",
                    pack_archetype or "",
                    priority,
                    json.dumps(metadata, ensure_ascii=False),
                    series_id,
                ),
            )

            # If preview rows were already prepared but not successfully
            # generated yet, refresh their prompt text so edits take effect.
            for brief in preview_briefs:
                try:
                    preview_idx = int(brief.get("preview_idx") or 0)
                except Exception:
                    preview_idx = 0
                if preview_idx <= 0:
                    continue
                prompt_text = build_preview_prompt(
                    style_anchor=style_anchor or "",
                    palette=palette or "",
                    preview_theme=str(brief.get("theme") or ""),
                    stickers=list(brief.get("stickers") or []),
                )
                conn.execute(
                    """
                    UPDATE pack_previews
                       SET prompt_text = ?
                     WHERE series_id = ?
                       AND preview_idx = ?
                       AND generation_status IN ('pending', 'error')
                    """,
                    (prompt_text, series_id, preview_idx),
                )

            plan_row = conn.execute(
                "SELECT series_payload FROM topic_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
            try:
                payload = json.loads(plan_row["series_payload"] or "{}") if plan_row else {}
            except Exception:
                payload = {}
            series_list = list(payload.get("series") or [])
            if 1 <= series_idx <= len(series_list):
                item = dict(series_list[series_idx - 1] or {})
                item.update(
                    {
                        "series_name": series_name,
                        "style_anchor": style_anchor or "",
                        "palette": palette or "",
                        "pack_archetype": pack_archetype or "",
                        "priority": priority,
                        "preview_briefs": preview_briefs,
                        "edited": True,
                    }
                )
                series_list[series_idx - 1] = item
                payload["series"] = series_list

            conn.execute(
                """
                UPDATE topic_plans
                   SET series_payload = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), int(time.time()), plan_id),
            )
            conn.commit()
            return {"plan_id": plan_id, "series_id": series_id, "series_idx": series_idx}

    def delete_plan(self, plan_id: int) -> bool:
        """Delete a plan only when no downstream preview/pack rows depend on it."""
        with _open_db(self.db_path) as conn:
            exists = conn.execute(
                "SELECT id FROM topic_plans WHERE id = ?", (plan_id,),
            ).fetchone()
            if not exists:
                return False

            previews = conn.execute(
                """
                SELECT COUNT(*) FROM pack_previews
                 WHERE series_id IN (SELECT id FROM pack_series WHERE plan_id = ?)
                """,
                (plan_id,),
            ).fetchone()[0]
            packs = conn.execute(
                """
                SELECT COUNT(*) FROM packs
                 WHERE series_id IN (SELECT id FROM pack_series WHERE plan_id = ?)
                """,
                (plan_id,),
            ).fetchone()[0]
            if previews or packs:
                raise ValueError(
                    f"plan #{plan_id} has downstream work "
                    f"({previews} previews, {packs} packs); cannot delete"
                )

            conn.execute("DELETE FROM pack_series WHERE plan_id = ?", (plan_id,))
            cur = conn.execute("DELETE FROM topic_plans WHERE id = ?", (plan_id,))
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
