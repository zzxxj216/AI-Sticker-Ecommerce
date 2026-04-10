from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from src.core.logger import get_logger
from src.models.ops import GenerationJob, GenerationOutput, TrendBriefRecord, TrendItem

logger = get_logger("service.ops.db")


_CN_TZ = timezone(timedelta(hours=8))

def _now() -> str:
    return datetime.now(_CN_TZ).strftime("%Y-%m-%dT%H:%M:%S")


class OpsDatabase:
    _write_lock = threading.Lock()

    def __init__(self, db_path: str | Path = "data/ops_workbench.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._ensure_tables()

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

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

            CREATE TABLE IF NOT EXISTS trend_brief_gen_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trend_id TEXT NOT NULL,
                log_level TEXT DEFAULT 'INFO',
                message TEXT NOT NULL,
                source TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_trend_brief_gen_logs_tid ON trend_brief_gen_logs(trend_id);

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

            -- Video Type + Combo Script Generation System --
            CREATE TABLE IF NOT EXISTS video_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                goal TEXT DEFAULT '',
                description TEXT DEFAULT '',
                required_inputs_json TEXT DEFAULT '[]',
                allowed_positions_json TEXT DEFAULT '["any"]',
                text_style_rules_json TEXT DEFAULT '[]',
                can_pair_with_json TEXT DEFAULT '[]',
                output_elements_json TEXT DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_type_combos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                combo_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                selected_types_json TEXT DEFAULT '[]',
                primary_type TEXT DEFAULT '',
                secondary_types_json TEXT DEFAULT '[]',
                support_types_json TEXT DEFAULT '[]',
                duration_range_json TEXT DEFAULT '{"min":7,"max":12}',
                shot_count_range_json TEXT DEFAULT '{"min":3,"max":5}',
                constraints_json TEXT DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_script_plans_v2 (
                id TEXT PRIMARY KEY,
                design_id TEXT DEFAULT '',
                pack_id TEXT DEFAULT '',
                job_id TEXT DEFAULT '',
                combo_id TEXT NOT NULL,
                selected_types_json TEXT DEFAULT '[]',
                input_json TEXT DEFAULT '{}',
                plan_json TEXT DEFAULT '{}',
                status TEXT DEFAULT 'pending',
                created_by TEXT DEFAULT 'system',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_vsp2_combo ON video_script_plans_v2(combo_id);
            CREATE INDEX IF NOT EXISTS idx_vsp2_job ON video_script_plans_v2(job_id);

            CREATE TABLE IF NOT EXISTS video_scripts (
                id TEXT PRIMARY KEY,
                design_id TEXT DEFAULT '',
                pack_id TEXT DEFAULT '',
                job_id TEXT DEFAULT '',
                combo_id TEXT NOT NULL,
                plan_id TEXT DEFAULT '',
                hook_text TEXT DEFAULT '',
                cta_text TEXT DEFAULT '',
                caption_text TEXT DEFAULT '',
                title_options_json TEXT DEFAULT '[]',
                script_json TEXT DEFAULT '{}',
                status TEXT DEFAULT 'pending',
                created_by TEXT DEFAULT 'system',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_vs_combo ON video_scripts(combo_id);
            CREATE INDEX IF NOT EXISTS idx_vs_plan ON video_scripts(plan_id);
            CREATE INDEX IF NOT EXISTS idx_vs_job ON video_scripts(job_id);

            -- Planning Events Calendar --
            CREATE TABLE IF NOT EXISTS planning_events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT DEFAULT '',
                region TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT,
                short_description TEXT DEFAULT '',
                source TEXT DEFAULT '',
                raw_json TEXT DEFAULT '{}',
                fetch_batch TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pe_region_date ON planning_events(region, start_date);
            CREATE INDEX IF NOT EXISTS idx_pe_batch ON planning_events(fetch_batch);

            -- Planning Directions (sticker pack design directions) --
            CREATE TABLE IF NOT EXISTS planning_directions (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                direction_index INTEGER DEFAULT 0,
                name_en TEXT NOT NULL,
                name_zh TEXT DEFAULT '',
                keywords TEXT DEFAULT '',
                design_elements TEXT DEFAULT '',
                text_slogans TEXT DEFAULT '',
                decorative_elements TEXT DEFAULT '',
                preview_path TEXT DEFAULT '',
                preview_status TEXT DEFAULT 'pending',
                gen_status TEXT DEFAULT 'pending',
                job_id TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (event_id) REFERENCES planning_events(id)
            );
            CREATE INDEX IF NOT EXISTS idx_pd_event ON planning_directions(event_id);
            """
        )
        c.commit()
        self._seed_video_types(c)
        self._seed_video_type_combos(c)
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
            ("generation_jobs", "family_id TEXT"),
            ("generation_jobs", "subtheme_id INTEGER"),
            ("generation_jobs", "variant_label TEXT"),
            ("trend_items", "family_status TEXT DEFAULT 'pending'"),
            ("trend_items", "allocation_status TEXT DEFAULT 'pending'"),
            ("planning_directions", "sticker_count INTEGER DEFAULT 10"),
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
        now = _now()
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
                review_status=CASE
                    WHEN trend_items.reviewed_by IS NOT NULL AND trend_items.reviewed_by != ''
                    THEN trend_items.review_status ELSE excluded.review_status END,
                queue_status=CASE
                    WHEN trend_items.reviewed_by IS NOT NULL AND trend_items.reviewed_by != ''
                    THEN trend_items.queue_status ELSE excluded.queue_status END,
                decision=CASE
                    WHEN trend_items.reviewed_by IS NOT NULL AND trend_items.reviewed_by != ''
                    THEN trend_items.decision ELSE excluded.decision END,
                platform_json=excluded.platform_json,
                risk_flags_json=excluded.risk_flags_json,
                visual_symbols_json=excluded.visual_symbols_json,
                emotional_core_json=excluded.emotional_core_json,
                raw_payload_json=excluded.raw_payload_json,
                source_url=excluded.source_url,
                batch_date=CASE
                    WHEN trend_items.reviewed_by IS NOT NULL AND trim(trend_items.reviewed_by) != ''
                    THEN trend_items.batch_date ELSE excluded.batch_date END,
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
        now = _now()
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
        try:
            self.conn.execute(
                """
                INSERT INTO generation_jobs (
                    id, trend_id, trend_name, status, output_dir, image_count, error_message,
                    created_by, created_at, started_at, finished_at, updated_at,
                    family_id, subtheme_id, variant_label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    payload.get("family_id"),
                    payload.get("subtheme_id"),
                    payload.get("variant_label"),
                ),
            )
            self.conn.commit()
        finally:
            if need_fk_bypass:
                self.conn.execute("PRAGMA foreign_keys=ON")

    _JOB_UPDATABLE_COLS = frozenset({
        "status", "error_message", "output_dir", "image_count",
        "started_at", "finished_at", "updated_at",
    })

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        bad_keys = set(fields) - self._JOB_UPDATABLE_COLS
        if bad_keys:
            raise ValueError(f"Disallowed columns for update_job: {bad_keys}")
        fields["updated_at"] = _now()
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

    def list_trends(
        self,
        source_type: str | None = None,
        only_latest: bool = True,
        status: str | None = 'pending',
        batch_date: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM trend_items WHERE 1=1"
        params: list[Any] = []

        if status:
            sql += " AND review_status = ?"
            params.append(status)

        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)

        if batch_date:
            sql += " AND batch_date = ?"
            params.append(batch_date)
        elif only_latest:
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

    _ARCHIVE_SORT_COLS = {'created_at', 'updated_at', 'score', 'heat_score', 'trend_name', 'title', 'source_type', 'review_status', 'batch_date'}

    def list_archive_trends(self, search_text: str | None = None, limit: int = 50, offset: int = 0,
                            sort_by: str = 'created_at', sort_dir: str = 'desc',
                            date_from: str = '', date_to: str = '') -> tuple[list[dict[str, Any]], int]:
        count_sql = "SELECT COUNT(*) FROM trend_items WHERE 1=1"
        sql = "SELECT * FROM trend_items WHERE 1=1"
        params: list[Any] = []
        
        if search_text:
            like_term = f"%{search_text}%"
            condition = " AND (title LIKE ? OR trend_name LIKE ? OR summary LIKE ?)"
            sql += condition
            count_sql += condition
            params.extend([like_term, like_term, like_term])

        if date_from:
            cond = " AND created_at >= ?"
            sql += cond
            count_sql += cond
            params.append(date_from)
        if date_to:
            cond = " AND created_at <= ?"
            sql += cond
            count_sql += cond
            params.append(date_to + "T23:59:59")
            
        total = self.conn.execute(count_sql, params).fetchone()[0]

        col = sort_by if sort_by in self._ARCHIVE_SORT_COLS else 'created_at'
        direction = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'
        sql += f" ORDER BY {col} {direction} LIMIT ? OFFSET ?"
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

    def list_jobs_by_family(self, family_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM generation_jobs WHERE family_id = ? ORDER BY created_at DESC",
            (family_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def skip_stale_pending_trends(self, source_type: str, current_batch_date: str) -> int:
        """将早于本次爬取批次、且从未人工审核过的 pending 趋势标记为跳过。"""
        now = _now()
        cur = self.conn.execute(
            """
            UPDATE trend_items
            SET review_status = 'skipped',
                decision = 'skip',
                queue_status = 'idle',
                reviewed_by = ?,
                reviewed_at = ?,
                updated_at = ?
            WHERE source_type = ?
              AND review_status = 'pending'
              AND (reviewed_by IS NULL OR trim(reviewed_by) = '')
              AND (
                  batch_date IS NULL OR trim(batch_date) = ''
                  OR batch_date < ?
              )
            """,
            (
                "system:auto-stale-batch",
                now,
                now,
                source_type,
                current_batch_date,
            ),
        )
        self.conn.commit()
        return cur.rowcount or 0

    def set_trend_review(self, trend_id: str, review_status: str, decision: str = "", reviewed_by: str = "") -> None:
        now = _now()
        self.conn.execute(
            "UPDATE trend_items SET review_status = ?, decision = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
            (review_status, decision, reviewed_by, now, now, trend_id),
        )
        self.conn.commit()

    def revert_approved_awaiting_brief_to_pending(self, reverted_by: str = "system") -> int:
        """已采纳但尚无可用 Brief、Brief 为空或仍在生成中的趋势，改回待审核（pending）。"""
        now = _now()
        rows = self.conn.execute(
            "SELECT id FROM trend_items WHERE review_status = 'approved'"
        ).fetchall()
        to_revert: list[str] = []
        for row in rows:
            tid = row["id"]
            br = self.get_brief(tid)
            if br is None:
                to_revert.append(tid)
                continue
            if (br.get("brief_status") or "").lower() == "generating":
                to_revert.append(tid)
                continue
            bj = br.get("brief_json")
            if not bj:
                to_revert.append(tid)
        if not to_revert:
            return 0
        ph = ",".join("?" * len(to_revert))
        self.conn.execute(
            f"""
            UPDATE trend_briefs
            SET brief_status = 'failed', source_ref = 'reverted_to_pending', updated_at = ?
            WHERE trend_id IN ({ph}) AND brief_status = 'generating'
            """,
            [now, *to_revert],
        )
        self.conn.execute(
            f"""
            UPDATE trend_items
            SET review_status = 'pending', decision = '', reviewed_by = ?, reviewed_at = ?, updated_at = ?,
                queue_status = 'idle'
            WHERE id IN ({ph})
            """,
            [reverted_by, now, now, *to_revert],
        )
        self.conn.commit()
        return len(to_revert)

    def set_trend_queue_status(self, trend_id: str, queue_status: str) -> None:
        if trend_id.startswith("chat:"):
            return
        self.conn.execute(
            "UPDATE trend_items SET queue_status = ?, updated_at = ? WHERE id = ?",
            (queue_status, _now(), trend_id),
        )
        self.conn.commit()

    # --- System Task Job & Logging Helpers ---

    def create_sys_task(self, job_id: str, job_type: str) -> None:
        self.conn.execute(
            "INSERT INTO sys_task_jobs (id, job_type, status, started_at) VALUES (?, ?, ?, ?)",
            (job_id, job_type, "running", _now()),
        )
        self.conn.commit()

    def update_sys_task(self, job_id: str, status: str, result_summary: str = "{}") -> None:
        self.conn.execute(
            "UPDATE sys_task_jobs SET status = ?, completed_at = ?, result_summary = ? WHERE id = ?",
            (status, _now(), result_summary, job_id),
        )
        self.conn.commit()

    def log_task_step(self, job_id: str, message: str, step_name: str = "", log_level: str = "INFO") -> None:
        self.conn.execute(
            "INSERT INTO sys_task_logs (job_id, step_name, log_level, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, step_name, log_level, message, _now()),
        )
        self.conn.commit()

    def list_sys_tasks(self, limit: int = 50) -> list[dict]:
        c = self.conn.execute("SELECT * FROM sys_task_jobs ORDER BY started_at DESC LIMIT ?", (limit,))
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]

    _TK_SORT_COLS = {'video_views', 'publish_cnt', 'hashtag_name', 'crawled_at', 'first_seen_at', 'last_seen_at', 'review_status', 'brief_status'}

    def list_tk_hashtags_paged(self, limit: int = 50, offset: int = 0,
                               sort_by: str = 'video_views', sort_dir: str = 'desc',
                               date_from: str = '', date_to: str = '') -> tuple[list[dict], int]:
        where = " WHERE 1=1"
        params: list[Any] = []
        if date_from:
            where += " AND crawled_at >= ?"
            params.append(date_from)
        if date_to:
            where += " AND crawled_at <= ?"
            params.append(date_to + "T23:59:59")

        total = self.conn.execute("SELECT COUNT(*) FROM tk_hashtags" + where, params).fetchone()[0]

        col = sort_by if sort_by in self._TK_SORT_COLS else 'video_views'
        direction = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'
        sql = f"SELECT * FROM tk_hashtags{where} ORDER BY {col} {direction} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        c = self.conn.execute(sql, params)
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
                    _now()
                )
            )
        c.commit()

    _RAW_NEWS_SORT_COLS = {'created_at', 'title', 'source', 'published_at', 'batch_date'}

    def list_raw_news(self, limit: int = 50, offset: int = 0,
                      sort_by: str = 'created_at', sort_dir: str = 'desc',
                      date_from: str = '', date_to: str = '') -> tuple[list[dict], int]:
        where = " WHERE 1=1"
        params: list[Any] = []
        if date_from:
            where += " AND created_at >= ?"
            params.append(date_from)
        if date_to:
            where += " AND created_at <= ?"
            params.append(date_to + "T23:59:59")

        total = self.conn.execute("SELECT COUNT(*) FROM raw_news_items" + where, params).fetchone()[0]

        col = sort_by if sort_by in self._RAW_NEWS_SORT_COLS else 'created_at'
        direction = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'
        sql = f"SELECT * FROM raw_news_items{where} ORDER BY {col} {direction} LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        c = self.conn.execute(sql, params)
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()], total

    def list_sys_task_logs(self, job_id: str) -> list[dict]:
        c = self.conn.execute("SELECT * FROM sys_task_logs WHERE job_id = ? ORDER BY id ASC", (job_id,))
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]

    def log_brief_generation(
        self,
        trend_id: str,
        message: str,
        log_level: str = "INFO",
        source: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO trend_brief_gen_logs (trend_id, log_level, message, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trend_id, log_level, message, source, _now()),
        )
        self.conn.commit()

    def list_brief_gen_logs(self, trend_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        if trend_id:
            c = self.conn.execute(
                "SELECT * FROM trend_brief_gen_logs WHERE trend_id = ? ORDER BY id DESC LIMIT ?",
                (trend_id, limit),
            )
        else:
            c = self.conn.execute(
                "SELECT * FROM trend_brief_gen_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        cols = [col[0] for col in c.description]
        rows = [dict(zip(cols, row)) for row in c.fetchall()]
        rows.reverse()
        return rows


    # --- Job & Tracing Methods ---

    def create_crawl_job(self, job_id: str, job_type: str) -> None:
        self.conn.execute(
            "INSERT INTO crawl_jobs (id, job_type, status, started_at) VALUES (?, ?, ?, ?)",
            (job_id, job_type, "running", _now()),
        )
        self.conn.commit()

    def update_crawl_job(self, job_id: str, status: str, result_summary: str = "{}") -> None:
        self.conn.execute(
            "UPDATE crawl_jobs SET status = ?, completed_at = ?, result_summary = ? WHERE id = ?",
            (status, _now(), result_summary, job_id),
        )
        self.conn.commit()

    def log_crawl_step(self, job_id: str, message: str, step_name: str = "", log_level: str = "INFO") -> None:
        self.conn.execute(
            "INSERT INTO crawl_job_logs (job_id, step_name, log_level, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, step_name, log_level, message, _now()),
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
        now = _now()
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
        now = _now()
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
        now = _now()
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
        now = _now()
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

    # --- Video Type + Combo seed & CRUD ---

    def _seed_video_types(self, c: sqlite3.Connection) -> None:
        count = c.execute("SELECT COUNT(*) FROM video_types").fetchone()[0]
        if count > 0:
            return

        now = _now()
        types = [
            {
                "type_id": "resonance",
                "name": "热点共鸣 / 情绪代入",
                "goal": "让用户觉得「这说的就是我」，适合热点卡贴的表达起点",
                "description": "Relatable opening, emotional captions, meme-style expression",
                "required_inputs": ["trend_topic", "emotional_hooks", "audience_persona"],
                "allowed_positions": ["opening", "any"],
                "text_style_rules": ["max 8 words per line", "use internet slang", "relatable first-person"],
                "can_pair_with": ["visual_showcase", "commerce_scene", "collection_flex", "comment_driver", "soft_sell"],
                "output_elements": ["hook_text", "emotional_caption", "relatable_opener"],
            },
            {
                "type_id": "visual_showcase",
                "name": "视觉展示",
                "goal": "让贴纸本身更抓眼，强化首屏停留",
                "description": "Hero sticker reveal, quick pack cuts, strong visual short captions",
                "required_inputs": ["hero_sticker", "collection_sheet", "sticker_descriptions"],
                "allowed_positions": ["opening", "middle", "any"],
                "text_style_rules": ["max 6 words", "punchy adjectives", "no long sentences"],
                "can_pair_with": ["resonance", "collection_flex", "soft_sell", "comment_driver"],
                "output_elements": ["hero_reveal", "pack_cut_sequence", "visual_caption"],
            },
            {
                "type_id": "commerce_scene",
                "name": "商品场景",
                "goal": "让用户把贴纸想象成能买、能贴、能用的商品",
                "description": "Use-case placement (laptop, bottle, journal), material feel, product framing",
                "required_inputs": ["use_cases", "materials", "audience_persona"],
                "allowed_positions": ["middle", "any"],
                "text_style_rules": ["show don't tell", "lifestyle language", "no hard-sell"],
                "can_pair_with": ["resonance", "collection_flex", "soft_sell", "comment_driver"],
                "output_elements": ["use_case_shot", "material_callout", "lifestyle_caption"],
            },
            {
                "type_id": "collection_flex",
                "name": "整包展示",
                "goal": "展示 pack 丰富度，避免用户只看到单张",
                "description": "Full pack spread, pick-your-favorite, whole-set energy",
                "required_inputs": ["collection_sheet", "sticker_descriptions"],
                "allowed_positions": ["middle", "any"],
                "text_style_rules": ["use quantity words", "fan-out energy", "which one are you"],
                "can_pair_with": ["resonance", "visual_showcase", "soft_sell", "comment_driver"],
                "output_elements": ["collection_spread", "pick_favorite_prompt", "pack_count_callout"],
            },
            {
                "type_id": "comment_driver",
                "name": "评论驱动",
                "goal": "增加互动，帮后续观察偏好",
                "description": "Choice-style ending, comment prompt, light controversy question",
                "required_inputs": ["audience_persona", "trend_topic", "brand_tone"],
                "allowed_positions": ["closing"],
                "text_style_rules": ["question format", "binary choice", "casual tone"],
                "can_pair_with": ["resonance", "visual_showcase", "commerce_scene", "collection_flex"],
                "output_elements": ["question_cta", "binary_choice", "comment_prompt"],
            },
            {
                "type_id": "soft_sell",
                "name": "轻转化",
                "goal": "带一点购买意图，不要太硬广",
                "description": "Soft buy intent, comment-to-buy, want-the-set energy",
                "required_inputs": ["one_line_product_angle", "materials", "use_cases", "brand_tone"],
                "allowed_positions": ["closing"],
                "text_style_rules": ["conversational CTA", "no price mention", "desire language"],
                "can_pair_with": ["resonance", "visual_showcase", "commerce_scene", "collection_flex"],
                "output_elements": ["soft_cta", "desire_prompt", "buy_intent_text"],
            },
        ]

        for t in types:
            c.execute(
                """INSERT OR IGNORE INTO video_types
                   (type_id, name, goal, description,
                    required_inputs_json, allowed_positions_json,
                    text_style_rules_json, can_pair_with_json,
                    output_elements_json, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    t["type_id"], t["name"], t["goal"], t["description"],
                    json.dumps(t["required_inputs"]),
                    json.dumps(t["allowed_positions"]),
                    json.dumps(t["text_style_rules"]),
                    json.dumps(t["can_pair_with"]),
                    json.dumps(t["output_elements"]),
                    now, now,
                ),
            )
        c.commit()
        logger.info("Seeded %d video types", len(types))

    def _seed_video_type_combos(self, c: sqlite3.Connection) -> None:
        count = c.execute("SELECT COUNT(*) FROM video_type_combos").fetchone()[0]
        if count > 0:
            return

        now = _now()
        combos = [
            {
                "combo_id": "combo_resonance_visual",
                "name": "热点共鸣 + 视觉展示",
                "selected_types": ["resonance", "visual_showcase"],
                "primary_type": "resonance",
                "secondary_types": ["visual_showcase"],
                "support_types": [],
                "duration_range": {"min": 7, "max": 10},
                "shot_count_range": {"min": 3, "max": 4},
                "constraints": ["must_have_hero_sticker", "hook_must_be_relatable"],
            },
            {
                "combo_id": "combo_resonance_commerce",
                "name": "热点共鸣 + 商品场景",
                "selected_types": ["resonance", "commerce_scene"],
                "primary_type": "resonance",
                "secondary_types": ["commerce_scene"],
                "support_types": [],
                "duration_range": {"min": 7, "max": 10},
                "shot_count_range": {"min": 3, "max": 4},
                "constraints": ["must_mention_use_case", "hook_must_be_relatable"],
            },
            {
                "combo_id": "combo_resonance_visual_softsell",
                "name": "热点共鸣 + 视觉展示 + 轻转化",
                "selected_types": ["resonance", "visual_showcase", "soft_sell"],
                "primary_type": "resonance",
                "secondary_types": ["visual_showcase"],
                "support_types": ["soft_sell"],
                "duration_range": {"min": 8, "max": 12},
                "shot_count_range": {"min": 3, "max": 5},
                "constraints": ["must_have_hero_sticker", "last_shot_must_have_cta"],
            },
            {
                "combo_id": "combo_resonance_commerce_comment",
                "name": "热点共鸣 + 商品场景 + 评论驱动",
                "selected_types": ["resonance", "commerce_scene", "comment_driver"],
                "primary_type": "resonance",
                "secondary_types": ["commerce_scene"],
                "support_types": ["comment_driver"],
                "duration_range": {"min": 8, "max": 12},
                "shot_count_range": {"min": 3, "max": 5},
                "constraints": ["must_mention_use_case", "last_shot_must_be_question"],
            },
            {
                "combo_id": "combo_visual_collection",
                "name": "视觉展示 + 整包展示",
                "selected_types": ["visual_showcase", "collection_flex"],
                "primary_type": "visual_showcase",
                "secondary_types": ["collection_flex"],
                "support_types": [],
                "duration_range": {"min": 7, "max": 10},
                "shot_count_range": {"min": 3, "max": 4},
                "constraints": ["must_have_hero_sticker", "must_show_collection_sheet"],
            },
            {
                "combo_id": "combo_visual_collection_softsell",
                "name": "视觉展示 + 整包展示 + 轻转化",
                "selected_types": ["visual_showcase", "collection_flex", "soft_sell"],
                "primary_type": "visual_showcase",
                "secondary_types": ["collection_flex"],
                "support_types": ["soft_sell"],
                "duration_range": {"min": 8, "max": 12},
                "shot_count_range": {"min": 3, "max": 5},
                "constraints": ["must_have_hero_sticker", "must_show_collection_sheet", "last_shot_must_have_cta"],
            },
            {
                "combo_id": "combo_resonance_collection_comment",
                "name": "热点共鸣 + 整包展示 + 评论驱动",
                "selected_types": ["resonance", "collection_flex", "comment_driver"],
                "primary_type": "resonance",
                "secondary_types": ["collection_flex"],
                "support_types": ["comment_driver"],
                "duration_range": {"min": 8, "max": 12},
                "shot_count_range": {"min": 3, "max": 5},
                "constraints": ["must_show_collection_sheet", "last_shot_must_be_question"],
            },
            {
                "combo_id": "combo_commerce_collection_softsell",
                "name": "商品场景 + 整包展示 + 轻转化",
                "selected_types": ["commerce_scene", "collection_flex", "soft_sell"],
                "primary_type": "commerce_scene",
                "secondary_types": ["collection_flex"],
                "support_types": ["soft_sell"],
                "duration_range": {"min": 8, "max": 12},
                "shot_count_range": {"min": 3, "max": 5},
                "constraints": ["must_mention_use_case", "must_show_collection_sheet", "last_shot_must_have_cta"],
            },
        ]

        for cb in combos:
            c.execute(
                """INSERT OR IGNORE INTO video_type_combos
                   (combo_id, name, selected_types_json, primary_type,
                    secondary_types_json, support_types_json,
                    duration_range_json, shot_count_range_json,
                    constraints_json, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    cb["combo_id"], cb["name"],
                    json.dumps(cb["selected_types"]),
                    cb["primary_type"],
                    json.dumps(cb["secondary_types"]),
                    json.dumps(cb["support_types"]),
                    json.dumps(cb["duration_range"]),
                    json.dumps(cb["shot_count_range"]),
                    json.dumps(cb["constraints"]),
                    now, now,
                ),
            )
        c.commit()
        logger.info("Seeded %d video type combos", len(combos))

    # --- V2: Video Types CRUD ---

    def list_video_types(self, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM video_types"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY type_id ASC"
        rows = self.conn.execute(sql).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for col in ("required_inputs", "allowed_positions", "text_style_rules",
                        "can_pair_with", "output_elements"):
                d[col] = self._loads(d.pop(f"{col}_json", "[]"), [])
            result.append(d)
        return result

    def get_video_type(self, type_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM video_types WHERE type_id = ?", (type_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for col in ("required_inputs", "allowed_positions", "text_style_rules",
                    "can_pair_with", "output_elements"):
            d[col] = self._loads(d.pop(f"{col}_json", "[]"), [])
        return d

    def upsert_video_type(self, data: dict) -> None:
        now = _now()
        self.conn.execute(
            """INSERT INTO video_types
               (type_id, name, goal, description,
                required_inputs_json, allowed_positions_json,
                text_style_rules_json, can_pair_with_json,
                output_elements_json, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(type_id) DO UPDATE SET
                name=excluded.name, goal=excluded.goal, description=excluded.description,
                required_inputs_json=excluded.required_inputs_json,
                allowed_positions_json=excluded.allowed_positions_json,
                text_style_rules_json=excluded.text_style_rules_json,
                can_pair_with_json=excluded.can_pair_with_json,
                output_elements_json=excluded.output_elements_json,
                is_active=excluded.is_active, updated_at=excluded.updated_at""",
            (
                data["type_id"], data["name"], data.get("goal", ""), data.get("description", ""),
                json.dumps(data.get("required_inputs", []), ensure_ascii=False),
                json.dumps(data.get("allowed_positions", ["any"]), ensure_ascii=False),
                json.dumps(data.get("text_style_rules", []), ensure_ascii=False),
                json.dumps(data.get("can_pair_with", []), ensure_ascii=False),
                json.dumps(data.get("output_elements", []), ensure_ascii=False),
                1 if data.get("is_active", True) else 0,
                now, now,
            ),
        )
        self.conn.commit()

    def toggle_video_type(self, type_id: str, is_active: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE video_types SET is_active = ?, updated_at = ? WHERE type_id = ?",
            (1 if is_active else 0, _now(), type_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # --- V2: Video Type Combos CRUD ---

    def list_video_type_combos(self, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM video_type_combos"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY combo_id ASC"
        rows = self.conn.execute(sql).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            for col in ("selected_types", "secondary_types", "support_types", "constraints"):
                d[col] = self._loads(d.pop(f"{col}_json", "[]"), [])
            for col in ("duration_range", "shot_count_range"):
                d[col] = self._loads(d.pop(f"{col}_json", "{}"), {})
            result.append(d)
        return result

    def get_video_type_combo(self, combo_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM video_type_combos WHERE combo_id = ?", (combo_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for col in ("selected_types", "secondary_types", "support_types", "constraints"):
            d[col] = self._loads(d.pop(f"{col}_json", "[]"), [])
        for col in ("duration_range", "shot_count_range"):
            d[col] = self._loads(d.pop(f"{col}_json", "{}"), {})
        return d

    def upsert_video_type_combo(self, data: dict) -> None:
        now = _now()
        self.conn.execute(
            """INSERT INTO video_type_combos
               (combo_id, name, selected_types_json, primary_type,
                secondary_types_json, support_types_json,
                duration_range_json, shot_count_range_json,
                constraints_json, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(combo_id) DO UPDATE SET
                name=excluded.name, selected_types_json=excluded.selected_types_json,
                primary_type=excluded.primary_type,
                secondary_types_json=excluded.secondary_types_json,
                support_types_json=excluded.support_types_json,
                duration_range_json=excluded.duration_range_json,
                shot_count_range_json=excluded.shot_count_range_json,
                constraints_json=excluded.constraints_json,
                is_active=excluded.is_active, updated_at=excluded.updated_at""",
            (
                data["combo_id"], data["name"],
                json.dumps(data.get("selected_types", []), ensure_ascii=False),
                data.get("primary_type", ""),
                json.dumps(data.get("secondary_types", []), ensure_ascii=False),
                json.dumps(data.get("support_types", []), ensure_ascii=False),
                json.dumps(data.get("duration_range", {"min": 7, "max": 12}), ensure_ascii=False),
                json.dumps(data.get("shot_count_range", {"min": 3, "max": 5}), ensure_ascii=False),
                json.dumps(data.get("constraints", []), ensure_ascii=False),
                1 if data.get("is_active", True) else 0,
                now, now,
            ),
        )
        self.conn.commit()

    def toggle_video_type_combo(self, combo_id: str, is_active: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE video_type_combos SET is_active = ?, updated_at = ? WHERE combo_id = ?",
            (1 if is_active else 0, _now(), combo_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_video_type_combo(self, combo_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM video_type_combos WHERE combo_id = ?", (combo_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- V2: Script Plan (two-stage) CRUD ---

    def insert_video_script_plan_v2(self, data: dict) -> None:
        now = _now()
        self.conn.execute(
            """INSERT INTO video_script_plans_v2
               (id, design_id, pack_id, job_id, combo_id, selected_types_json,
                input_json, plan_json, status, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"], data.get("design_id", ""), data.get("pack_id", ""),
                data.get("job_id", ""), data["combo_id"],
                json.dumps(data.get("selected_types", []), ensure_ascii=False),
                json.dumps(data.get("input_snapshot", {}), ensure_ascii=False),
                json.dumps(data.get("plan", {}), ensure_ascii=False),
                data.get("status", "completed"),
                data.get("created_by", "system"),
                now,
            ),
        )
        self.conn.commit()

    def list_video_script_plans_v2(self, job_id: str | None = None, combo_id: str | None = None) -> list[dict[str, Any]]:
        conditions = []
        params: list[str] = []
        if job_id:
            conditions.append("job_id = ?")
            params.append(job_id)
        if combo_id:
            conditions.append("combo_id = ?")
            params.append(combo_id)
        sql = "SELECT * FROM video_script_plans_v2"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["plan"] = self._loads(d.pop("plan_json", "{}"), {})
            d["input_snapshot"] = self._loads(d.pop("input_json", "{}"), {})
            d["selected_types"] = self._loads(d.pop("selected_types_json", "[]"), [])
            result.append(d)
        return result

    def get_video_script_plan_v2(self, plan_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM video_script_plans_v2 WHERE id = ?", (plan_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["plan"] = self._loads(d.pop("plan_json", "{}"), {})
        d["input_snapshot"] = self._loads(d.pop("input_json", "{}"), {})
        d["selected_types"] = self._loads(d.pop("selected_types_json", "[]"), [])
        return d

    def delete_video_script_plan_v2(self, plan_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM video_script_plans_v2 WHERE id = ?", (plan_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- V2: Video Scripts CRUD ---

    def insert_video_script(self, data: dict) -> None:
        now = _now()
        self.conn.execute(
            """INSERT INTO video_scripts
               (id, design_id, pack_id, job_id, combo_id, plan_id,
                hook_text, cta_text, caption_text, title_options_json,
                script_json, status, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"], data.get("design_id", ""), data.get("pack_id", ""),
                data.get("job_id", ""), data["combo_id"], data.get("plan_id", ""),
                data.get("hook_text", ""), data.get("cta_text", ""),
                data.get("caption_text", ""),
                json.dumps(data.get("title_options", []), ensure_ascii=False),
                json.dumps(data.get("script", {}), ensure_ascii=False),
                data.get("status", "completed"),
                data.get("created_by", "system"),
                now,
            ),
        )
        self.conn.commit()

    def list_video_scripts(self, job_id: str | None = None, combo_id: str | None = None, plan_id: str | None = None) -> list[dict[str, Any]]:
        conditions = []
        params: list[str] = []
        if job_id:
            conditions.append("job_id = ?")
            params.append(job_id)
        if combo_id:
            conditions.append("combo_id = ?")
            params.append(combo_id)
        if plan_id:
            conditions.append("plan_id = ?")
            params.append(plan_id)
        sql = "SELECT * FROM video_scripts"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["script"] = self._loads(d.pop("script_json", "{}"), {})
            d["title_options"] = self._loads(d.pop("title_options_json", "[]"), [])
            result.append(d)
        return result

    def get_video_script(self, script_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM video_scripts WHERE id = ?", (script_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["script"] = self._loads(d.pop("script_json", "{}"), {})
        d["title_options"] = self._loads(d.pop("title_options_json", "[]"), [])
        return d

    def delete_video_script(self, script_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM video_scripts WHERE id = ?", (script_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ── Planning Events ──────────────────────────────────────

    def insert_planning_events(self, events: list[dict[str, Any]]) -> int:
        now = _now()
        inserted = 0
        with self._write_lock:
            for e in events:
                self.conn.execute(
                    """INSERT OR REPLACE INTO planning_events
                       (id, title, category, region, start_date, end_date,
                        short_description, source, raw_json, fetch_batch,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        e["id"], e["title"], e.get("category", ""),
                        e["region"], e["start_date"], e.get("end_date"),
                        e.get("short_description", ""), e.get("source", ""),
                        json.dumps(e.get("raw", {}), ensure_ascii=False),
                        e.get("fetch_batch", ""), now, now,
                    ),
                )
                inserted += 1
            self.conn.commit()
        return inserted

    def list_planning_events(
        self,
        region: str | None = None,
        year: int | None = None,
        month: int | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if region:
            conditions.append("region = ?")
            params.append(region)
        if year and month:
            prefix = f"{year:04d}-{month:02d}"
            conditions.append("start_date LIKE ?")
            params.append(f"{prefix}%")
        sql = "SELECT * FROM planning_events"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY start_date ASC"
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_planning_event(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM planning_events WHERE id = ?", (event_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_planning_event(self, event_id: str) -> bool:
        with self._write_lock:
            cur = self.conn.execute(
                "DELETE FROM planning_events WHERE id = ?", (event_id,)
            )
            self.conn.commit()
        return cur.rowcount > 0

    def delete_planning_events_by_batch(self, batch: str) -> int:
        with self._write_lock:
            cur = self.conn.execute(
                "DELETE FROM planning_events WHERE fetch_batch = ?", (batch,)
            )
            self.conn.commit()
        return cur.rowcount

    def get_planning_stats(self) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT region, COUNT(*) as cnt FROM planning_events GROUP BY region"
        ).fetchall()
        by_region = {r["region"]: r["cnt"] for r in rows}
        total = sum(by_region.values())
        return {"total": total, "by_region": by_region}

    # ── Planning Directions ────────────────────────────────────

    def insert_planning_direction(self, d: dict[str, Any]) -> None:
        with self._write_lock:
            self.conn.execute(
                """INSERT OR REPLACE INTO planning_directions
                   (id, event_id, direction_index, name_en, name_zh,
                    keywords, design_elements, text_slogans, decorative_elements,
                    preview_path, preview_status, gen_status, job_id, sticker_count, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    d["id"], d["event_id"], d.get("direction_index", 0),
                    d["name_en"], d.get("name_zh", ""),
                    d.get("keywords", ""), d.get("design_elements", ""),
                    d.get("text_slogans", ""), d.get("decorative_elements", ""),
                    d.get("preview_path", ""), d.get("preview_status", "pending"),
                    d.get("gen_status", "pending"), d.get("job_id", ""),
                    d.get("sticker_count", 10),
                    d.get("created_at", _now()),
                ),
            )
            self.conn.commit()

    def list_directions_by_event(self, event_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM planning_directions WHERE event_id = ? ORDER BY direction_index",
            (event_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_direction(self, direction_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM planning_directions WHERE id = ?", (direction_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_direction(self, direction_id: str, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [direction_id]
        with self._write_lock:
            self.conn.execute(
                f"UPDATE planning_directions SET {sets} WHERE id = ?", vals,
            )
            self.conn.commit()

    @staticmethod
    def _to_iso(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        return value
