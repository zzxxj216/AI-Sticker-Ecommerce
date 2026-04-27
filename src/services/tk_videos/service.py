"""TK video service — B.1 of the V2 pipeline.

Manages tk_videos rows: create from a pack, save uploaded video file,
two-step AI caption generation (English), schedule, dispatch via
Blotato, refresh metrics.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router
from src.services.storage.pack_store import PackStore, get_pack_store
from src.services.tk_videos.prompts import (
    CAPTION_EXTRACT_INSTRUCTIONS,
    CAPTION_EXTRACT_SCHEMA,
    CAPTION_MAIN_SYSTEM_PROMPT,
    build_caption_main_prompt,
)

logger = get_logger("service.tk_videos")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

PUBLISH_STATUSES = (
    "pending",      # row exists, no schedule yet
    "scheduled",    # operator set scheduled_at, waiting for dispatcher
    "dispatching",  # dispatcher actively talking to Blotato
    "published",    # Blotato confirmed published
    "failed",       # dispatch or publish failed
)


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class TKVideoService:
    def __init__(
        self,
        router: Optional[AIRouter] = None,
        store: Optional[PackStore] = None,
        db_path: Path = DEFAULT_DB_PATH,
    ) -> None:
        self.router = router or get_router()
        self.store = store or get_pack_store()
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Create / upload
    # ------------------------------------------------------------------

    def create_video(
        self,
        pack_id: int,
        *,
        account_open_id: str = "",
        video_one_liner: str = "",
    ) -> int:
        with _open_db(self.db_path) as conn:
            pack = conn.execute(
                "SELECT id, pack_uid FROM packs WHERE id = ?", (pack_id,),
            ).fetchone()
            if not pack:
                raise ValueError(f"pack #{pack_id} not found")
            cur = conn.execute(
                """
                INSERT INTO tk_videos
                    (pack_id, account_open_id, local_video_path,
                     video_one_liner, caption_main_raw_text, caption,
                     hashtags, scheduled_at, blotato_post_id,
                     publish_status, published_at, publish_error)
                VALUES (?, ?, '', ?, '', '', '[]', NULL, '', 'pending', NULL, '')
                """,
                (pack_id, account_open_id, video_one_liner[:500]),
            )
            conn.commit()
            return cur.lastrowid

    def save_video_file(self, video_id: int, src_path: Path,
                        *, original_filename: str = "local.mp4") -> str:
        """Move/copy an uploaded file into the pack tree, update DB."""
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT v.id, p.pack_uid
                  FROM tk_videos v
                  JOIN packs     p ON p.id = v.pack_id
                 WHERE v.id = ?
                """,
                (video_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"video #{video_id} not found")
        # PackStore.save_video_file uses .copy2; we already wrote a tmp file
        # from the upload handler so copy then unlink the tmp.
        target_filename = "local.mp4"
        ext = Path(original_filename).suffix.lower()
        if ext in (".mp4", ".mov", ".webm", ".mkv"):
            target_filename = f"local{ext}"
        dest = self.store.save_video_file(
            row["pack_uid"], video_id, src_path,
            target_filename=target_filename,
        )
        rel = dest.as_posix()
        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE tk_videos SET local_video_path = ? WHERE id = ?",
                (rel, video_id),
            )
            conn.commit()
        logger.info("video #%d file saved → %s", video_id, rel)
        return rel

    # ------------------------------------------------------------------
    # AI caption (two-step)
    # ------------------------------------------------------------------

    def generate_caption(
        self,
        video_id: int,
        *,
        main_model: Optional[str] = None,
        extract_model: Optional[str] = None,
    ) -> dict[str, Any]:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT v.id, v.video_one_liner,
                       p.id AS pack_id, p.display_name, p.total_stickers,
                       s.style_anchor, s.palette, s.pack_archetype
                  FROM tk_videos    v
                  JOIN packs        p ON p.id = v.pack_id
             LEFT JOIN pack_series  s ON s.id = p.series_id
                 WHERE v.id = ?
                """,
                (video_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"video #{video_id} not found")

        prompt = build_caption_main_prompt(
            pack_display_name=row["display_name"] or "",
            pack_archetype=row["pack_archetype"] or "",
            style_anchor=row["style_anchor"] or "",
            palette=row["palette"] or "",
            total_stickers=row["total_stickers"] or 0,
            video_one_liner=row["video_one_liner"] or "",
        )

        try:
            main_text = self.router.text_complete(
                prompt,
                model=main_model,
                system=CAPTION_MAIN_SYSTEM_PROMPT,
                temperature=0.85,
                task="tk_caption:main",
                related_table="tk_videos",
                related_id=video_id,
            )
        except Exception as e:
            logger.exception("caption main step failed for video #%d", video_id)
            raise

        extract_ok = True
        extract_error = ""
        payload: dict[str, Any] = {}
        try:
            payload = self.router.extract_json(
                main_text,
                schema=CAPTION_EXTRACT_SCHEMA,
                instructions=CAPTION_EXTRACT_INSTRUCTIONS,
                model=extract_model,
                max_retries=1,
                task="tk_caption:extract",
                related_table="tk_videos",
                related_id=video_id,
            )
        except Exception as e:
            logger.warning("caption extract failed for video #%d: %s", video_id, e)
            extract_ok = False
            extract_error = str(e)[:500]

        with _open_db(self.db_path) as conn:
            if extract_ok:
                conn.execute(
                    """
                    UPDATE tk_videos
                       SET caption_main_raw_text = ?,
                           caption  = ?,
                           hashtags = ?
                     WHERE id = ?
                    """,
                    (
                        main_text,
                        (payload.get("caption") or "")[:500],
                        json.dumps(payload.get("hashtags") or [], ensure_ascii=False),
                        video_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE tk_videos SET caption_main_raw_text = ? WHERE id = ?",
                    (main_text, video_id),
                )
            conn.commit()

        # Also persist the markdown raw to disk for human inspection.
        with _open_db(self.db_path) as conn:
            pack_uid = conn.execute(
                """
                SELECT p.pack_uid FROM packs p
                  JOIN tk_videos v ON v.pack_id = p.id
                 WHERE v.id = ?
                """, (video_id,),
            ).fetchone()[0]
        try:
            self.store.write_caption_main_raw(pack_uid, video_id, main_text)
        except Exception as e:
            logger.warning("write_caption_main_raw failed: %s", e)

        return {
            "video_id": video_id,
            "extract_ok": extract_ok,
            "extract_error": extract_error,
            "main_chars": len(main_text),
            "caption": payload.get("caption", ""),
            "hashtags": payload.get("hashtags", []),
        }

    # ------------------------------------------------------------------
    # Schedule / dispatch (B.1 stub — wires Blotato in W3.2)
    # ------------------------------------------------------------------

    def schedule_video(self, video_id: int, scheduled_at: int) -> bool:
        """Set scheduled_at + status='scheduled'. Pass 0 to clear."""
        with _open_db(self.db_path) as conn:
            if scheduled_at <= 0:
                cur = conn.execute(
                    """
                    UPDATE tk_videos
                       SET scheduled_at = NULL, publish_status = 'pending'
                     WHERE id = ?
                    """,
                    (video_id,),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE tk_videos
                       SET scheduled_at = ?, publish_status = 'scheduled',
                           publish_error = ''
                     WHERE id = ?
                    """,
                    (scheduled_at, video_id),
                )
            conn.commit()
            return cur.rowcount > 0

    def update_one_liner(self, video_id: int, text: str) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE tk_videos SET video_one_liner = ? WHERE id = ?",
                (text[:500], video_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def update_account(self, video_id: int, account_open_id: str) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE tk_videos SET account_open_id = ? WHERE id = ?",
                (account_open_id, video_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def update_caption_manual(self, video_id: int, caption: str,
                              hashtags: list[str]) -> bool:
        """Operator-edited override of the AI-generated caption/hashtags."""
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                """
                UPDATE tk_videos
                   SET caption = ?, hashtags = ?
                 WHERE id = ?
                """,
                (caption[:500], json.dumps(hashtags, ensure_ascii=False), video_id),
            )
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_videos(
        self,
        *,
        pack_id: Optional[int] = None,
        publish_status: Optional[str] = None,
        limit: int = 100,
    ) -> tuple[list[dict], int]:
        clauses, params = [], []
        if pack_id is not None:
            clauses.append("v.pack_id = ?")
            params.append(pack_id)
        if publish_status and publish_status != "all":
            clauses.append("v.publish_status = ?")
            params.append(publish_status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _open_db(self.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM tk_videos v {where}", tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT v.id, v.pack_id, v.account_open_id, v.local_video_path,
                       v.video_one_liner, v.caption, v.hashtags,
                       v.scheduled_at, v.blotato_post_id, v.publish_status,
                       v.published_at, v.publish_error,
                       p.display_name AS pack_name, p.cover_image_path AS pack_cover
                  FROM tk_videos v
             LEFT JOIN packs     p ON p.id = v.pack_id
                  {where}
                 ORDER BY v.id DESC
                 LIMIT ?
                """,
                tuple(params) + (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["hashtags"] = json.loads(d.get("hashtags") or "[]")
            except Exception:
                d["hashtags"] = []
            out.append(d)
        return out, total

    def get_video(self, video_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                """
                SELECT v.id, v.pack_id, v.account_open_id, v.local_video_path,
                       v.video_one_liner, v.caption_main_raw_text,
                       v.caption, v.hashtags, v.scheduled_at,
                       v.blotato_post_id, v.publish_status,
                       v.published_at, v.publish_error,
                       p.display_name AS pack_name, p.cover_image_path,
                       p.pack_uid
                  FROM tk_videos v
             LEFT JOIN packs     p ON p.id = v.pack_id
                 WHERE v.id = ?
                """,
                (video_id,),
            ).fetchone()
            if not r:
                return None
            d = dict(r)
            try:
                d["hashtags"] = json.loads(d.get("hashtags") or "[]")
            except Exception:
                d["hashtags"] = []
            metrics = conn.execute(
                """
                SELECT view_count, like_count, comment_count, share_count, fetched_at
                  FROM tk_video_metrics
                 WHERE video_id = ?
                 ORDER BY fetched_at DESC LIMIT 10
                """,
                (video_id,),
            ).fetchall()
            d["metrics_history"] = [dict(m) for m in metrics]
            d["latest_metrics"] = dict(metrics[0]) if metrics else None
        return d


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[TKVideoService] = None


def get_tk_video_service() -> TKVideoService:
    global _svc
    if _svc is None:
        _svc = TKVideoService()
    return _svc
