"""TikTok Display API metrics snapshots — for the V2 数据看板.

The TikTok Display API only returns the *current* engagement counters for
a video (no history), so we snapshot every fetch into a local table.
That lets the v2 analytics page show:
  * the current view/like/comment/share counts per video,
  * the delta vs. the previous snapshot,
  * a short history in the per-video detail view.

This service intentionally lives next to ``TikTokDisplayService`` (which
owns the OAuth tokens) but writes into the main ops_workbench.db so the
data is visible to scheduler jobs and other v2 services.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.tiktok.tiktok_display_service import TikTokDisplayService

logger = get_logger("services.tiktok.display_metrics")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS tk_display_video_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    open_id           TEXT    NOT NULL,
    tiktok_video_id   TEXT    NOT NULL,
    title             TEXT    DEFAULT '',
    video_description TEXT    DEFAULT '',
    cover_image_url   TEXT    DEFAULT '',
    share_url         TEXT    DEFAULT '',
    duration          INTEGER DEFAULT 0,
    create_time       INTEGER DEFAULT 0,
    view_count        INTEGER DEFAULT 0,
    like_count        INTEGER DEFAULT 0,
    comment_count     INTEGER DEFAULT 0,
    share_count       INTEGER DEFAULT 0,
    fetched_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tk_dvs_account_video
    ON tk_display_video_snapshots(open_id, tiktok_video_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_tk_dvs_account_time
    ON tk_display_video_snapshots(open_id, fetched_at);
"""


