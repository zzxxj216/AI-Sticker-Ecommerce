from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.logger import get_logger
from src.models.ops import GenerationJob, GenerationOutput, TrendBriefRecord, TrendItem

logger = get_logger("service.ops.db")


def _utcnow() -> str:
    return datetime.utcnow().isoformat()


class OpsDatabase:
    def __init__(self, db_path: str | Path = "data/ops_workbench.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_tables()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_tables(self) -> None:
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS trend_items (
                id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_item_id TEXT DEFAULT '',
                title TEXT NOT NULL,
                summary TEXT DEFAULT '',
                trend_name TEXT DEFAULT '',
                trend_type TEXT DEFAULT '',
                score REAL DEFAULT 0,
                heat_score REAL DEFAULT 0,
                fit_level TEXT DEFAULT '',
                pack_archetype TEXT DEFAULT '',
                review_status TEXT DEFAULT 'pending',
                queue_status TEXT DEFAULT 'idle',
                decision TEXT DEFAULT '',
                platform_json TEXT DEFAULT '[]',
                risk_flags_json TEXT DEFAULT '[]',
                visual_symbols_json TEXT DEFAULT '[]',
                emotional_core_json TEXT DEFAULT '[]',
                raw_payload_json TEXT DEFAULT '{}',
                source_url TEXT DEFAULT '',
                reviewed_by TEXT DEFAULT '',
                reviewed_at TEXT,
                batch_date TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS raw_news_items (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                source TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                batch_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trend_briefs (
                trend_id TEXT PRIMARY KEY REFERENCES trend_items(id),
                brief_status TEXT DEFAULT 'pending',
                brief_json TEXT NOT NULL,
                source_ref TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS generation_jobs (
                id TEXT PRIMARY KEY,
                trend_id TEXT NOT NULL REFERENCES trend_items(id),
                trend_name TEXT DEFAULT '',
                status TEXT DEFAULT 'queued',
                output_dir TEXT DEFAULT '',
                image_count INTEGER DEFAULT 0,
                error_message TEXT DEFAULT '',
                created_by TEXT DEFAULT 'system',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS generation_outputs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES generation_jobs(id),
                output_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                preview_path TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            -- Single Database Consolidation: System Task Logs --
            CREATE TABLE IF NOT EXISTS sys_task_jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                result_summary TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS sys_task_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES sys_task_jobs(id),
                step_name TEXT,
                log_level TEXT DEFAULT 'INFO',
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Crawl Job Tracking --
            CREATE TABLE IF NOT EXISTS crawl_jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT DEFAULT 'running',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                result_summary TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS crawl_job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES crawl_jobs(id),
                step_name TEXT,
                log_level TEXT DEFAULT 'INFO',
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- Single Database Consolidation: TikTok Raw Fetches --
            CREATE TABLE IF NOT EXISTS tk_hashtags (
                hashtag_id      TEXT PRIMARY KEY,
                hashtag_name    TEXT NOT NULL,
                video_views     INTEGER DEFAULT 0,
                publish_cnt     INTEGER DEFAULT 0,
                list_data_json  TEXT NOT NULL,
                detail_data_json TEXT,
                creators_raw_json TEXT DEFAULT '[]',
                found_in_filters TEXT DEFAULT '[]',
                crawled_at      TEXT NOT NULL,
                first_seen_at   TEXT NOT NULL,
                last_seen_at    TEXT NOT NULL,
                review_status   TEXT DEFAULT 'pending',
                brief_status    TEXT DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS blog_drafts (
                id TEXT PRIMARY KEY,
                session_id TEXT DEFAULT '',
                topic TEXT NOT NULL,
                seo_keywords TEXT DEFAULT '[]',
                meta_title TEXT DEFAULT '',
                meta_description TEXT DEFAULT '',
                url_slug TEXT DEFAULT '',
                content TEXT DEFAULT '',
                language TEXT DEFAULT 'en',
                status TEXT DEFAULT 'draft',
                publish_status TEXT DEFAULT 'unpublished',
                shopify_article_id TEXT DEFAULT '',
                shopify_url TEXT DEFAULT '',
                published_at TEXT,
                iteration INTEGER DEFAULT 0,
                created_by TEXT DEFAULT 'system',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                agent_type TEXT NOT NULL,
                title TEXT DEFAULT '',
                message_count INTEGER DEFAULT 0,
                created_by TEXT DEFAULT 'system',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_name TEXT DEFAULT '',
                tool_call_id TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trend_items_source_type ON trend_items(source_type);
            CREATE INDEX IF NOT EXISTS idx_trend_items_review_status ON trend_items(review_status);
            CREATE INDEX IF NOT EXISTS idx_trend_items_queue_status ON trend_items(queue_status);
            CREATE INDEX IF NOT EXISTS idx_blog_drafts_publish_status ON blog_drafts(publish_status);
            CREATE INDEX IF NOT EXISTS idx_generation_jobs_trend_id ON generation_jobs(trend_id);
            CREATE INDEX IF NOT EXISTS idx_generation_outputs_job_id ON generation_outputs(job_id);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_agent ON chat_sessions(agent_type);
            """
        )
        c.commit()
        self._migrate_columns(c)

    def _migrate_columns(self, c: sqlite3.Connection) -> None:
        """Add columns that may be missing from older databases."""
        migrations: list[tuple[str, str]] = [
            ("generation_jobs", "trend_name TEXT DEFAULT ''"),
            ("generation_jobs", "output_dir TEXT DEFAULT ''"),
            ("generation_jobs", "image_count INTEGER DEFAULT 0"),
            ("generation_jobs", "error_message TEXT DEFAULT ''"),
            ("generation_jobs", "created_by TEXT DEFAULT 'system'"),
            ("generation_jobs", "started_at TEXT"),
            ("generation_jobs", "finished_at TEXT"),
            ("trend_briefs", "edited_by TEXT DEFAULT ''"),
            ("trend_briefs", "edited_at TEXT"),
        ]
        for table, column_def in migrations:
            col_name = column_def.split()[0]
            try:
                c.execute(f"SELECT {col_name} FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                try:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
                except sqlite3.OperationalError:
                    pass
        c.commit()

    def upsert_trend_item(self, item: TrendItem) -> None:
        now = _utcnow()
        payload = item.model_dump()
        payload["updated_at"] = now
        payload.setdefault("created_at", now)
        # Ensure ID has date component to prevent overwriting old days
        # Assume ID is formatted upstream, but we can capture batch_date from now if missing
        batch_date = now.split('T')[0]
        self.conn.execute(
            """
            INSERT INTO trend_items (
                id, source_type, source_item_id, title, summary, trend_name, trend_type,
                score, heat_score, fit_level, pack_archetype, review_status, queue_status,
                decision, platform_json, risk_flags_json, visual_symbols_json,
                emotional_core_json, raw_payload_json, source_url, batch_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_type=excluded.source_type,
                source_item_id=excluded.source_item_id,
                title=excluded.title,
                summary=excluded.summary,
                trend_name=excluded.trend_name,
                trend_type=excluded.trend_type,
                score=excluded.score,
                heat_score=excluded.heat_score,
                fit_level=excluded.fit_level,
                pack_archetype=excluded.pack_archetype,
                review_status=excluded.review_status,
                queue_status=excluded.queue_status,
                decision=excluded.decision,
                platform_json=excluded.platform_json,
                risk_flags_json=excluded.risk_flags_json,
                visual_symbols_json=excluded.visual_symbols_json,
                emotional_core_json=excluded.emotional_core_json,
                raw_payload_json=excluded.raw_payload_json,
                source_url=excluded.source_url,
                batch_date=excluded.batch_date,
                updated_at=excluded.updated_at
            """,
            (
                payload["id"],
                payload["source_type"],
                payload["source_item_id"],
                payload["title"],
                payload["summary"],
                payload["trend_name"],
                payload["trend_type"],
                payload["score"],
                payload["heat_score"],
                payload["fit_level"],
                payload["pack_archetype"],
                payload["review_status"],
                payload["queue_status"],
                payload["decision"],
                json.dumps(payload["platform"] or []),
                json.dumps(payload["risk_flags"] or []),
                json.dumps(payload["visual_symbols"] or []),
                json.dumps(payload["emotional_core"] or []),
                json.dumps(payload["raw_payload"] or {}),
                payload["source_url"],
                batch_date,
                payload["created_at"],
                payload["updated_at"],
            ),
        )
        self.conn.commit()

    def upsert_brief(self, brief: TrendBriefRecord) -> None:
        now = _utcnow()
        payload = brief.model_dump()
        payload["updated_at"] = now
        payload.setdefault("created_at", now)
        self.conn.execute(
            """
            INSERT INTO trend_briefs (
                trend_id, brief_status, brief_json, source_ref, edited_by, edited_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trend_id) DO UPDATE SET
                brief_status=excluded.brief_status,
                brief_json=excluded.brief_json,
                source_ref=excluded.source_ref,
                edited_by=excluded.edited_by,
                edited_at=excluded.edited_at,
                updated_at=excluded.updated_at
            """,
            (
                payload["trend_id"],
                payload["brief_status"],
                json.dumps(payload["brief_json"], ensure_ascii=False),
                payload["source_ref"],
                payload["edited_by"],
                self._to_iso(payload["edited_at"]),
                self._to_iso(payload["created_at"]),
                self._to_iso(payload["updated_at"]),
            ),
        )
        self.conn.commit()

    def create_job(self, job: GenerationJob) -> None:
        payload = job.model_dump()
        need_fk_bypass = payload["trend_id"].startswith("chat:")
        if need_fk_bypass:
            self.conn.execute("PRAGMA foreign_keys=OFF")
        self.conn.execute(
            """
            INSERT INTO generation_jobs (
                id, trend_id, trend_name, status, output_dir, image_count, error_message,
                created_by, created_at, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["trend_id"],
                payload["trend_name"],
                payload["status"],
                payload["output_dir"],
                payload["image_count"],
                payload["error_message"],
                payload["created_by"],
                self._to_iso(payload["created_at"]),
                self._to_iso(payload["started_at"]),
                self._to_iso(payload["finished_at"]),
                self._to_iso(payload["updated_at"]),
            ),
        )
        self.conn.commit()
        if need_fk_bypass:
            self.conn.execute("PRAGMA foreign_keys=ON")

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = _utcnow()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = [self._to_iso(value) for value in fields.values()]
        values.append(job_id)
        self.conn.execute(
            f"UPDATE generation_jobs SET {assignments} WHERE id = ?",
            values,
        )
        self.conn.commit()

    def replace_outputs(self, job_id: str, outputs: list[GenerationOutput]) -> None:
        self.conn.execute("DELETE FROM generation_outputs WHERE job_id = ?", (job_id,))
        for output in outputs:
            payload = output.model_dump()
            self.conn.execute(
                """
                INSERT INTO generation_outputs (
                    id, job_id, output_type, file_path, preview_path, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["job_id"],
                    payload["output_type"],
                    payload["file_path"],
                    payload["preview_path"],
                    json.dumps(payload["metadata_json"], ensure_ascii=False),
                    self._to_iso(payload["created_at"]),
                ),
            )
        self.conn.commit()

    def list_trends(self, source_type: str | None = None, only_latest: bool = True, status: str = 'pending') -> list[dict[str, Any]]:
        sql = "SELECT * FROM trend_items WHERE 1=1"
        params: list[Any] = []
        
        if status:
            sql += " AND review_status = ?"
            params.append(status)
        
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
            
        if only_latest:
            if source_type:
                sql += " AND batch_date = (SELECT MAX(batch_date) FROM trend_items WHERE source_type = ?)"
                params.append(source_type)
            else:
                sql += " AND batch_date = (SELECT MAX(batch_date) FROM trend_items)"
                
        sql += " ORDER BY score DESC, updated_at DESC"
        c = self.conn.execute(sql, params)
        cols = [col[0] for col in c.description]
        return [self._decode_trend_row(dict(zip(cols, row))) for row in c.fetchall()]

    def list_approved_trends(self) -> list[dict[str, Any]]:
        sql = """
            SELECT t.*,
                   gj.status AS last_job_status,
                   gj.finished_at AS last_job_finished_at,
                   gj.id AS last_job_id
            FROM trend_items t
            LEFT JOIN (
                SELECT trend_id, status, finished_at, id,
                       ROW_NUMBER() OVER (PARTITION BY trend_id ORDER BY created_at DESC) AS rn
                FROM generation_jobs
            ) gj ON gj.trend_id = t.id AND gj.rn = 1
            WHERE t.review_status = 'approved'
            ORDER BY t.updated_at DESC
        """
        c = self.conn.execute(sql)
        cols = [col[0] for col in c.description]
        return [self._decode_trend_row(dict(zip(cols, row))) for row in c.fetchall()]

    def list_archive_trends(self, search_text: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        count_sql = "SELECT COUNT(*) FROM trend_items WHERE 1=1"
        sql = "SELECT * FROM trend_items WHERE 1=1"
        params: list[Any] = []
        
        if search_text:
            like_term = f"%{search_text}%"
            condition = " AND (title LIKE ? OR trend_name LIKE ? OR summary LIKE ?)"
            sql += condition
            count_sql += condition
            params.extend([like_term, like_term, like_term])
            
        total = self.conn.execute(count_sql, params).fetchone()[0]
        
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        c = self.conn.execute(sql, params)
        cols = [col[0] for col in c.description]
        items = [self._decode_trend_row(dict(zip(cols, row))) for row in c.fetchall()]
        return items, total

    def get_trend(self, trend_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM trend_items WHERE id = ?", (trend_id,)).fetchone()
        if not row:
            return None
        return self._decode_trend_row(dict(row))

    def get_brief(self, trend_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM trend_briefs WHERE trend_id = ?", (trend_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["brief_json"] = self._loads(result.get("brief_json"), {})
        return result

    def list_jobs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM generation_jobs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_outputs(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM generation_outputs WHERE job_id = ? ORDER BY created_at",
            (job_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata_json"] = self._loads(item.get("metadata_json"), {})
            result.append(item)
        return result

    def list_completed_jobs_with_images(self) -> list[dict[str, Any]]:
        """Return completed jobs with their image outputs, including source trend info.

        Uses a single query with JOIN to avoid N+1 per-job image lookups.
        """
        rows = self.conn.execute(
            """
            SELECT gj.*,
                   t.trend_name AS source_trend_name,
                   t.title AS source_title,
                   t.source_type AS source_type,
                   t.score AS source_score,
                   go.id AS img_id,
                   go.file_path AS img_file_path,
                   go.preview_path AS img_preview_path,
                   go.metadata_json AS img_metadata_json,
                   go.created_at AS img_created_at
            FROM generation_jobs gj
            LEFT JOIN trend_items t ON t.id = gj.trend_id
            LEFT JOIN generation_outputs go
                   ON go.job_id = gj.id AND go.output_type = 'image'
            WHERE gj.status = 'completed'
            ORDER BY gj.finished_at DESC, go.created_at ASC
            """
        ).fetchall()

        from collections import OrderedDict
        jobs_map: OrderedDict[str, dict] = OrderedDict()
        for row in rows:
            r = dict(row)
            jid = r["id"]
            if jid not in jobs_map:
                job = {k: v for k, v in r.items() if not k.startswith("img_")}
                if job.get("trend_id", "").startswith("chat:"):
                    job["source_type"] = "chat"
                    if not job.get("source_trend_name"):
                        job["source_trend_name"] = job.get("trend_name", "")
                job["images"] = []
                jobs_map[jid] = job
            if r.get("img_id"):
                jobs_map[jid]["images"].append({
                    "id": r["img_id"],
                    "job_id": jid,
                    "output_type": "image",
                    "file_path": r["img_file_path"],
                    "preview_path": r["img_preview_path"],
                    "metadata_json": self._loads(r.get("img_metadata_json"), {}),
                    "created_at": r["img_created_at"],
                })
        return list(jobs_map.values())

    def get_job_image_paths(self, job_id: str) -> list[str]:
        """Return image file paths for a single job."""
        rows = self.conn.execute(
            "SELECT file_path FROM generation_outputs WHERE job_id = ? AND output_type = 'image' ORDER BY created_at",
            (job_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def get_jobs_image_paths(self, job_ids: list[str]) -> dict[str, list[str]]:
        """Return image file paths grouped by job_id, along with trend_name for folder naming."""
        if not job_ids:
            return {}
        placeholders = ",".join("?" for _ in job_ids)
        rows = self.conn.execute(
            f"""
            SELECT gj.id, gj.trend_name, go.file_path
            FROM generation_jobs gj
            JOIN generation_outputs go ON go.job_id = gj.id AND go.output_type = 'image'
            WHERE gj.id IN ({placeholders})
            ORDER BY gj.id, go.created_at
            """,
            job_ids,
        ).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            jid = row[0]
            if jid not in result:
                result[jid] = {"trend_name": row[1], "paths": []}
            result[jid]["paths"].append(row[2])
        return result

    def set_trend_review(self, trend_id: str, review_status: str, decision: str = "", reviewed_by: str = "") -> None:
        now = _utcnow()
        self.conn.execute(
            "UPDATE trend_items SET review_status = ?, decision = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
            (review_status, decision, reviewed_by, now, now, trend_id),
        )
        self.conn.commit()

    def set_trend_queue_status(self, trend_id: str, queue_status: str) -> None:
        if trend_id.startswith("chat:"):
            return
        self.conn.execute(
            "UPDATE trend_items SET queue_status = ?, updated_at = ? WHERE id = ?",
            (queue_status, _utcnow(), trend_id),
        )
        self.conn.commit()

    # --- System Task Job & Logging Helpers ---

    def create_sys_task(self, job_id: str, job_type: str) -> None:
        self.conn.execute(
            "INSERT INTO sys_task_jobs (id, job_type, status, started_at) VALUES (?, ?, ?, ?)",
            (job_id, job_type, "running", _utcnow()),
        )
        self.conn.commit()

    def update_sys_task(self, job_id: str, status: str, result_summary: str = "{}") -> None:
        self.conn.execute(
            "UPDATE sys_task_jobs SET status = ?, completed_at = ?, result_summary = ? WHERE id = ?",
            (status, _utcnow(), result_summary, job_id),
        )
        self.conn.commit()

    def log_task_step(self, job_id: str, message: str, step_name: str = "", log_level: str = "INFO") -> None:
        self.conn.execute(
            "INSERT INTO sys_task_logs (job_id, step_name, log_level, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, step_name, log_level, message, _utcnow()),
        )
        self.conn.commit()

    def list_sys_tasks(self, limit: int = 50) -> list[dict]:
        c = self.conn.execute("SELECT * FROM sys_task_jobs ORDER BY started_at DESC LIMIT ?", (limit,))
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]

    def list_tk_hashtags_paged(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        total = self.conn.execute("SELECT COUNT(*) FROM tk_hashtags").fetchone()[0]
        c = self.conn.execute(
            "SELECT * FROM tk_hashtags ORDER BY video_views DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()], total

    def insert_raw_news(self, items: list[dict], batch_date: str) -> None:
        import hashlib
        c = self.conn
        for item in items:
            title = str(item.get("title") or item.get("keyword") or "")
            url = str(item.get("url", item.get("link", "")))
            if not title and not url: continue
            
            raw_id = hashlib.md5((title + url).encode()).hexdigest()[:12]
            
            c.execute(
                """INSERT OR IGNORE INTO raw_news_items 
                   (id, title, url, source, published_at, batch_date, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"news_{raw_id}",
                    title,
                    url,
                    str(item.get("source", "RSS")),
                    str(item.get("published_at") or item.get("published") or ""),
                    batch_date,
                    _utcnow()
                )
            )
        c.commit()

    def list_raw_news(self, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        total = self.conn.execute("SELECT COUNT(*) FROM raw_news_items").fetchone()[0]
        c = self.conn.execute("SELECT * FROM raw_news_items ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()], total

    def list_sys_task_logs(self, job_id: str) -> list[dict]:
        c = self.conn.execute("SELECT * FROM sys_task_logs WHERE job_id = ? ORDER BY id ASC", (job_id,))
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]


    # --- Job & Tracing Methods ---

    def create_crawl_job(self, job_id: str, job_type: str) -> None:
        self.conn.execute(
            "INSERT INTO crawl_jobs (id, job_type, status, started_at) VALUES (?, ?, ?, ?)",
            (job_id, job_type, "running", _utcnow()),
        )
        self.conn.commit()

    def update_crawl_job(self, job_id: str, status: str, result_summary: str = "{}") -> None:
        self.conn.execute(
            "UPDATE crawl_jobs SET status = ?, completed_at = ?, result_summary = ? WHERE id = ?",
            (status, _utcnow(), result_summary, job_id),
        )
        self.conn.commit()

    def log_crawl_step(self, job_id: str, message: str, step_name: str = "", log_level: str = "INFO") -> None:
        self.conn.execute(
            "INSERT INTO crawl_job_logs (job_id, step_name, log_level, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, step_name, log_level, message, _utcnow()),
        )
        self.conn.commit()
    
    def list_crawl_jobs(self, limit: int = 50) -> list[dict]:
        c = self.conn.execute(
            "SELECT id, job_type, status, started_at, completed_at, result_summary FROM crawl_jobs ORDER BY started_at DESC LIMIT ?",
            (limit,)
        )
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]

    def list_job_logs(self, job_id: str) -> list[dict]:
        c = self.conn.execute(
            "SELECT step_name, log_level, message, created_at FROM crawl_job_logs WHERE job_id = ? ORDER BY id ASC",
            (job_id,)
        )
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]

    @staticmethod
    def _loads(raw: Any, default: Any) -> Any:
        if raw in (None, ""):
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    def _decode_trend_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["platform"] = self._loads(row.get("platform_json"), [])
        row["risk_flags"] = self._loads(row.get("risk_flags_json"), [])
        row["visual_symbols"] = self._loads(row.get("visual_symbols_json"), [])
        row["emotional_core"] = self._loads(row.get("emotional_core_json"), [])
        row["raw_payload"] = self._loads(row.get("raw_payload_json"), {})
        return row

    # --- Blog Drafts ---

    def insert_blog_draft(self, data: dict) -> None:
        now = _utcnow()
        self.conn.execute(
            """
            INSERT INTO blog_drafts (
                id, session_id, topic, seo_keywords, meta_title, meta_description,
                url_slug, content, language, status, publish_status, iteration,
                created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["id"],
                data.get("session_id", ""),
                data["topic"],
                json.dumps(data.get("seo_keywords", []), ensure_ascii=False),
                data.get("meta_title", ""),
                data.get("meta_description", ""),
                data.get("url_slug", ""),
                data.get("content", ""),
                data.get("language", "en"),
                data.get("status", "completed"),
                data.get("publish_status", "unpublished"),
                data.get("iteration", 0),
                data.get("created_by", "system"),
                now,
                now,
            ),
        )
        self.conn.commit()

    def list_blog_drafts(self, publish_status: str | None = None) -> list[dict[str, Any]]:
        if publish_status:
            rows = self.conn.execute(
                "SELECT * FROM blog_drafts WHERE publish_status = ? ORDER BY created_at DESC",
                (publish_status,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM blog_drafts ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["seo_keywords"] = self._loads(d.get("seo_keywords"), [])
            result.append(d)
        return result

    def get_blog_draft(self, blog_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM blog_drafts WHERE id = ?", (blog_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["seo_keywords"] = self._loads(d.get("seo_keywords"), [])
        return d

    def update_blog_publish_status(
        self,
        blog_id: str,
        publish_status: str,
        shopify_article_id: str = "",
        shopify_url: str = "",
    ) -> None:
        now = _utcnow()
        published_at = now if publish_status in ("shopify_draft", "published") else None
        self.conn.execute(
            """
            UPDATE blog_drafts
            SET publish_status = ?, shopify_article_id = ?, shopify_url = ?,
                published_at = COALESCE(?, published_at), updated_at = ?
            WHERE id = ?
            """,
            (publish_status, shopify_article_id, shopify_url, published_at, now, blog_id),
        )
        self.conn.commit()

    def delete_blog_draft(self, blog_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM blog_drafts WHERE id = ?", (blog_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- Chat Sessions / Messages ---

    def ensure_chat_session(
        self, session_id: str, agent_type: str, created_by: str = "system",
    ) -> None:
        now = _utcnow()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO chat_sessions (id, agent_type, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, agent_type, created_by, now, now),
        )
        self.conn.commit()

    def insert_chat_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: str = "",
        tool_call_id: str = "",
    ) -> None:
        now = _utcnow()
        self.conn.execute(
            """
            INSERT INTO chat_messages (session_id, role, content, tool_name, tool_call_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, content, tool_name, tool_call_id, now),
        )
        title_text = content[:60] if role == "user" else ""
        if title_text:
            self.conn.execute(
                """
                UPDATE chat_sessions
                SET title = CASE WHEN title = '' THEN ? ELSE title END,
                    message_count = message_count + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (title_text, now, session_id),
            )
        else:
            self.conn.execute(
                "UPDATE chat_sessions SET message_count = message_count + 1, updated_at = ? WHERE id = ?",
                (now, session_id),
            )
        self.conn.commit()

    def list_chat_sessions(
        self, agent_type: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        if agent_type:
            rows = self.conn.execute(
                "SELECT * FROM chat_sessions WHERE agent_type = ? ORDER BY updated_at DESC LIMIT ?",
                (agent_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_chat_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_chat_session(self, session_id: str) -> bool:
        self.conn.execute("PRAGMA foreign_keys=ON")
        cur = self.conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def delete_chat_sessions_batch(self, session_ids: list[str]) -> int:
        if not session_ids:
            return 0
        self.conn.execute("PRAGMA foreign_keys=ON")
        placeholders = ",".join("?" for _ in session_ids)
        cur = self.conn.execute(
            f"DELETE FROM chat_sessions WHERE id IN ({placeholders})", session_ids,
        )
        self.conn.commit()
        return cur.rowcount

    @staticmethod
    def _to_iso(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return value
