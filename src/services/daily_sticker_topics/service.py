"""Daily web collector for sticker-pack-ready hot topics.

This module is intentionally self-contained: every daily run starts from fresh
web-search results, ranks them into three sticker-pack candidates, saves source
metadata plus downloaded reference images, and mirrors the final topics into
``hot_topics`` so the existing V2 planning pipeline can pick them up.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlparse

import requests
from PIL import Image, UnidentifiedImageError

from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router

logger = get_logger("service.daily_sticker_topics")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")
DEFAULT_STORAGE_ROOT = Path("data/daily_sticker_topics")
DAILY_STICKER_TOPIC_TASK_ID = "daily_sticker_topic_collect"
DEFAULT_DAILY_STICKER_TOPIC_PROVIDERS = ["aihubmix_surfing"]

_CN_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class CandidateSource:
    index: int
    query: str
    provider: str
    rank: int
    title: str
    url: str
    snippet: str = ""
    raw: dict[str, Any] | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "query": self.query,
            "provider": self.provider,
            "rank": self.rank,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
        }


def _now_ts() -> int:
    return int(time.time())


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _slugify(text: str, fallback: str = "topic") -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
    text = text.strip("-")[:70]
    return text or fallback


def _is_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class DailyStickerTopicService:
    """Collect and persist three daily hot topics suitable for sticker packs."""

    def __init__(
        self,
        router: Optional[AIRouter] = None,
        db_path: Path = DEFAULT_DB_PATH,
        storage_root: Path = DEFAULT_STORAGE_ROOT,
    ) -> None:
        self.router = router or get_router()
        self.db_path = db_path
        self.storage_root = storage_root

    # ------------------------------------------------------------------
    # DB / schema
    # ------------------------------------------------------------------

    def _open_db(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def ensure_schema(self) -> None:
        """Create the daily collector tables if migrations have not run yet."""
        with closing(self._open_db()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS daily_sticker_topic_runs (
                  id              TEXT PRIMARY KEY,
                  run_date        TEXT NOT NULL,
                  started_at      INTEGER NOT NULL,
                  finished_at     INTEGER,
                  status          TEXT NOT NULL DEFAULT 'running',
                  region          TEXT DEFAULT 'US',
                  providers       TEXT DEFAULT '[]',
                  queries         TEXT DEFAULT '[]',
                  total_results   INTEGER DEFAULT 0,
                  selected_count  INTEGER DEFAULT 0,
                  storage_dir     TEXT DEFAULT '',
                  error           TEXT DEFAULT '',
                  raw_summary     TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_daily_sticker_runs_date
                  ON daily_sticker_topic_runs(run_date, started_at);

                CREATE TABLE IF NOT EXISTS daily_sticker_topics (
                  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id                     TEXT NOT NULL REFERENCES daily_sticker_topic_runs(id),
                  rank                       INTEGER NOT NULL,
                  topic_key                  TEXT DEFAULT '',
                  title                      TEXT NOT NULL,
                  summary                    TEXT DEFAULT '',
                  reason_for_sticker_pack    TEXT DEFAULT '',
                  sticker_ideas              TEXT DEFAULT '[]',
                  keywords                   TEXT DEFAULT '[]',
                  source_urls                TEXT DEFAULT '[]',
                  score_json                 TEXT DEFAULT '{}',
                  risk_notes                 TEXT DEFAULT '',
                  metadata_path              TEXT DEFAULT '',
                  hot_topic_id               INTEGER,
                  created_at                 INTEGER NOT NULL,
                  UNIQUE(run_id, rank)
                );
                CREATE INDEX IF NOT EXISTS idx_daily_sticker_topics_run
                  ON daily_sticker_topics(run_id, rank);
                CREATE INDEX IF NOT EXISTS idx_daily_sticker_topics_hot_topic
                  ON daily_sticker_topics(hot_topic_id);

                CREATE TABLE IF NOT EXISTS daily_sticker_topic_images (
                  id             INTEGER PRIMARY KEY AUTOINCREMENT,
                  topic_id       INTEGER NOT NULL REFERENCES daily_sticker_topics(id),
                  source_url     TEXT NOT NULL,
                  local_path     TEXT NOT NULL,
                  public_url     TEXT DEFAULT '',
                  mime_type      TEXT DEFAULT '',
                  file_hash      TEXT DEFAULT '',
                  size_bytes     INTEGER DEFAULT 0,
                  width          INTEGER DEFAULT 0,
                  height         INTEGER DEFAULT 0,
                  created_at     INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_daily_sticker_images_topic
                  ON daily_sticker_topic_images(topic_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_sticker_images_topic_hash
                  ON daily_sticker_topic_images(topic_id, file_hash);

                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                  id              INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_name        TEXT    NOT NULL,
                  started_at      INTEGER NOT NULL,
                  finished_at     INTEGER,
                  status          TEXT    NOT NULL DEFAULT 'running',
                  affected_rows   INTEGER DEFAULT 0,
                  error           TEXT    DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_name_time
                  ON scheduled_jobs(job_name, started_at);
                """
            )
            # Idempotent ALTER for dismissed_at — older DBs may not have it.
            cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_sticker_topics)").fetchall()}
            if "dismissed_at" not in cols:
                conn.execute("ALTER TABLE daily_sticker_topics ADD COLUMN dismissed_at INTEGER")
            conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_daily_collect(
        self,
        *,
        run_date: str | None = None,
        region: str = "US",
        providers: list[str] | None = None,
        max_results_per_query: int = 5,
        topic_count: int = 3,
        max_images_per_topic: int = 3,
        force: bool = False,
    ) -> dict[str, Any]:
        """Run the daily collector.

        ``force=False`` makes scheduler ticks idempotent for the same date:
        if a completed run already has three topics, the service returns the
        latest run instead of spending another search pass.
        """
        self.ensure_schema()
        run_date = run_date or datetime.now(_CN_TZ).date().isoformat()
        providers = self._normalize_providers(providers)
        max_results_per_query = min(max(1, int(max_results_per_query or 5)), 10)
        topic_count = min(max(1, int(topic_count or 3)), 5)
        max_images_per_topic = min(max(0, int(max_images_per_topic or 0)), 8)

        if not force:
            existing = self.latest_run(run_date=run_date)
            if existing and existing.get("status") == "completed" and existing.get("selected_count", 0) >= topic_count:
                logger.info("daily sticker topics skipped: completed run already exists for %s", run_date)
                return {
                    "skipped": True,
                    "reason": "completed run already exists for this date",
                    "run": existing,
                }

        started_at = _now_ts()
        run_id = f"{run_date.replace('-', '')}-{started_at}"
        queries = self._build_queries(run_date)
        storage_dir = self.storage_root / run_date
        storage_dir.mkdir(parents=True, exist_ok=True)

        scheduled_job_id = self._record_scheduled_job_start(started_at)
        self._insert_run(
            run_id=run_id,
            run_date=run_date,
            started_at=started_at,
            region=region,
            providers=providers,
            queries=queries,
            storage_dir=storage_dir,
        )

        try:
            candidates, search_errors = self._search_all(
                queries=queries,
                providers=providers,
                region=region,
                max_results_per_query=max_results_per_query,
            )
            if not candidates:
                detail = "; ".join(search_errors.values())[:500] if search_errors else "no results"
                raise RuntimeError(f"daily sticker topic search returned no candidates ({detail})")
            selected_topics = self._select_topics(candidates, topic_count=topic_count, run_date=run_date)
            if not selected_topics:
                raise RuntimeError("daily sticker topic ranking returned no usable topics")

            saved_topic_ids: list[int] = []
            hot_topic_ids: list[int] = []
            for rank, topic in enumerate(selected_topics[:topic_count], 1):
                topic_id, hot_topic_id = self._persist_topic(
                    run_id=run_id,
                    rank=rank,
                    topic=topic,
                    candidates=candidates,
                    region=region,
                    run_storage_dir=storage_dir,
                )
                saved_topic_ids.append(topic_id)
                if hot_topic_id:
                    hot_topic_ids.append(hot_topic_id)

                if max_images_per_topic > 0:
                    self._collect_and_save_images(
                        topic_id=topic_id,
                        topic=topic,
                        candidates=candidates,
                        max_images=max_images_per_topic,
                    )
                self._write_topic_metadata(topic_id)

            raw_summary = {
                "search_errors": search_errors,
                "candidate_count": len(candidates),
                "saved_topic_ids": saved_topic_ids,
                "hot_topic_ids": hot_topic_ids,
            }
            self._finish_run(
                run_id=run_id,
                status="completed",
                total_results=len(candidates),
                selected_count=len(saved_topic_ids),
                raw_summary=raw_summary,
            )
            self._record_scheduled_job_finish(
                scheduled_job_id,
                status="completed",
                affected_rows=len(saved_topic_ids),
            )
            return {
                "run_id": run_id,
                "status": "completed",
                "total_results": len(candidates),
                "selected_count": len(saved_topic_ids),
                "topic_ids": saved_topic_ids,
                "hot_topic_ids": hot_topic_ids,
                "errors": search_errors,
            }
        except Exception as exc:
            logger.exception("daily sticker topic collect failed")
            self._finish_run(
                run_id=run_id,
                status="failed",
                error=str(exc),
                raw_summary={"error": str(exc)},
            )
            self._record_scheduled_job_finish(
                scheduled_job_id,
                status="failed",
                error=str(exc),
            )
            raise

    def latest_run(self, *, run_date: str | None = None) -> dict[str, Any] | None:
        self.ensure_schema()
        with closing(self._open_db()) as conn:
            if run_date:
                row = conn.execute(
                    """
                    SELECT * FROM daily_sticker_topic_runs
                     WHERE run_date = ?
                     ORDER BY started_at DESC
                     LIMIT 1
                    """,
                    (run_date,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM daily_sticker_topic_runs
                     ORDER BY started_at DESC
                     LIMIT 1
                    """
                ).fetchone()
            if not row:
                return None
            run = self._run_row_to_dict(row)
            run["topics"] = self._list_topics_for_run(conn, run["id"])
            return run

    def dismiss_topic(self, topic_id: int) -> bool:
        """Mark a single daily-collect card as hidden. Does NOT touch the
        underlying hot_topics row — operator may still want to act on it via
        /v2/hot-topics/{id}.
        """
        self.ensure_schema()
        with closing(self._open_db()) as conn:
            cur = conn.execute(
                "UPDATE daily_sticker_topics SET dismissed_at = ? "
                "WHERE id = ? AND dismissed_at IS NULL",
                (_now_ts(), int(topic_id)),
            )
            conn.commit()
            return cur.rowcount > 0

    def dismiss_run(self, run_id: str) -> int:
        """Hide every still-visible card for this run. Returns rows hidden."""
        self.ensure_schema()
        with closing(self._open_db()) as conn:
            cur = conn.execute(
                "UPDATE daily_sticker_topics SET dismissed_at = ? "
                "WHERE run_id = ? AND dismissed_at IS NULL",
                (_now_ts(), str(run_id)),
            )
            conn.commit()
            return cur.rowcount

    def list_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        self.ensure_schema()
        limit = min(max(1, int(limit or 10)), 100)
        with closing(self._open_db()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM daily_sticker_topic_runs
                 ORDER BY started_at DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._run_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Search / ranking
    # ------------------------------------------------------------------

    def _normalize_providers(self, providers: list[str] | None) -> list[str]:
        providers = providers or DEFAULT_DAILY_STICKER_TOPIC_PROVIDERS
        allowed = {"aihubmix_surfing", "openai", "tavily", "perplexity"}
        out = [p for p in providers if p in allowed]
        return out or DEFAULT_DAILY_STICKER_TOPIC_PROVIDERS

    def _build_queries(self, run_date: str) -> list[str]:
        display_date = run_date
        try:
            display_date = datetime.fromisoformat(run_date).strftime("%B %d, %Y")
        except ValueError:
            pass
        return [
            f"{display_date} viral meme trends sticker pack ideas",
            f"{display_date} TikTok meme trends reaction stickers",
            f"{display_date} cute viral animal meme trend stickers",
            f"{display_date} trending slang memes social media stickers",
            f"{display_date} pop culture internet trend sticker ideas",
            "today trending reaction memes cute sticker pack ideas",
        ]

    def _search_all(
        self,
        *,
        queries: list[str],
        providers: list[str],
        region: str,
        max_results_per_query: int,
    ) -> tuple[list[CandidateSource], dict[str, str]]:
        candidates: list[CandidateSource] = []
        errors: dict[str, str] = {}
        seen_urls: set[str] = set()

        for query in queries:
            response = self.router.web_search(
                query,
                providers=providers,
                region=region,
                max_results=max_results_per_query,
                related_table="daily_sticker_topic_runs",
            )
            for provider, err in response.errors.items():
                errors[f"{provider}:{query[:48]}"] = err
            for provider, results in response.by_provider.items():
                for rank, result in enumerate(results, 1):
                    url = (result.url or "").strip()
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    candidates.append(
                        CandidateSource(
                            index=len(candidates) + 1,
                            query=query,
                            provider=provider,
                            rank=rank,
                            title=(result.title or "").strip()[:240],
                            url=url,
                            snippet=(result.snippet or "").strip()[:800],
                            raw=result.raw or {},
                        )
                    )
        return candidates, errors

    def _select_topics(
        self,
        candidates: list[CandidateSource],
        *,
        topic_count: int,
        run_date: str,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        try:
            selected = self._select_topics_with_ai(candidates, topic_count=topic_count, run_date=run_date)
            if selected:
                if len(selected) >= topic_count:
                    return selected[:topic_count]
                # Fill any AI shortfall with deterministic candidates so the
                # daily job still tries to produce three topics.
                seen = {_slugify(t.get("title") or "") for t in selected}
                for fallback in self._select_topics_with_heuristics(candidates, topic_count=topic_count):
                    key = _slugify(fallback.get("title") or "")
                    if key in seen:
                        continue
                    selected.append(fallback)
                    seen.add(key)
                    if len(selected) >= topic_count:
                        break
                return selected[:topic_count]
        except Exception:
            logger.exception("AI topic selection failed; falling back to heuristic ranking")
        return self._select_topics_with_heuristics(candidates, topic_count=topic_count)

    def _select_topics_with_ai(
        self,
        candidates: list[CandidateSource],
        *,
        topic_count: int,
        run_date: str,
    ) -> list[dict[str, Any]]:
        schema = {
            "topics": [
                {
                    "title": "string",
                    "summary": "string",
                    "reason_for_sticker_pack": "string",
                    "sticker_ideas": ["string"],
                    "keywords": ["string"],
                    "source_indexes": [1],
                    "source_urls": ["string"],
                    "risk_notes": "string",
                    "score": {
                        "trendScore": 0,
                        "visualScore": 0,
                        "emotionScore": 0,
                        "safetyScore": 0,
                        "commercialScore": 0,
                        "totalScore": 0,
                    },
                }
            ],
            "rejected_notes": ["string"],
        }
        prompt_payload = {
            "date": run_date,
            "goal": f"Select exactly {topic_count} fresh hot topics that can become sticker packs.",
            "source_results": [c.to_prompt_dict() for c in candidates],
        }
        instructions = (
            f"Select exactly {topic_count} topics if enough safe candidates exist. "
            "Merge duplicate sources into one topic. Prefer topics with strong visual/emotion potential "
            "for cute, funny, relatable sticker packs. Avoid politics, disasters, violence, death, "
            "celebrity likeness, trademarked brands, and copyrighted IP. "
            "Use concise Chinese for title/summary/reason/sticker_ideas. "
            "Score each dimension from 1 to 10 and set totalScore to their sum. "
            "source_indexes must reference the provided source_results indexes."
        )
        parsed = self.router.extract_json(
            json.dumps(prompt_payload, ensure_ascii=False, indent=2),
            schema,
            instructions=instructions,
            task="daily_sticker_topic_select",
            related_table="daily_sticker_topic_runs",
        )
        topics = parsed.get("topics") if isinstance(parsed, dict) else []
        if not isinstance(topics, list):
            return []
        normalized = [
            self._normalize_selected_topic(t, candidates)
            for t in topics
            if isinstance(t, dict)
        ]
        return [t for t in normalized if t.get("title")]

    def _select_topics_with_heuristics(
        self,
        candidates: list[CandidateSource],
        *,
        topic_count: int,
    ) -> list[dict[str, Any]]:
        risky = re.compile(
            r"\b(politic|election|war|shooting|death|dead|lawsuit|trademark|copyright|"
            r"celebrity|brand|disaster|attack|murder|stock|crypto)\b",
            re.I,
        )
        good = re.compile(
            r"\b(meme|viral|cute|animal|cat|dog|reaction|slang|funny|trend|tiktok|"
            r"emoji|sticker|office|school|food|cozy|wholesome)\b",
            re.I,
        )
        scored: list[tuple[int, CandidateSource]] = []
        for c in candidates:
            text = f"{c.title} {c.snippet} {c.query}"
            if risky.search(text):
                continue
            score = 40 + max(0, 12 - c.rank * 2)
            score += min(30, len(good.findall(text)) * 5)
            if c.url:
                score += 5
            scored.append((score, c))
        scored.sort(key=lambda item: item[0], reverse=True)

        out: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for score, c in scored:
            title = c.title or c.snippet or c.query
            key = _slugify(title)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(
                self._normalize_selected_topic(
                    {
                        "title": title[:80],
                        "summary": c.snippet or f"Candidate found from search query: {c.query}",
                        "reason_for_sticker_pack": (
                            "This candidate has meme/reaction potential and can be split into "
                            "multiple chat-scenario stickers."
                        ),
                        "sticker_ideas": [
                            "Got it",
                            "So cute",
                            "Tiny meltdown",
                            "Nodding hard",
                            "Let's go",
                        ],
                        "keywords": [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", title)[:6]],
                        "source_indexes": [c.index],
                        "source_urls": [c.url] if c.url else [],
                        "risk_notes": (
                            "Use sources for inspiration only; confirm copyright and do not "
                            "commercially reuse downloaded images directly."
                        ),
                        "score": {
                            "trendScore": min(10, max(1, score // 10)),
                            "visualScore": 7,
                            "emotionScore": 7,
                            "safetyScore": 8,
                            "commercialScore": 7,
                            "totalScore": min(50, max(1, score // 2)),
                        },
                    },
                    candidates,
                )
            )
            if len(out) >= topic_count:
                break
        return out

    def _normalize_selected_topic(
        self,
        raw_topic: dict[str, Any],
        candidates: list[CandidateSource],
    ) -> dict[str, Any]:
        by_index = {c.index: c for c in candidates}
        source_indexes = [
            _safe_int(x, 0)
            for x in raw_topic.get("source_indexes", []) or []
            if _safe_int(x, 0) in by_index
        ]
        urls: list[str] = []
        for idx in source_indexes:
            url = by_index[idx].url
            if url and url not in urls:
                urls.append(url)
        for url in raw_topic.get("source_urls", []) or []:
            if isinstance(url, str) and url.strip() and url.strip() not in urls:
                urls.append(url.strip())
        if not source_indexes and urls:
            for c in candidates:
                if c.url in urls:
                    source_indexes.append(c.index)

        sticker_ideas = [
            str(x).strip()
            for x in (raw_topic.get("sticker_ideas") or [])
            if str(x).strip()
        ][:12]
        keywords = [
            str(x).strip()
            for x in (raw_topic.get("keywords") or [])
            if str(x).strip()
        ][:12]
        score = raw_topic.get("score") if isinstance(raw_topic.get("score"), dict) else {}
        total = sum(
            int(score.get(k) or 0)
            for k in ["trendScore", "visualScore", "emotionScore", "safetyScore", "commercialScore"]
        )
        if total and not score.get("totalScore"):
            score["totalScore"] = total
        return {
            "title": str(raw_topic.get("title") or "").strip()[:200],
            "summary": str(raw_topic.get("summary") or "").strip(),
            "reason_for_sticker_pack": str(raw_topic.get("reason_for_sticker_pack") or "").strip(),
            "sticker_ideas": sticker_ideas,
            "keywords": keywords,
            "source_indexes": source_indexes,
            "source_urls": urls[:8],
            "risk_notes": str(raw_topic.get("risk_notes") or "").strip(),
            "score": score,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _insert_run(
        self,
        *,
        run_id: str,
        run_date: str,
        started_at: int,
        region: str,
        providers: list[str],
        queries: list[str],
        storage_dir: Path,
    ) -> None:
        with closing(self._open_db()) as conn:
            conn.execute(
                """
                INSERT INTO daily_sticker_topic_runs
                    (id, run_date, started_at, status, region, providers, queries, storage_dir)
                VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    run_date,
                    started_at,
                    region,
                    _json_dumps(providers),
                    _json_dumps(queries),
                    str(storage_dir),
                ),
            )
            conn.commit()

    def _finish_run(
        self,
        *,
        run_id: str,
        status: str,
        total_results: int = 0,
        selected_count: int = 0,
        error: str = "",
        raw_summary: dict[str, Any] | None = None,
    ) -> None:
        with closing(self._open_db()) as conn:
            conn.execute(
                """
                UPDATE daily_sticker_topic_runs
                   SET finished_at = ?, status = ?, total_results = ?,
                       selected_count = ?, error = ?, raw_summary = ?
                 WHERE id = ?
                """,
                (
                    _now_ts(),
                    status,
                    total_results,
                    selected_count,
                    error,
                    _json_dumps(raw_summary or {}),
                    run_id,
                ),
            )
            conn.commit()

    def _persist_topic(
        self,
        *,
        run_id: str,
        rank: int,
        topic: dict[str, Any],
        candidates: list[CandidateSource],
        region: str,
        run_storage_dir: Path,
    ) -> tuple[int, int | None]:
        topic_key = f"{rank:02d}-{_slugify(topic.get('title') or '', fallback='topic')}"
        topic_dir = run_storage_dir / topic_key
        (topic_dir / "images").mkdir(parents=True, exist_ok=True)
        metadata_path = topic_dir / "metadata.json"

        source_rows = [
            c.to_prompt_dict()
            for c in candidates
            if c.index in set(topic.get("source_indexes") or [])
        ]
        hot_topic_id = self._insert_hot_topic(topic, source_rows, region)

        with closing(self._open_db()) as conn:
            cur = conn.execute(
                """
                INSERT INTO daily_sticker_topics
                    (run_id, rank, topic_key, title, summary, reason_for_sticker_pack,
                     sticker_ideas, keywords, source_urls, score_json, risk_notes,
                     metadata_path, hot_topic_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    rank,
                    topic_key,
                    topic.get("title") or "",
                    topic.get("summary") or "",
                    topic.get("reason_for_sticker_pack") or "",
                    _json_dumps(topic.get("sticker_ideas") or []),
                    _json_dumps(topic.get("keywords") or []),
                    _json_dumps(topic.get("source_urls") or []),
                    _json_dumps(topic.get("score") or {}),
                    topic.get("risk_notes") or "",
                    str(metadata_path),
                    hot_topic_id,
                    _now_ts(),
                ),
            )
            topic_id = int(cur.lastrowid)
            conn.commit()
        return topic_id, hot_topic_id

    def _insert_hot_topic(
        self,
        topic: dict[str, Any],
        source_rows: list[dict[str, Any]],
        region: str,
    ) -> int | None:
        """Mirror the selected daily topic into the existing hot topic pool."""
        title = (topic.get("title") or "").strip()[:200]
        if not title:
            return None
        raw_payload = {
            "daily_sticker_topic": True,
            "summary": topic.get("summary") or "",
            "reason_for_sticker_pack": topic.get("reason_for_sticker_pack") or "",
            "sticker_ideas": topic.get("sticker_ideas") or [],
            "keywords": topic.get("keywords") or [],
            "risk_notes": topic.get("risk_notes") or "",
            "score": topic.get("score") or {},
            "sources": source_rows,
        }
        evidence_urls = topic.get("source_urls") or []
        hot_score = float((topic.get("score") or {}).get("totalScore") or 0)
        theme_summary = "\n".join(
            x
            for x in [
                topic.get("summary") or "",
                topic.get("reason_for_sticker_pack") or "",
                "Sticker ideas: " + " / ".join(topic.get("sticker_ideas") or []),
            ]
            if x
        )

        try:
            with closing(self._open_db()) as conn:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='hot_topics'"
                ).fetchone()
                if not tables:
                    return None
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(hot_topics)").fetchall()}
                values: dict[str, Any] = {
                    "source": "daily_sticker_trend",
                    "query": "daily sticker topic collector",
                    "topic_name": title,
                    "raw_payload": _json_dumps(raw_payload),
                    "evidence_urls": _json_dumps(evidence_urls),
                    "hot_score": hot_score,
                    "region": region,
                    "fetched_at": _now_ts(),
                    "status": "pending",
                }
                if "theme_summary" in cols:
                    values["theme_summary"] = theme_summary
                if "parent_topic_ids" in cols:
                    values["parent_topic_ids"] = "[]"
                insert_cols = [c for c in values if c in cols]
                placeholders = ", ".join("?" for _ in insert_cols)
                sql = (
                    f"INSERT INTO hot_topics ({', '.join(insert_cols)}) "
                    f"VALUES ({placeholders})"
                )
                cur = conn.execute(sql, tuple(values[c] for c in insert_cols))
                conn.commit()
                return int(cur.lastrowid)
        except Exception:
            logger.exception("failed to mirror daily sticker topic into hot_topics")
            return None

    def _record_scheduled_job_start(self, started_at: int) -> int | None:
        try:
            with closing(self._open_db()) as conn:
                cur = conn.execute(
                    """
                    INSERT INTO scheduled_jobs(job_name, started_at, status)
                    VALUES (?, ?, 'running')
                    """,
                    (DAILY_STICKER_TOPIC_TASK_ID, started_at),
                )
                conn.commit()
                return int(cur.lastrowid)
        except Exception:
            logger.exception("failed to record scheduled job start")
            return None

    def _record_scheduled_job_finish(
        self,
        job_id: int | None,
        *,
        status: str,
        affected_rows: int = 0,
        error: str = "",
    ) -> None:
        if not job_id:
            return
        try:
            with closing(self._open_db()) as conn:
                conn.execute(
                    """
                    UPDATE scheduled_jobs
                       SET finished_at = ?, status = ?, affected_rows = ?, error = ?
                     WHERE id = ?
                    """,
                    (_now_ts(), status, affected_rows, error[:1000], job_id),
                )
                conn.commit()
        except Exception:
            logger.exception("failed to record scheduled job finish")

    # ------------------------------------------------------------------
    # Image collection
    # ------------------------------------------------------------------

    def _collect_and_save_images(
        self,
        *,
        topic_id: int,
        topic: dict[str, Any],
        candidates: list[CandidateSource],
        max_images: int,
    ) -> list[dict[str, Any]]:
        topic_row = self._get_topic_row(topic_id)
        if not topic_row:
            return []
        topic_dir = Path(topic_row["metadata_path"]).parent
        image_dir = topic_dir / "images"
        image_dir.mkdir(parents=True, exist_ok=True)

        candidate_urls = self._image_candidate_urls(topic, candidates)
        saved: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        session = self._requests_session()

        for image_url in candidate_urls:
            if len(saved) >= max_images:
                break
            try:
                image = self._download_image(session, image_url)
            except Exception:
                logger.debug("image download failed: %s", image_url, exc_info=True)
                continue
            if not image:
                continue
            if image["file_hash"] in seen_hashes:
                continue
            seen_hashes.add(image["file_hash"])
            ext = image["ext"]
            filename = f"{len(saved) + 1:02d}-{image['file_hash'][:10]}.{ext}"
            path = image_dir / filename
            path.write_bytes(image["content"])
            public_url = self._public_asset_url(path)
            row = {
                "source_url": image_url,
                "local_path": str(path),
                "public_url": public_url,
                "mime_type": image["mime_type"],
                "file_hash": image["file_hash"],
                "size_bytes": len(image["content"]),
                "width": image["width"],
                "height": image["height"],
            }
            with closing(self._open_db()) as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO daily_sticker_topic_images
                          (topic_id, source_url, local_path, public_url, mime_type,
                           file_hash, size_bytes, width, height, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            topic_id,
                            row["source_url"],
                            row["local_path"],
                            row["public_url"],
                            row["mime_type"],
                            row["file_hash"],
                            row["size_bytes"],
                            row["width"],
                            row["height"],
                            _now_ts(),
                        ),
                    )
                    conn.commit()
                except sqlite3.IntegrityError:
                    continue
            saved.append(row)
        return saved

    def _image_candidate_urls(
        self,
        topic: dict[str, Any],
        candidates: list[CandidateSource],
    ) -> list[str]:
        urls: list[str] = []
        source_urls = list(topic.get("source_urls") or [])
        source_indexes = set(topic.get("source_indexes") or [])
        for c in candidates:
            if c.index in source_indexes and c.url and c.url not in source_urls:
                source_urls.append(c.url)

        session = self._requests_session()
        for source_url in source_urls[:8]:
            if not _is_http_url(source_url):
                continue
            if self._looks_like_image_url(source_url):
                urls.append(source_url)
                continue
            try:
                html_text, final_url = self._fetch_html(session, source_url)
            except Exception:
                logger.debug("source html fetch failed: %s", source_url, exc_info=True)
                continue
            for image_url in self._extract_image_urls_from_html(html_text, final_url or source_url):
                if image_url not in urls:
                    urls.append(image_url)
                if len(urls) >= 24:
                    return urls
        return urls

    def _requests_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            }
        )
        return session

    def _fetch_html(self, session: requests.Session, url: str) -> tuple[str, str]:
        resp = session.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").lower()
        if content_type and "html" not in content_type and "text/" not in content_type:
            return "", resp.url
        resp.encoding = resp.encoding or "utf-8"
        return resp.text[:700_000], resp.url

    def _extract_image_urls_from_html(self, html_text: str, base_url: str) -> list[str]:
        if not html_text:
            return []
        found: list[str] = []
        meta_priority = {
            "og:image",
            "og:image:url",
            "og:image:secure_url",
            "twitter:image",
            "twitter:image:src",
        }
        for tag in re.findall(r"<meta\b[^>]*>", html_text, flags=re.I):
            attrs = self._html_attrs(tag)
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            content = attrs.get("content") or ""
            if key in meta_priority and content:
                resolved = self._resolve_image_url(content, base_url)
                if resolved and resolved not in found:
                    found.append(resolved)
        for tag in re.findall(r"<img\b[^>]*>", html_text, flags=re.I):
            attrs = self._html_attrs(tag)
            src = (
                attrs.get("src")
                or attrs.get("data-src")
                or attrs.get("data-original")
                or attrs.get("data-lazy-src")
                or ""
            )
            if not src and attrs.get("srcset"):
                src = attrs["srcset"].split(",", 1)[0].strip().split(" ", 1)[0]
            resolved = self._resolve_image_url(src, base_url)
            if resolved and resolved not in found:
                found.append(resolved)
            if len(found) >= 24:
                break
        return found

    def _html_attrs(self, tag: str) -> dict[str, str]:
        attrs: dict[str, str] = {}
        for key, _quote, value in re.findall(
            r"([:\w-]+)\s*=\s*(['\"])(.*?)\2",
            tag,
            flags=re.I | re.S,
        ):
            attrs[key.lower()] = html.unescape(value.strip())
        return attrs

    def _resolve_image_url(self, raw_url: str, base_url: str) -> str:
        raw_url = html.unescape((raw_url or "").strip())
        if not raw_url or raw_url.startswith("data:") or raw_url.startswith("blob:"):
            return ""
        resolved = urljoin(base_url, raw_url)
        if not _is_http_url(resolved):
            return ""
        return resolved

    def _download_image(self, session: requests.Session, url: str) -> dict[str, Any] | None:
        max_bytes = int(os.getenv("DAILY_STICKER_TOPIC_MAX_IMAGE_MB", "10")) * 1024 * 1024
        resp = session.get(url, timeout=20, allow_redirects=True, stream=True)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].lower()
        if content_type and not content_type.startswith("image/"):
            return None
        if content_type in {"image/svg+xml", "image/x-icon"}:
            return None
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
        content = b"".join(chunks)
        if len(content) < 256:
            return None

        file_hash = hashlib.sha256(content).hexdigest()
        width = height = 0
        try:
            with Image.open(BytesIO(content)) as img:
                width, height = img.size
                img_format = (img.format or "").lower()
        except UnidentifiedImageError:
            return None
        except Exception:
            logger.debug("image dimension check failed: %s", url, exc_info=True)
            img_format = ""

        if width and height and (width < 80 or height < 80):
            return None
        ext = self._image_ext(content_type, img_format, url)
        if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
            return None
        if ext == "jpeg":
            ext = "jpg"
        return {
            "content": content,
            "file_hash": file_hash,
            "mime_type": content_type or f"image/{ext}",
            "width": width,
            "height": height,
            "ext": ext,
        }

    def _image_ext(self, content_type: str, img_format: str, url: str) -> str:
        mapping = {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
        }
        if content_type in mapping:
            return mapping[content_type]
        if img_format:
            return "jpg" if img_format == "jpeg" else img_format.lower()
        suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
        return suffix[:5] if suffix else "jpg"

    def _looks_like_image_url(self, url: str) -> bool:
        suffix = Path(urlparse(url).path).suffix.lower()
        return suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    def _public_asset_url(self, path: Path) -> str:
        try:
            rel = path.resolve().relative_to(self.storage_root.resolve())
        except Exception:
            rel = path
        return "/daily-sticker-assets/" + quote(rel.as_posix())

    # ------------------------------------------------------------------
    # Read/decorate
    # ------------------------------------------------------------------

    def _get_topic_row(self, topic_id: int) -> sqlite3.Row | None:
        with closing(self._open_db()) as conn:
            return conn.execute(
                "SELECT * FROM daily_sticker_topics WHERE id = ?",
                (topic_id,),
            ).fetchone()

    def _write_topic_metadata(self, topic_id: int) -> None:
        with closing(self._open_db()) as conn:
            row = conn.execute(
                "SELECT * FROM daily_sticker_topics WHERE id = ?",
                (topic_id,),
            ).fetchone()
            if not row:
                return
            topic = self._topic_row_to_dict(row)
            topic["images"] = self._images_for_topic(conn, topic_id)
        metadata_path = Path(topic.get("metadata_path") or "")
        if not metadata_path:
            return
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(topic, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["providers"] = _json_loads(d.get("providers"), [])
        d["queries"] = _json_loads(d.get("queries"), [])
        d["raw_summary"] = _json_loads(d.get("raw_summary"), {})
        d["started_human"] = self._fmt_ts(d.get("started_at"))
        d["finished_human"] = self._fmt_ts(d.get("finished_at"))
        d["duration_s"] = (
            int(d["finished_at"] - d["started_at"])
            if d.get("finished_at") and d.get("started_at")
            else None
        )
        return d

    def _topic_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["sticker_ideas"] = _json_loads(d.get("sticker_ideas"), [])
        d["keywords"] = _json_loads(d.get("keywords"), [])
        d["source_urls"] = _json_loads(d.get("source_urls"), [])
        d["score_json"] = _json_loads(d.get("score_json"), {})
        d["created_human"] = self._fmt_ts(d.get("created_at"))
        return d

    def _list_topics_for_run(self, conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM daily_sticker_topics
             WHERE run_id = ?
               AND dismissed_at IS NULL
             ORDER BY rank
            """,
            (run_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            topic = self._topic_row_to_dict(row)
            topic["images"] = self._images_for_topic(conn, int(topic["id"]))
            out.append(topic)
        return out

    def _images_for_topic(self, conn: sqlite3.Connection, topic_id: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM daily_sticker_topic_images
             WHERE topic_id = ?
             ORDER BY id
            """,
            (topic_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def _fmt_ts(self, ts: int | None) -> str:
        if not ts:
            return ""
        return datetime.fromtimestamp(int(ts), tz=_CN_TZ).strftime("%m-%d %H:%M:%S")


_svc: Optional[DailyStickerTopicService] = None


def get_daily_sticker_topic_service() -> DailyStickerTopicService:
    global _svc
    if _svc is None:
        _svc = DailyStickerTopicService()
    return _svc
