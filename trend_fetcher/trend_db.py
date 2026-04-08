"""TikTok 热点数据 SQLite 数据库

Tables:
  - tk_hashtags:         原始爬取数据 + 处理状态追踪
  - tk_crawl_logs:       每次爬取的日志
  - tk_topic_reviews:    AI 审核结果
  - tk_topic_briefs:     AI Brief 结果（旧链路兜底）
  - tk_theme_families:   主题家族（一条 review → 一个 family）
  - tk_subthemes:        子题材（一个 family → 多个 subtheme）
  - tk_subtheme_briefs:  子题材 Brief
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class TrendDB:
    """基于 SQLite 的热点数据库。"""

    def __init__(self, db_path: str | Path = "data/ops_workbench.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._ensure_tables()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Schema ────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        c = self.conn
        c.executescript("""
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

        CREATE TABLE IF NOT EXISTS tk_crawl_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            crawled_at  TEXT NOT NULL,
            country     TEXT,
            period      INTEGER,
            filters_json TEXT,
            new_count   INTEGER DEFAULT 0,
            dup_count   INTEGER DEFAULT 0,
            total_after INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tk_topic_reviews (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            hashtag_id           TEXT NOT NULL REFERENCES tk_hashtags(hashtag_id),
            batch_id             TEXT,
            decision             TEXT,
            review_text          TEXT,
            normalized_theme     TEXT,
            theme_type           TEXT,
            one_line_interpretation TEXT,
            pack_archetype       TEXT,
            best_platform        TEXT,
            visual_symbols       TEXT,
            emotional_hooks      TEXT,
            risk_flags           TEXT,
            score_total          INTEGER,
            sticker_fit_level    TEXT,
            reviewed_at          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tk_topic_briefs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            hashtag_id        TEXT NOT NULL REFERENCES tk_hashtags(hashtag_id),
            review_id         INTEGER REFERENCES tk_topic_reviews(id),
            batch_id          TEXT,
            brief_status      TEXT,
            brief_text        TEXT,
            trend_name        TEXT,
            trend_type        TEXT,
            lifecycle         TEXT,
            platform          TEXT,
            product_goal      TEXT,
            target_audience   TEXT,
            emotional_core    TEXT,
            visual_symbols    TEXT,
            pack_size_goal    TEXT,
            generated_at      TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_hashtags_review
            ON tk_hashtags(review_status);
        CREATE INDEX IF NOT EXISTS idx_hashtags_brief
            ON tk_hashtags(brief_status);
        CREATE INDEX IF NOT EXISTS idx_reviews_decision
            ON tk_topic_reviews(decision);
        CREATE INDEX IF NOT EXISTS idx_reviews_hashtag
            ON tk_topic_reviews(hashtag_id);
        CREATE INDEX IF NOT EXISTS idx_briefs_hashtag
            ON tk_topic_briefs(hashtag_id);

        -- Theme Family tables (v2 pipeline) --
        CREATE TABLE IF NOT EXISTS tk_theme_families (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            hashtag_id            TEXT NOT NULL REFERENCES tk_hashtags(hashtag_id),
            review_id             INTEGER REFERENCES tk_topic_reviews(id),
            parent_theme          TEXT,
            shared_emotional_core TEXT,
            shared_visual_core    TEXT,
            shared_platform_fit   TEXT,
            family_raw_text       TEXT,
            generated_at          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tk_subthemes (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            family_id                   INTEGER NOT NULL REFERENCES tk_theme_families(id),
            subtheme_name               TEXT,
            subtheme_type               TEXT,
            one_line_direction          TEXT,
            recommended_pack_archetype  TEXT,
            recommended_pack_size       TEXT,
            natural_sticker_count_range TEXT,
            ai_selected                 INTEGER DEFAULT 1,
            ai_priority                 TEXT DEFAULT 'medium',
            ai_target_sticker_count     INTEGER DEFAULT 0,
            ai_reason                   TEXT,
            selected                    INTEGER DEFAULT 0,
            priority                    TEXT DEFAULT 'medium',
            target_sticker_count        INTEGER DEFAULT 0,
            allocation_notes            TEXT,
            status                      TEXT DEFAULT 'draft',
            created_at                  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tk_subtheme_briefs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            subtheme_id   INTEGER NOT NULL REFERENCES tk_subthemes(id),
            brief_status  TEXT,
            brief_text    TEXT,
            brief_json    TEXT,
            generated_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_families_hashtag
            ON tk_theme_families(hashtag_id);
        CREATE INDEX IF NOT EXISTS idx_subthemes_family
            ON tk_subthemes(family_id);
        CREATE INDEX IF NOT EXISTS idx_subthemes_status
            ON tk_subthemes(status);
        CREATE INDEX IF NOT EXISTS idx_subtheme_briefs_subtheme
            ON tk_subtheme_briefs(subtheme_id);
        """)
        c.commit()

        self._migrate_add_columns()

    def _migrate_add_columns(self) -> None:
        """Safely add columns introduced in v2 pipeline."""
        c = self.conn
        for col, default in [
            ("family_status", "'pending'"),
            ("allocation_status", "'pending'"),
        ]:
            try:
                c.execute(f"ALTER TABLE tk_hashtags ADD COLUMN {col} TEXT DEFAULT {default}")
                c.commit()
            except sqlite3.OperationalError:
                pass

    # ── 爬取数据写入 ─────────────────────────────────────

    def upsert_crawl(self, crawl_result: dict[str, Any]) -> dict[str, int]:
        """将一次爬取结果写入数据库，按 hashtag_id 去重。

        Args:
            crawl_result: TikTokFetcher.fetch() 的返回值

        Returns:
            {"new": N, "duplicate": M, "total": T}
        """
        meta = crawl_result.get("meta", {})
        incoming = crawl_result.get("hashtags", {})
        crawl_ts = meta.get("crawled_at", _now_iso())

        c = self.conn
        new_count = 0
        dup_count = 0

        for hid, data in incoming.items():
            existing = c.execute(
                "SELECT hashtag_id, found_in_filters FROM tk_hashtags WHERE hashtag_id = ?",
                (hid,)
            ).fetchone()

            if existing:
                old_filters = json.loads(existing["found_in_filters"] or "[]")
                new_filters = data.get("found_in_filters", [])
                merged = sorted(set(old_filters) | set(new_filters))
                ld = data.get("list_data") or {}
                detail = data.get("detail_data")
                list_json = json.dumps(ld, ensure_ascii=False)
                name = (ld.get("hashtag_name") or "").strip()
                v_views = int(ld.get("video_views") or 0)
                v_pub = int(ld.get("publish_cnt") or 0)
                at = data.get("crawled_at", crawl_ts)

                if detail:
                    detail_json = json.dumps(detail, ensure_ascii=False)
                    creators_json = json.dumps(
                        data.get("creators_raw", []), ensure_ascii=False
                    )
                    c.execute(
                        """
                        UPDATE tk_hashtags SET
                            last_seen_at = ?,
                            found_in_filters = ?,
                            hashtag_name = CASE WHEN ? != '' THEN ? ELSE hashtag_name END,
                            video_views = ?,
                            publish_cnt = ?,
                            list_data_json = ?,
                            detail_data_json = ?,
                            creators_raw_json = ?,
                            crawled_at = ?,
                            review_status = 'pending',
                            brief_status = 'pending'
                        WHERE hashtag_id = ?
                        """,
                        (
                            crawl_ts,
                            json.dumps(merged),
                            name,
                            name,
                            v_views,
                            v_pub,
                            list_json,
                            detail_json,
                            creators_json,
                            at,
                            hid,
                        ),
                    )
                else:
                    c.execute(
                        """
                        UPDATE tk_hashtags SET
                            last_seen_at = ?,
                            found_in_filters = ?,
                            hashtag_name = CASE WHEN ? != '' THEN ? ELSE hashtag_name END,
                            video_views = ?,
                            publish_cnt = ?,
                            list_data_json = ?,
                            crawled_at = ?
                        WHERE hashtag_id = ?
                        """,
                        (
                            crawl_ts,
                            json.dumps(merged),
                            name,
                            name,
                            v_views,
                            v_pub,
                            list_json,
                            at,
                            hid,
                        ),
                    )
                dup_count += 1
            else:
                ld = data.get("list_data") or {}
                c.execute(
                    """INSERT INTO tk_hashtags
                    (hashtag_id, hashtag_name, video_views, publish_cnt,
                     list_data_json, detail_data_json, creators_raw_json,
                     found_in_filters, crawled_at, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        hid,
                        ld.get("hashtag_name", ""),
                        ld.get("video_views", 0),
                        ld.get("publish_cnt", 0),
                        json.dumps(ld, ensure_ascii=False),
                        json.dumps(data.get("detail_data"), ensure_ascii=False)
                        if data.get("detail_data") else None,
                        json.dumps(data.get("creators_raw", []), ensure_ascii=False),
                        json.dumps(data.get("found_in_filters", [])),
                        data.get("crawled_at", crawl_ts),
                        crawl_ts,
                        crawl_ts,
                    )
                )
                new_count += 1

        total = c.execute("SELECT COUNT(*) FROM tk_hashtags").fetchone()[0]

        c.execute(
            """INSERT INTO tk_crawl_logs
            (crawled_at, country, period, filters_json, new_count, dup_count, total_after)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                crawl_ts,
                meta.get("country", ""),
                meta.get("period", 0),
                json.dumps(meta.get("filters", [])),
                new_count, dup_count, total,
            )
        )
        c.commit()

        return {"new": new_count, "duplicate": dup_count, "total": total}

    # ── 查询方法 ──────────────────────────────────────────

    def total(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM tk_hashtags").fetchone()[0]

    def get_hashtag(self, hashtag_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tk_hashtags WHERE hashtag_id = ?", (hashtag_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_tk_hashtags(self, limit: int = 0) -> list[dict]:
        sql = "SELECT * FROM tk_hashtags ORDER BY video_views DESC"
        if limit:
            sql += f" LIMIT {limit}"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def crawl_history(self) -> list[dict]:
        return [
            dict(r) for r in
            self.conn.execute("SELECT * FROM tk_crawl_logs ORDER BY id").fetchall()
        ]

    # ── Pipeline 状态追踪 ────────────────────────────────

    def get_unreviewed(self) -> list[dict]:
        """获取所有未审核且有详情数据的 hashtag。"""
        rows = self.conn.execute(
            "SELECT * FROM tk_hashtags "
            "WHERE review_status = 'pending' AND detail_data_json IS NOT NULL "
            "ORDER BY video_views DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_approved_without_brief(self) -> list[dict]:
        """获取所有已通过审核但尚未生成 Brief 的 hashtag + review 信息。"""
        rows = self.conn.execute(
            """SELECT h.*, r.id as review_id, r.decision, r.normalized_theme,
                      r.theme_type, r.one_line_interpretation, r.pack_archetype,
                      r.best_platform, r.visual_symbols, r.emotional_hooks,
                      r.risk_flags, r.score_total, r.sticker_fit_level
               FROM tk_hashtags h
               JOIN tk_topic_reviews r ON r.hashtag_id = h.hashtag_id
               WHERE r.decision = 'approve'
                 AND h.brief_status = 'pending'
               ORDER BY r.score_total DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def save_review(
        self,
        hashtag_id: str,
        parsed: dict[str, Any],
        full_text: str,
        batch_id: str,
    ) -> int:
        """保存审核结果，更新 hashtag 状态。"""
        c = self.conn
        now = _now_iso()
        decision = parsed.get("decision", "unknown")

        cur = c.execute(
            """INSERT INTO tk_topic_reviews
            (hashtag_id, batch_id, decision, review_text,
             normalized_theme, theme_type, one_line_interpretation,
             pack_archetype, best_platform, visual_symbols,
             emotional_hooks, risk_flags, score_total, sticker_fit_level,
             reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hashtag_id, batch_id, decision, full_text,
                parsed.get("normalized_theme", ""),
                parsed.get("theme_type", ""),
                parsed.get("one_line_interpretation", ""),
                parsed.get("pack_archetype", ""),
                parsed.get("best_platform", ""),
                parsed.get("visual_symbols", ""),
                parsed.get("emotional_hooks", ""),
                parsed.get("risk_flags", ""),
                parsed.get("score_total", 0),
                parsed.get("sticker_fit_level", ""),
                now,
            )
        )
        review_id = cur.lastrowid

        brief_status = "pending" if decision == "approve" else "not_applicable"
        c.execute(
            "UPDATE tk_hashtags SET review_status = 'reviewed', brief_status = ? "
            "WHERE hashtag_id = ?",
            (brief_status, hashtag_id)
        )
        c.commit()
        return review_id

    def save_brief(
        self,
        hashtag_id: str,
        review_id: int,
        parsed: dict[str, Any],
        full_text: str,
        batch_id: str,
    ) -> int:
        """保存 Brief 结果，更新 hashtag 状态。"""
        c = self.conn
        now = _now_iso()

        cur = c.execute(
            """INSERT INTO tk_topic_briefs
            (hashtag_id, review_id, batch_id, brief_status, brief_text,
             trend_name, trend_type, lifecycle, platform, product_goal,
             target_audience, emotional_core, visual_symbols,
             pack_size_goal, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hashtag_id, review_id, batch_id,
                parsed.get("brief_status", ""),
                full_text,
                parsed.get("trend_name", ""),
                parsed.get("trend_type", ""),
                parsed.get("lifecycle", ""),
                parsed.get("platform", ""),
                parsed.get("product_goal", ""),
                parsed.get("target_audience", ""),
                parsed.get("emotional_core", ""),
                parsed.get("visual_symbols", ""),
                parsed.get("pack_size_goal", ""),
                now,
            )
        )

        c.execute(
            "UPDATE tk_hashtags SET brief_status = 'generated' WHERE hashtag_id = ?",
            (hashtag_id,)
        )
        c.commit()
        return cur.lastrowid

    # ── 统计 ──────────────────────────────────────────────

    # ── Theme Family CRUD ────────────────────────────────

    def get_families_needing_expansion(self) -> list[dict]:
        """获取所有 decision=approve 且 family_status=pending 的 hashtag + review。"""
        rows = self.conn.execute(
            """SELECT h.*, r.id as review_id, r.decision, r.normalized_theme,
                      r.theme_type, r.one_line_interpretation, r.pack_archetype,
                      r.best_platform, r.visual_symbols, r.emotional_hooks,
                      r.risk_flags, r.score_total, r.sticker_fit_level
               FROM tk_hashtags h
               JOIN tk_topic_reviews r ON r.hashtag_id = h.hashtag_id
               WHERE r.decision IN ('approve')
                 AND h.family_status = 'pending'
               ORDER BY r.score_total DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def save_theme_family(
        self,
        hashtag_id: str,
        review_id: int,
        parsed: dict[str, Any],
        raw_text: str,
    ) -> int:
        """保存主题家族及其子题材（含AI推荐配量）。"""
        c = self.conn
        now = _now_iso()
        shared = parsed.get("shared_core", {})

        cur = c.execute(
            """INSERT INTO tk_theme_families
            (hashtag_id, review_id, parent_theme,
             shared_emotional_core, shared_visual_core, shared_platform_fit,
             family_raw_text, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                hashtag_id, review_id,
                parsed.get("parent_topic", ""),
                shared.get("emotional_core", ""),
                shared.get("visual_core", ""),
                shared.get("platform_fit", ""),
                raw_text,
                now,
            )
        )
        family_id = cur.lastrowid

        for sub in parsed.get("subthemes", []):
            ai_sel = 1 if sub.get("ai_selected", True) else 0
            ai_count = int(sub.get("ai_target_sticker_count", 0))
            c.execute(
                """INSERT INTO tk_subthemes
                (family_id, subtheme_name, subtheme_type, one_line_direction,
                 recommended_pack_archetype, recommended_pack_size,
                 natural_sticker_count_range,
                 ai_selected, ai_priority, ai_target_sticker_count, ai_reason,
                 selected, priority, target_sticker_count,
                 status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
                (
                    family_id,
                    sub.get("subtheme_name", ""),
                    sub.get("subtheme_type", ""),
                    sub.get("one_line_direction", ""),
                    sub.get("recommended_pack_archetype", ""),
                    sub.get("recommended_pack_size", ""),
                    sub.get("natural_sticker_count_range", ""),
                    ai_sel,
                    sub.get("ai_priority", "medium"),
                    ai_count,
                    sub.get("ai_reason", ""),
                    ai_sel,
                    sub.get("ai_priority", "medium"),
                    ai_count,
                    now,
                )
            )

        c.execute(
            "UPDATE tk_hashtags SET family_status = 'generated' WHERE hashtag_id = ?",
            (hashtag_id,)
        )
        c.commit()
        return family_id

    def get_theme_family(self, family_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tk_theme_families WHERE id = ?", (family_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_family_by_hashtag(self, hashtag_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tk_theme_families WHERE hashtag_id = ? ORDER BY id DESC LIMIT 1",
            (hashtag_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_subthemes(self, family_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tk_subthemes WHERE family_id = ? ORDER BY id",
            (family_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_subtheme(self, subtheme_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tk_subthemes WHERE id = ?", (subtheme_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_subtheme_allocation(self, subtheme_id: int, data: dict) -> None:
        """更新人工确认配量。"""
        c = self.conn
        c.execute(
            """UPDATE tk_subthemes SET
                selected = ?,
                priority = ?,
                target_sticker_count = ?,
                allocation_notes = ?
            WHERE id = ?""",
            (
                1 if data.get("selected") else 0,
                data.get("priority", "medium"),
                int(data.get("target_sticker_count", 0)),
                data.get("allocation_notes", ""),
                subtheme_id,
            )
        )
        c.commit()

    def confirm_ai_allocation(self, family_id: int) -> None:
        """一键确认 AI 推荐配量，将 ai_* 复制到正式字段。"""
        c = self.conn
        c.execute(
            """UPDATE tk_subthemes SET
                selected = ai_selected,
                priority = ai_priority,
                target_sticker_count = ai_target_sticker_count,
                status = CASE WHEN ai_selected = 1 THEN 'brief_pending' ELSE 'skipped' END
            WHERE family_id = ?""",
            (family_id,)
        )
        c.commit()

    def finalize_allocation(self, family_id: int, hashtag_id: str) -> None:
        """确认配量后更新子题材 status 和 hashtag allocation_status。"""
        c = self.conn
        c.execute(
            """UPDATE tk_subthemes SET
                status = CASE WHEN selected = 1 THEN 'brief_pending' ELSE 'skipped' END
            WHERE family_id = ?""",
            (family_id,)
        )
        c.execute(
            "UPDATE tk_hashtags SET allocation_status = 'allocated' WHERE hashtag_id = ?",
            (hashtag_id,)
        )
        c.commit()

    def get_selected_subthemes_without_brief(self, family_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT s.*, f.parent_theme, f.shared_emotional_core,
                      f.shared_visual_core, f.shared_platform_fit,
                      f.hashtag_id, f.review_id
               FROM tk_subthemes s
               JOIN tk_theme_families f ON f.id = s.family_id
               WHERE s.family_id = ?
                 AND s.selected = 1
                 AND s.status = 'brief_pending'
               ORDER BY s.id""",
            (family_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def save_subtheme_brief(
        self,
        subtheme_id: int,
        parsed: dict[str, Any],
        raw_text: str,
    ) -> int:
        c = self.conn
        now = _now_iso()
        cur = c.execute(
            """INSERT INTO tk_subtheme_briefs
            (subtheme_id, brief_status, brief_text, brief_json, generated_at)
            VALUES (?, 'generated', ?, ?, ?)""",
            (
                subtheme_id,
                raw_text,
                json.dumps(parsed, ensure_ascii=False),
                now,
            )
        )
        c.execute(
            "UPDATE tk_subthemes SET status = 'brief_ready' WHERE id = ?",
            (subtheme_id,)
        )
        c.commit()
        return cur.lastrowid

    def get_subtheme_brief(self, subtheme_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM tk_subtheme_briefs WHERE subtheme_id = ? ORDER BY id DESC LIMIT 1",
            (subtheme_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_subthemes_with_brief(self, family_id: int) -> list[dict]:
        """获取已有 brief 的选中子题材。"""
        rows = self.conn.execute(
            """SELECT s.*, sb.brief_json, sb.brief_status as brief_gen_status
               FROM tk_subthemes s
               LEFT JOIN tk_subtheme_briefs sb ON sb.subtheme_id = s.id
               WHERE s.family_id = ?
                 AND s.selected = 1
                 AND s.status = 'brief_ready'
               ORDER BY s.id""",
            (family_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_subthemes_by_ids(self, subtheme_ids: list[int]) -> None:
        """删除指定子题材及其 brief（用于选中重新生成）。"""
        if not subtheme_ids:
            return
        c = self.conn
        ph = ",".join("?" * len(subtheme_ids))
        c.execute(f"DELETE FROM tk_subtheme_briefs WHERE subtheme_id IN ({ph})", subtheme_ids)
        c.execute(f"DELETE FROM tk_subthemes WHERE id IN ({ph})", subtheme_ids)
        c.commit()

    def add_subthemes_to_family(self, family_id: int, subthemes: list[dict]) -> list[int]:
        """向已有家族追加子题材（补充扩展 / 替换重生成后写回）。"""
        c = self.conn
        now = _now_iso()
        new_ids: list[int] = []
        for sub in subthemes:
            ai_sel = 1 if sub.get("ai_selected", True) else 0
            ai_count = int(sub.get("ai_target_sticker_count", 0))
            cur = c.execute(
                """INSERT INTO tk_subthemes
                (family_id, subtheme_name, subtheme_type, one_line_direction,
                 recommended_pack_archetype, recommended_pack_size,
                 natural_sticker_count_range,
                 ai_selected, ai_priority, ai_target_sticker_count, ai_reason,
                 selected, priority, target_sticker_count,
                 status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
                (
                    family_id,
                    sub.get("subtheme_name", ""),
                    sub.get("subtheme_type", ""),
                    sub.get("one_line_direction", ""),
                    sub.get("recommended_pack_archetype", ""),
                    sub.get("recommended_pack_size", ""),
                    sub.get("natural_sticker_count_range", ""),
                    ai_sel,
                    sub.get("ai_priority", "medium"),
                    ai_count,
                    sub.get("ai_reason", ""),
                    ai_sel,
                    sub.get("ai_priority", "medium"),
                    ai_count,
                    now,
                )
            )
            new_ids.append(cur.lastrowid)
        c.commit()
        return new_ids

    def delete_family(self, family_id: int) -> None:
        """删除家族及其所有子题材和 brief（全部推倒重来）。"""
        c = self.conn
        sub_ids = [r[0] for r in c.execute(
            "SELECT id FROM tk_subthemes WHERE family_id = ?", (family_id,)
        ).fetchall()]
        if sub_ids:
            placeholders = ",".join("?" * len(sub_ids))
            c.execute(f"DELETE FROM tk_subtheme_briefs WHERE subtheme_id IN ({placeholders})", sub_ids)
        c.execute("DELETE FROM tk_subthemes WHERE family_id = ?", (family_id,))
        family = c.execute("SELECT hashtag_id FROM tk_theme_families WHERE id = ?", (family_id,)).fetchone()
        c.execute("DELETE FROM tk_theme_families WHERE id = ?", (family_id,))
        if family:
            c.execute(
                "UPDATE tk_hashtags SET family_status = 'pending', allocation_status = 'pending' "
                "WHERE hashtag_id = ?",
                (family["hashtag_id"],)
            )
        c.commit()

    # ── 统计 ──────────────────────────────────────────────

    def status_summary(self) -> dict[str, Any]:
        c = self.conn
        total = c.execute("SELECT COUNT(*) FROM tk_hashtags").fetchone()[0]
        with_detail = c.execute(
            "SELECT COUNT(*) FROM tk_hashtags WHERE detail_data_json IS NOT NULL"
        ).fetchone()[0]
        reviewed = c.execute(
            "SELECT COUNT(*) FROM tk_hashtags WHERE review_status = 'reviewed'"
        ).fetchone()[0]
        pending_review = c.execute(
            "SELECT COUNT(*) FROM tk_hashtags "
            "WHERE review_status = 'pending' AND detail_data_json IS NOT NULL"
        ).fetchone()[0]

        approve = c.execute(
            "SELECT COUNT(*) FROM tk_topic_reviews WHERE decision = 'approve'"
        ).fetchone()[0]
        watchlist = c.execute(
            "SELECT COUNT(*) FROM tk_topic_reviews WHERE decision = 'watchlist'"
        ).fetchone()[0]
        reject = c.execute(
            "SELECT COUNT(*) FROM tk_topic_reviews WHERE decision = 'reject'"
        ).fetchone()[0]

        briefs_done = c.execute(
            "SELECT COUNT(*) FROM tk_hashtags WHERE brief_status = 'generated'"
        ).fetchone()[0]
        briefs_pending = c.execute(
            "SELECT COUNT(*) FROM tk_hashtags "
            "WHERE brief_status = 'pending' AND review_status = 'reviewed'"
        ).fetchone()[0]

        families = c.execute("SELECT COUNT(*) FROM tk_theme_families").fetchone()[0]
        families_pending = c.execute(
            "SELECT COUNT(*) FROM tk_hashtags WHERE family_status = 'pending' AND review_status = 'reviewed'"
        ).fetchone()[0]
        alloc_pending = c.execute(
            "SELECT COUNT(*) FROM tk_hashtags WHERE allocation_status = 'pending' AND family_status = 'generated'"
        ).fetchone()[0]

        crawls = c.execute("SELECT COUNT(*) FROM tk_crawl_logs").fetchone()[0]

        return {
            "total_hashtags": total,
            "with_detail": with_detail,
            "reviewed": reviewed,
            "pending_review": pending_review,
            "approve": approve,
            "watchlist": watchlist,
            "reject": reject,
            "briefs_generated": briefs_done,
            "briefs_pending": briefs_pending,
            "families_generated": families,
            "families_pending": families_pending,
            "allocation_pending": alloc_pending,
            "total_crawls": crawls,
        }
