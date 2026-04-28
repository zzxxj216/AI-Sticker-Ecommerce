"""Hot topic pool — write multi-source search results, query / filter for UI.

Single source of truth for the ``hot_topics`` table. Web routes call into this
service rather than touching SQL directly so the schema can move without
churning the controller layer.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router

logger = get_logger("service.hot_topics")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

# Providers wired into AIRouter today. Order matters for the UI's default
# selection — aihubmix_surfing first since it's the verified working path.
KNOWN_PROVIDERS = [
    ("aihubmix_surfing", "AiHubMix Surfing", True),   # verified W1.7
    ("openai",            "OpenAI Responses",  False), # blocked on native key
    ("tavily",            "Tavily",            False), # needs TAVILY_API_KEY
    ("perplexity",        "Perplexity",        False), # needs PERPLEXITY_API_KEY
    ("tiktok_cc",         "TikTok Creative Center (legacy)", True),  # already seeded
    ("synthesized",       "AI 合成题材 (A.1.5)", True),                  # produced by SynthesisService
]

VALID_STATUSES = ("pending", "selected", "archived")


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class HotTopicService:
    def __init__(
        self,
        router: Optional[AIRouter] = None,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.router = router or get_router()
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Write — search and persist
    # ------------------------------------------------------------------

    def search_and_persist(
        self,
        query: str,
        providers: list[str],
        *,
        region: str = "US",
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Run multi-source web_search and store every result as a hot_topics row.

        Returns a summary dict::

            {
              "inserted_total":    int,
              "by_provider":       {provider: int_inserted},
              "errors":            {provider: error_message},
              "row_ids":           [int, ...],
            }

        Each search hit becomes its own row. The W1.7 POC verified that
        AiHubMix's surfing model returns 5-10 distinct sources per query,
        each of which is a meaningful "candidate topic" (e.g. an Etsy /
        Redbubble / Pinterest URL with a topic-relevant title), so 1
        result == 1 topic is the natural mapping.

        The caller can deduplicate later by ``topic_name`` if desired —
        we don't auto-dedupe here because the same title from different
        sources is signal worth preserving.
        """
        # Filter out providers not in the known list (defensive — UI sends
        # whatever the user clicked).
        known = {p[0] for p in KNOWN_PROVIDERS if p[0] != "tiktok_cc"}
        wanted = [p for p in providers if p in known]
        if not wanted:
            return {"inserted_total": 0, "by_provider": {}, "errors": {},
                    "row_ids": [], "skipped_reason": "no valid providers selected"}

        response = self.router.web_search(
            query, providers=wanted, region=region, max_results=max_results,
        )

        inserted_ids: list[int] = []
        per_provider: dict[str, int] = {}
        now = int(time.time())

        with _open_db(self.db_path) as conn:
            for provider, results in response.by_provider.items():
                count = 0
                for rank, r in enumerate(results, 1):
                    raw = {
                        "rank": rank,
                        "title": r.title,
                        "snippet": r.snippet,
                        "raw": r.raw,
                    }
                    cur = conn.execute(
                        """
                        INSERT INTO hot_topics
                            (source, query, topic_name, raw_payload,
                             evidence_urls, hot_score, region, fetched_at, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                        """,
                        (
                            provider,
                            query,
                            (r.title or f"untitled_{provider}_{rank}")[:200],
                            json.dumps(raw, ensure_ascii=False),
                            json.dumps([r.url] if r.url else [], ensure_ascii=False),
                            float(max(1, max_results - rank + 1)) * 10.0,
                            region,
                            now,
                        ),
                    )
                    inserted_ids.append(cur.lastrowid)
                    count += 1
                per_provider[provider] = count
            conn.commit()

        return {
            "inserted_total": len(inserted_ids),
            "by_provider": per_provider,
            "errors": response.errors,
            "row_ids": inserted_ids,
        }

    # ------------------------------------------------------------------
    # Read — list / filter
    # ------------------------------------------------------------------

    def list_topics(
        self,
        *,
        source: Optional[str] = None,
        status: Optional[str] = None,
        query_substring: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return (rows, total) with optional filters and paging."""
        clauses: list[str] = []
        params: list[Any] = []
        if source and source != "all":
            clauses.append("source = ?")
            params.append(source)
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if query_substring:
            clauses.append("(topic_name LIKE ? OR query LIKE ?)")
            like = f"%{query_substring}%"
            params.extend([like, like])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        with _open_db(self.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM hot_topics {where}", tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT id, source, query, topic_name, raw_payload,
                       evidence_urls, hot_score, region, fetched_at, status
                  FROM hot_topics
                  {where}
                 ORDER BY id DESC
                 LIMIT ? OFFSET ?
                """,
                tuple(params) + (limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows], total

    def get_topic(self, topic_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                """
                SELECT id, source, query, topic_name, raw_payload,
                       evidence_urls, hot_score, region, fetched_at, status,
                       theme_summary, parent_topic_ids
                  FROM hot_topics
                 WHERE id = ?
                """,
                (topic_id,),
            ).fetchone()
            if not r:
                return None
            d = self._row_to_dict(r)
            # Resolve parent topic IDs into names so the detail page can
            # link back to the raw evidence rows.
            parents: list[dict] = []
            try:
                pids = json.loads(d.get("parent_topic_ids") or "[]")
            except Exception:
                pids = []
            if pids:
                placeholders = ",".join("?" * len(pids))
                rows = conn.execute(
                    f"""
                    SELECT id, source, topic_name FROM hot_topics
                     WHERE id IN ({placeholders})
                     ORDER BY id
                    """,
                    tuple(pids),
                ).fetchall()
                parents = [dict(p) for p in rows]
            d["parents"] = parents
        return d

    def update_status(self, topic_id: int, new_status: str) -> bool:
        if new_status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {new_status!r} (allowed: {VALID_STATUSES})")
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE hot_topics SET status = ? WHERE id = ?",
                (new_status, topic_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def source_stats(self) -> dict[str, dict[str, int]]:
        """Return {source: {pending, selected, archived, total}} for the filter UI."""
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT source, status, COUNT(*) AS n
                  FROM hot_topics
                 GROUP BY source, status
                """
            ).fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            d = out.setdefault(r["source"], {"pending": 0, "selected": 0, "archived": 0, "total": 0})
            if r["status"] in d:
                d[r["status"]] = r["n"]
            d["total"] += r["n"]
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for json_field in ("raw_payload", "evidence_urls", "parent_topic_ids"):
            if json_field not in d:
                continue
            try:
                d[json_field] = json.loads(d.get(json_field) or "{}")
            except Exception:
                pass
        return d


# ----------------------------------------------------------------------
# Module singleton
# ----------------------------------------------------------------------

_svc: Optional[HotTopicService] = None


def get_hot_topic_service() -> HotTopicService:
    global _svc
    if _svc is None:
        _svc = HotTopicService()
    return _svc