class TKDisplayMetricsService:
    def __init__(
        self,
        display: Optional[TikTokDisplayService] = None,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.display = display or TikTokDisplayService()
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with _open_db(self.db_path) as conn:
            conn.executescript(_TABLE_DDL)
            conn.commit()

    # ------------------------------------------------------------------
    # Snapshot writing — call this whenever you want a fresh data point
    # ------------------------------------------------------------------

    def snapshot_account(
        self, open_id: str, *, max_pages: int = 5, page_size: int = 20,
    ) -> dict[str, Any]:
        """Pull the latest video list for one account and append a snapshot
        row per video.

        Returns ``{"appended": N, "videos": [...]}`` with the live items so
        the caller can render them immediately without a second fetch.
        """
        token = self.display.get_valid_token(open_id)
        if not token:
            raise RuntimeError("token expired or missing — please re-authorize")

        all_videos: list[dict[str, Any]] = []
        cursor: Optional[int] = None
        for _ in range(max_pages):
            page = self.display.get_video_list(
                token["access_token"], max_count=page_size, cursor=cursor,
            )
            items = page.get("videos") or []
            all_videos.extend(items)
            if not page.get("has_more"):
                break
            cursor = page.get("cursor") or 0
            if not cursor:
                break

        now = int(time.time())
        with _open_db(self.db_path) as conn:
            for v in all_videos:
                conn.execute(
                    """
                    INSERT INTO tk_display_video_snapshots
                        (open_id, tiktok_video_id, title, video_description,
                         cover_image_url, share_url, duration, create_time,
                         view_count, like_count, comment_count, share_count,
                         fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        open_id,
                        str(v.get("id") or ""),
                        v.get("title") or "",
                        v.get("video_description") or "",
                        v.get("cover_image_url") or "",
                        v.get("share_url") or "",
                        int(v.get("duration") or 0),
                        int(v.get("create_time") or 0),
                        int(v.get("view_count") or 0),
                        int(v.get("like_count") or 0),
                        int(v.get("comment_count") or 0),
                        int(v.get("share_count") or 0),
                        now,
                    ),
                )
            conn.commit()
        return {"appended": len(all_videos), "videos": all_videos, "fetched_at": now}

    # ------------------------------------------------------------------
    # Read paths used by the v2 analytics page
    # ------------------------------------------------------------------

    def latest_per_video(
        self,
        open_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return one row per tiktok_video_id, joined with the previous
        snapshot so the UI can display delta values, plus the total count.

        Pass ``limit`` / ``offset`` to paginate. When ``limit`` is None the
        full list is returned (kept for callers that want every row).

        The result is ordered by ``create_time DESC`` (newest TikTok video
        first). When only one snapshot exists for a video, the delta fields
        are 0 and ``has_prev`` is False.
        """
        with _open_db(self.db_path) as conn:
            total_row = conn.execute(
                """
                SELECT COUNT(DISTINCT tiktok_video_id) AS n
                  FROM tk_display_video_snapshots
                 WHERE open_id = ?
                """,
                (open_id,),
            ).fetchone()
            total = int(total_row["n"]) if total_row else 0

            sql = """
                WITH ranked AS (
                  SELECT
                    s.*,
                    ROW_NUMBER() OVER (
                      PARTITION BY tiktok_video_id ORDER BY fetched_at DESC
                    ) AS rn
                  FROM tk_display_video_snapshots s
                  WHERE open_id = ?
                )
                SELECT
                  curr.tiktok_video_id,
                  curr.title, curr.video_description,
                  curr.cover_image_url, curr.share_url,
                  curr.duration, curr.create_time,
                  curr.view_count    AS view_count,
                  curr.like_count    AS like_count,
                  curr.comment_count AS comment_count,
                  curr.share_count   AS share_count,
                  curr.fetched_at    AS latest_fetched_at,
                  prev.view_count    AS prev_view_count,
                  prev.like_count    AS prev_like_count,
                  prev.comment_count AS prev_comment_count,
                  prev.share_count   AS prev_share_count,
                  prev.fetched_at    AS prev_fetched_at
                FROM ranked curr
                LEFT JOIN ranked prev
                  ON  prev.tiktok_video_id = curr.tiktok_video_id
                  AND prev.rn = 2
                WHERE curr.rn = 1
                ORDER BY curr.create_time DESC, curr.tiktok_video_id ASC
            """
            params: list[Any] = [open_id]
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params += [int(limit), int(offset)]
            rows = conn.execute(sql, tuple(params)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            has_prev = d.get("prev_fetched_at") is not None
            d["has_prev"] = has_prev
            for k in ("view", "like", "comment", "share"):
                cur = d.get(f"{k}_count") or 0
                prev = d.get(f"prev_{k}_count") or 0
                d[f"delta_{k}"] = (cur - prev) if has_prev else 0
            engagements = d["like_count"] + d["comment_count"] + d["share_count"]
            d["interaction_rate"] = (
                engagements / d["view_count"] if d["view_count"] else 0.0
            )
            out.append(d)
        return out, total

    def video_history(
        self, open_id: str, tiktok_video_id: str, *, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """All recorded snapshots for one video, newest first."""
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT view_count, like_count, comment_count, share_count,
                       fetched_at
                  FROM tk_display_video_snapshots
                 WHERE open_id = ? AND tiktok_video_id = ?
                 ORDER BY fetched_at DESC
                 LIMIT ?
                """,
                (open_id, tiktok_video_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def video_meta(
        self, open_id: str, tiktok_video_id: str,
    ) -> Optional[dict[str, Any]]:
        """Latest stored metadata (title/cover/share_url/...) for one video."""
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                """
                SELECT tiktok_video_id, title, video_description,
                       cover_image_url, share_url, duration, create_time,
                       view_count, like_count, comment_count, share_count,
                       fetched_at
                  FROM tk_display_video_snapshots
                 WHERE open_id = ? AND tiktok_video_id = ?
                 ORDER BY fetched_at DESC LIMIT 1
                """,
                (open_id, tiktok_video_id),
            ).fetchone()
        return dict(r) if r else None

    def last_snapshot_at(self, open_id: str) -> Optional[int]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                "SELECT MAX(fetched_at) AS t FROM tk_display_video_snapshots WHERE open_id = ?",
                (open_id,),
            ).fetchone()
        return r["t"] if r and r["t"] else None


_svc: Optional[TKDisplayMetricsService] = None


def get_tk_display_metrics_service() -> TKDisplayMetricsService:
    global _svc
    if _svc is None:
        _svc = TKDisplayMetricsService()
    return _svc
