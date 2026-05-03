"""Feedback collection service for V2 pack assets.

Stores operator feedback for packs, preview sheets and individual stickers.
A lightweight scheduled collector can periodically mark new feedback as
collected so the team can review it in batches without interrupting the
production flow.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB_PATH = Path("data/ops_workbench.db")
VALID_TARGET_TYPES = {"pack", "preview", "sticker"}
VALID_STATUSES = {"new", "collected", "reviewed", "closed"}


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class FeedbackService:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    def ensure_schema(self, conn: sqlite3.Connection | None = None) -> None:
        owns_conn = conn is None
        if conn is None:
            conn = _open_db(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL,
                    target_id INTEGER NOT NULL,
                    pack_id INTEGER,
                    rating TEXT DEFAULT '',
                    reason TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at INTEGER NOT NULL,
                    collected_at INTEGER,
                    updated_at INTEGER
                )
                """
            )
            cols = {r[1] for r in conn.execute("PRAGMA table_info(asset_feedback)").fetchall()}
            if "collected_at" not in cols:
                conn.execute("ALTER TABLE asset_feedback ADD COLUMN collected_at INTEGER")
            if "updated_at" not in cols:
                conn.execute("ALTER TABLE asset_feedback ADD COLUMN updated_at INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_asset_feedback_status_created "
                "ON asset_feedback(status, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_asset_feedback_target "
                "ON asset_feedback(target_type, target_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_asset_feedback_pack "
                "ON asset_feedback(pack_id, created_at DESC)"
            )
            if owns_conn:
                conn.commit()
        finally:
            if owns_conn:
                conn.close()

    def create_feedback(
        self,
        *,
        target_type: str,
        target_id: int,
        pack_id: int | None = None,
        rating: str = "",
        reason: str = "",
    ) -> int:
        target_type = (target_type or "").strip().lower()
        if target_type not in VALID_TARGET_TYPES:
            raise ValueError("bad target_type")
        if int(target_id) <= 0:
            raise ValueError("bad target_id")
        reason = (reason or "").strip()[:2000]
        if not reason:
            raise ValueError("reason required")
        now = int(time.time())
        with _open_db(self.db_path) as conn:
            self.ensure_schema(conn)
            cur = conn.execute(
                """
                INSERT INTO asset_feedback
                    (target_type, target_id, pack_id, rating, reason, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'new', ?, ?)
                """,
                (target_type, int(target_id), pack_id, (rating or "").strip()[:40], reason, now, now),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_feedback(
        self,
        *,
        status: str = "open",
        target_type: str = "all",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        self.ensure_schema()
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "all":
            if status == "open":
                clauses.append("f.status IN ('new', 'collected')")
            else:
                clauses.append("f.status = ?")
                params.append(status)
        if target_type and target_type != "all":
            clauses.append("f.target_type = ?")
            params.append(target_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))
        with _open_db(self.db_path) as conn:
            self.ensure_schema(conn)
            total = conn.execute(
                f"SELECT COUNT(*) FROM asset_feedback f {where}", tuple(params)
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT f.id, f.target_type, f.target_id, f.pack_id, f.rating,
                       f.reason, f.status, f.created_at, f.collected_at, f.updated_at,
                       p.display_name AS pack_name,
                       CASE
                         WHEN f.target_type = 'preview' THEN (SELECT pp.image_path FROM pack_previews pp WHERE pp.id = f.target_id)
                         WHEN f.target_type = 'sticker' THEN (SELECT ps.image_path FROM pack_stickers ps WHERE ps.id = f.target_id)
                         WHEN f.target_type = 'pack' THEN COALESCE(NULLIF(p.cover_image_path, ''), '')
                         ELSE ''
                       END AS asset_image_path
                  FROM asset_feedback f
             LEFT JOIN packs p ON p.id = f.pack_id
                       {where}
              ORDER BY f.id DESC
                 LIMIT ? OFFSET ?
                """,
                tuple(params) + (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], int(total or 0)

    def stats(self) -> dict[str, int]:
        self.ensure_schema()
        with _open_db(self.db_path) as conn:
            self.ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                  FROM asset_feedback
              GROUP BY status
                """
            ).fetchall()
        out = {"new": 0, "collected": 0, "reviewed": 0, "closed": 0, "total": 0}
        for r in rows:
            status = r["status"] or "new"
            n = int(r["n"] or 0)
            out[status] = n
            out["total"] += n
        out["open"] = out["new"] + out["collected"]
        return out

    def collect_pending(self, *, limit: int = 500) -> dict[str, int]:
        """Mark a batch of new feedback as collected for scheduled review."""
        self.ensure_schema()
        now = int(time.time())
        limit = max(1, min(int(limit or 500), 5000))
        with _open_db(self.db_path) as conn:
            self.ensure_schema(conn)
            ids = [
                int(r[0]) for r in conn.execute(
                    """
                    SELECT id FROM asset_feedback
                     WHERE status = 'new'
                     ORDER BY created_at ASC
                     LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            ]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"""
                    UPDATE asset_feedback
                       SET status = 'collected', collected_at = ?, updated_at = ?
                     WHERE id IN ({placeholders})
                    """,
                    (now, now, *ids),
                )
            conn.commit()
        return {"collected": len(ids)}

    def update_status(self, feedback_id: int, status: str) -> bool:
        status = (status or "").strip().lower()
        if status not in VALID_STATUSES:
            raise ValueError("bad status")
        now = int(time.time())
        with _open_db(self.db_path) as conn:
            self.ensure_schema(conn)
            cur = conn.execute(
                "UPDATE asset_feedback SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, int(feedback_id)),
            )
            conn.commit()
            return cur.rowcount > 0


_svc: Optional[FeedbackService] = None


def get_feedback_service() -> FeedbackService:
    global _svc
    if _svc is None:
        _svc = FeedbackService()
    return _svc
