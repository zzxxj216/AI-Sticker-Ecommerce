"""Topic synthesis service — A.1.5 of the V2 pipeline.

synthesize(topic_ids) →
  step 1 main:  cluster N raw hot_topics into 1-3 themes (markdown)
  step 2 extract: convert markdown → JSON list of themes
  insert each theme as a new hot_topic with source='synthesized',
    theme_summary=<extracted positioning + visual keywords + why>,
    parent_topic_ids=[ids of the source rows the theme was built from],
    evidence_urls=[unioned from those parent rows],
    raw_payload={original markdown + extract output}

Returned summary lets the UI redirect the operator to the synthesized rows.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router
from src.services.topic_synthesis.prompts import (
    SYNTH_EXTRACT_INSTRUCTIONS,
    SYNTH_MAIN_SYSTEM_PROMPT,
    build_synth_extract_schema,
    build_synth_main_prompt,
)

logger = get_logger("service.topic_synthesis")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")
SYNTH_SOURCE = "synthesized"
DEFAULT_MAX_THEMES = 3


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class SynthesisService:
    def __init__(
        self,
        router: Optional[AIRouter] = None,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.router = router or get_router()
        self.db_path = db_path

    def synthesize(
        self,
        topic_ids: list[int],
        *,
        extra_brief: str = "",
        max_themes: int = DEFAULT_MAX_THEMES,
        main_model: Optional[str] = None,
        extract_model: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run the two-step synthesis. Returns a summary dict.

        ``topic_ids`` must reference existing hot_topics rows (any source).
        Recommended: 2-8 inputs; <2 yields no synthesis benefit, >8 dilutes
        the signal.
        """
        if not topic_ids:
            raise ValueError("topic_ids cannot be empty")
        if len(topic_ids) > 12:
            raise ValueError(f"too many inputs ({len(topic_ids)} > 12) — pick fewer")

        with _open_db(self.db_path) as conn:
            placeholders = ",".join("?" * len(topic_ids))
            rows = conn.execute(
                f"""
                SELECT id, source, query, region, topic_name,
                       raw_payload, evidence_urls
                  FROM hot_topics WHERE id IN ({placeholders})
                """,
                tuple(topic_ids),
            ).fetchall()
        if len(rows) < len(topic_ids):
            found = {r["id"] for r in rows}
            missing = [tid for tid in topic_ids if tid not in found]
            raise ValueError(f"hot_topics not found: {missing}")

        results: list[dict] = []
        for r in rows:
            evidence = []
            try:
                evidence = json.loads(r["evidence_urls"] or "[]")
            except Exception:
                pass
            snippet = ""
            try:
                raw = json.loads(r["raw_payload"] or "{}")
                snippet = (raw.get("snippet") or "")[:400]
            except Exception:
                pass
            results.append({
                "id":             r["id"],
                "source":         r["source"],
                "topic_name":     r["topic_name"],
                "query":          r["query"],
                "evidence_urls":  evidence,
                "snippet":        snippet,
            })

        common_query = next((r["query"] for r in rows if r["query"]), "") or ""
        common_region = next((r["region"] for r in rows if r["region"]), "") or "US"

        prompt = build_synth_main_prompt(
            common_query=common_query,
            common_region=common_region,
            results=results,
            extra_brief=extra_brief,
        )

        # Step 1: main creative
        main_text = self.router.text_complete(
            prompt,
            model=main_model,
            system=SYNTH_MAIN_SYSTEM_PROMPT,
            temperature=0.7,
            task="topic_synth:main",
            related_table="hot_topics",
            related_id=topic_ids[0],
        )

        # Step 2: extract
        schema = build_synth_extract_schema(min_themes=1, max_themes=max_themes)
        try:
            payload = self.router.extract_json(
                main_text,
                schema=schema,
                instructions=SYNTH_EXTRACT_INSTRUCTIONS,
                model=extract_model,
                max_retries=1,
                task="topic_synth:extract",
                related_table="hot_topics",
                related_id=topic_ids[0],
            )
        except Exception as e:
            logger.exception("synth extract failed")
            return {"created": [], "extract_ok": False,
                    "main_chars": len(main_text), "error": str(e)[:500]}

        themes = payload.get("themes") or []
        if not themes:
            return {"created": [], "extract_ok": True,
                    "main_chars": len(main_text),
                    "error": "extractor returned 0 themes"}

        # Map 1-based evidence_ids → actual hot_topic IDs and union URLs
        id_by_idx = {idx: results[idx - 1]["id"] for idx in range(1, len(results) + 1)}
        urls_by_id = {r["id"]: r["evidence_urls"] for r in results}

        created_ids: list[int] = []
        now = int(time.time())
        with _open_db(self.db_path) as conn:
            for theme in themes:
                ev_idxs = theme.get("evidence_ids") or []
                parent_ids = sorted({id_by_idx[i] for i in ev_idxs
                                     if isinstance(i, int) and i in id_by_idx})
                # Fallback: if extractor gave no IDs, use ALL inputs
                if not parent_ids:
                    parent_ids = sorted(id_by_idx.values())
                merged_urls: list[str] = []
                seen = set()
                for pid in parent_ids:
                    for u in urls_by_id.get(pid, []):
                        if u and u not in seen:
                            seen.add(u)
                            merged_urls.append(u)

                # theme_summary is the markdown the operator will read
                kw = ", ".join(theme.get("key_visual_keywords") or [])
                summary = (
                    f"**定位**: {theme.get('positioning_cn', '')}\n\n"
                    f"**Target audience (en)**: {theme.get('target_audience_en', '')}\n\n"
                    f"**Visual keywords**: {kw}\n\n"
                    f"**Why commercial**: {theme.get('why_commercial_cn', '')}\n\n"
                    f"**Priority**: {theme.get('priority', 'medium')}"
                )
                raw = {
                    "synthesis_payload": theme,
                    "main_raw_text":     main_text,
                    "common_query":      common_query,
                    "common_region":     common_region,
                }
                # hot_score from priority so the list orders sensibly
                priority = (theme.get("priority") or "medium").lower()
                hot_score = {"high": 100.0, "medium": 60.0, "low": 30.0}.get(priority, 60.0)

                cur = conn.execute(
                    """
                    INSERT INTO hot_topics
                        (source, query, topic_name, raw_payload,
                         evidence_urls, hot_score, region, fetched_at, status,
                         theme_summary, parent_topic_ids)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        SYNTH_SOURCE,
                        common_query,
                        (theme.get("theme_name") or "untitled_theme")[:200],
                        json.dumps(raw, ensure_ascii=False),
                        json.dumps(merged_urls, ensure_ascii=False),
                        hot_score,
                        common_region,
                        now,
                        summary,
                        json.dumps(parent_ids, ensure_ascii=False),
                    ),
                )
                created_ids.append(cur.lastrowid)
            conn.commit()

        logger.info("synthesized %d themes from %d inputs → %s",
                    len(created_ids), len(topic_ids), created_ids)
        return {
            "created":     created_ids,
            "extract_ok":  True,
            "main_chars":  len(main_text),
            "n_inputs":    len(topic_ids),
            "n_themes":    len(created_ids),
        }


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[SynthesisService] = None


def get_synthesis_service() -> SynthesisService:
    global _svc
    if _svc is None:
        _svc = SynthesisService()
    return _svc
