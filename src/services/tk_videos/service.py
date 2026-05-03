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

import requests

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
        blotato_account_id: str = "",
        video_one_liner: str = "",
        account_open_id: str = "",  # only for B.2 metrics linkage
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
                    (pack_id, account_open_id, blotato_account_id,
                     local_video_path, video_one_liner,
                     caption_main_raw_text, caption, hashtags,
                     scheduled_at, blotato_post_id, publish_status,
                     published_at, publish_error)
                VALUES (?, ?, ?, '', ?, '', '', '[]', NULL, '', 'pending', NULL, '')
                """,
                (pack_id, account_open_id, blotato_account_id,
                 video_one_liner[:500]),
            )
            conn.commit()
            return cur.lastrowid

    # ------------------------------------------------------------------
    # Blotato account selection (replaces operator-typed open_id)
    # ------------------------------------------------------------------

    # In-process TTL cache for Blotato account listing. Each /v2/packs
    # and /v2/videos page render used to do a synchronous HTTP call here
    # (~1.5–2s, occasionally 21s on timeout). Account lists barely
    # change, so a short TTL gives massively faster page loads while
    # still picking up edits within a minute.
    _blotato_cache: dict[str, Any] = {"at": 0.0, "value": None}
    _BLOTATO_CACHE_TTL = 60.0  # seconds

    @classmethod
    def list_blotato_accounts(cls, *, force_refresh: bool = False) -> dict[str, Any]:
        """List Blotato-side TikTok accounts (memoized for 60s).

        Returns ``{"accounts": [...], "ok": bool, "error": str}`` so the
        UI can distinguish three states:
          - ok=True, accounts=[...]   → render dropdown
          - ok=False, error="not_configured"  → BLOTATO_API_KEY missing
          - ok=False, error=<message> → transient API failure (retry)

        Pass ``force_refresh=True`` to bypass the cache (e.g., after the
        operator added/removed an account on Blotato).
        """
        now = time.time()
        cached = cls._blotato_cache.get("value")
        if (
            not force_refresh
            and cached is not None
            and (now - cls._blotato_cache.get("at", 0.0)) < cls._BLOTATO_CACHE_TTL
        ):
            return cached

        from src.services.tiktok.blotato_service import BlotaToService
        bsvc = BlotaToService()
        if not bsvc.is_configured():
            value: dict[str, Any] = {"accounts": [], "ok": False, "error": "not_configured"}
        else:
            # Use a tighter timeout for this UI-fronting call: a slow Blotato
            # shouldn't make every page render hang for 30s.
            prev_timeout = getattr(bsvc, "_timeout", 30)
            try:
                bsvc._timeout = 5
                accs = bsvc.get_tiktok_accounts()
                value = {"accounts": accs, "ok": True, "error": ""}
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                logger.warning("list_blotato_accounts failed: %s", err)
                # On error, return the previous value (if any) so transient
                # network blips don't blank out the dropdown for the user.
                if cached is not None and cached.get("ok"):
                    value = {**cached, "error": f"stale (cause: {err[:120]})"}
                else:
                    value = {"accounts": [], "ok": False, "error": err[:200]}
            finally:
                bsvc._timeout = prev_timeout

        cls._blotato_cache = {"at": now, "value": value}
        return value

    def update_blotato_account(self, video_id: int, account_id: str) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE tk_videos SET blotato_account_id = ? WHERE id = ?",
                (account_id.strip(), video_id),
            )
            conn.commit()
            return cur.rowcount > 0

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

    def refresh_dispatch_status(self) -> dict[str, Any]:
        """Poll Blotato for every dispatched post that doesn't have a
        ``tiktok_video_id`` yet, parse the response, and persist the ID
        once the post reaches a terminal state.

        Blotato's GET /posts/{id} returns whatever shape they currently
        emit; we look for a URL like ``https://www.tiktok.com/.../video/{id}``
        in any string field and extract the numeric id.
        """
        import re
        from src.services.tiktok.blotato_service import BlotaToService

        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, blotato_post_id
                  FROM tk_videos
                 WHERE publish_status = 'published'
                   AND blotato_post_id != ''
                   AND tiktok_video_id = ''
                """,
            ).fetchall()
        if not rows:
            return {"checked": 0, "matched": 0, "errors": []}

        bsvc = BlotaToService()
        if not bsvc.is_configured():
            return {"checked": 0, "matched": 0,
                    "errors": ["BLOTATO_API_KEY not set"]}

        url_re = re.compile(r"tiktok\.com/[^\s\"'<>]*?/video/(\d{8,25})", re.I)
        matched, errors = 0, []
        for r in rows:
            try:
                resp = bsvc.get_post_status(r["blotato_post_id"])
                tid = ""
                # Walk every string in the response looking for a TikTok video URL.
                stack = [resp]
                while stack:
                    cur = stack.pop()
                    if isinstance(cur, dict):
                        stack.extend(cur.values())
                    elif isinstance(cur, list):
                        stack.extend(cur)
                    elif isinstance(cur, str):
                        m = url_re.search(cur)
                        if m:
                            tid = m.group(1)
                            break
                if tid:
                    with _open_db(self.db_path) as conn:
                        conn.execute(
                            "UPDATE tk_videos SET tiktok_video_id = ? WHERE id = ?",
                            (tid, r["id"]),
                        )
                        conn.commit()
                    matched += 1
                    logger.info("video #%d → tiktok_video_id %s", r["id"], tid)
            except Exception as e:
                errors.append({"video_id": r["id"], "error": f"{type(e).__name__}: {e}"[:200]})
        return {"checked": len(rows), "matched": matched, "errors": errors}

    def dispatch_due(self) -> dict[str, Any]:
        """Send every tk_videos row with status='scheduled' and scheduled_at<=now.

        Wraps src.services.tiktok.blotato_service.BlotaToService.publish_tiktok_video.
        Returns a summary the scheduler can log into scheduled_jobs.
        """
        now = int(time.time())
        with _open_db(self.db_path) as conn:
            due = conn.execute(
                """
                SELECT id FROM tk_videos
                 WHERE publish_status = 'scheduled' AND scheduled_at <= ?
                 ORDER BY scheduled_at ASC LIMIT 20
                """,
                (now,),
            ).fetchall()
        ok_n, err_n, skipped, errors = 0, 0, 0, []
        for r in due:
            try:
                res = self.dispatch_video(r["id"])
                if res.get("ok"):
                    ok_n += 1
                elif res.get("skipped"):
                    skipped += 1
                else:
                    err_n += 1
                    errors.append({"video_id": r["id"], "error": res.get("error", "")[:200]})
            except Exception as e:
                err_n += 1
                errors.append({"video_id": r["id"], "error": str(e)[:200]})
        return {"due": len(due), "ok": ok_n, "error": err_n,
                "skipped": skipped, "errors": errors}

    def dispatch_video(self, video_id: int) -> dict[str, Any]:
        """Upload local file to Blotato + publish. Idempotent (no-op if already published)."""
        from datetime import datetime, timezone

        # Local imports so the scheduler / web don't pull blotato unless needed
        from src.services.tiktok.blotato_service import BlotaToService

        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT id, blotato_account_id, local_video_path, caption,
                       hashtags, scheduled_at, publish_status
                  FROM tk_videos WHERE id = ?
                """,
                (video_id,),
            ).fetchone()
            if not row:
                return {"ok": False, "error": f"video #{video_id} not found"}
            if row["publish_status"] in ("published", "dispatching"):
                return {"ok": False, "skipped": True,
                        "error": f"already in status {row['publish_status']}"}
            local_path = Path(row["local_video_path"] or "")
            if not local_path.is_file():
                self._set_status(video_id, "failed", error="local video file missing")
                return {"ok": False, "error": "local video file missing"}

            self._set_status(video_id, "dispatching")

        bsvc = BlotaToService()
        if not bsvc.is_configured():
            self._set_status(video_id, "failed", error="BLOTATO_API_KEY not set")
            return {"ok": False, "error": "BLOTATO_API_KEY not set"}

        try:
            # Step 1: presigned URL + PUT bytes
            presigned = bsvc.get_presigned_url(local_path.name)
            put_resp = requests.put(
                presigned["presignedUrl"],
                data=local_path.read_bytes(),
                headers={"Content-Type": "video/mp4"},
                timeout=300,
            )
            put_resp.raise_for_status()
            public_url = presigned["publicUrl"]

            # Step 2: assemble caption with hashtags
            try:
                tags = json.loads(row["hashtags"] or "[]")
            except Exception:
                tags = []
            text = (row["caption"] or "").strip()
            if tags:
                text = f"{text}\n\n{' '.join(tags)}".strip()

            account_id = (row["blotato_account_id"] or "").strip()
            if not account_id:
                accounts = bsvc.get_tiktok_accounts()
                if not accounts:
                    raise RuntimeError("no TikTok accounts on Blotato")
                account_id = str(accounts[0]["id"])
                logger.info("video #%d had no blotato_account_id; "
                            "falling back to first available (%s)",
                            video_id, account_id)

            # Blotato target schema (as of 2026-04) requires these
            # disclosure-related TikTok flags. Defaults: stickers we
            # design ourselves are not branded content / not promoting
            # someone else's brand / not AI-generated (operator may
            # want to flip ai_generated=True for AI-generated assets).
            target_options = {
                "privacyLevel":      "PUBLIC_TO_EVERYONE",
                "disabledComments":  False,
                "disabledDuet":      False,
                "disabledStitch":    False,
                "isBrandedContent":  False,
                "isYourBrand":       True,
                "isAiGenerated":     False,
            }
            scheduled_iso = None
            if row["scheduled_at"]:
                # Convert epoch → ISO 8601 UTC for Blotato API
                scheduled_iso = datetime.fromtimestamp(
                    row["scheduled_at"], tz=timezone.utc,
                ).isoformat().replace("+00:00", "Z")

            resp = bsvc.publish_tiktok_video(
                account_id=str(account_id), text=text,
                media_urls=[public_url],
                target_options=target_options,
                scheduled_time=scheduled_iso,
            )
            post_id = str(resp.get("postSubmissionId") or "")
            with _open_db(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE tk_videos
                       SET blotato_post_id = ?, publish_status = 'published',
                           published_at = ?, publish_error = ''
                     WHERE id = ?
                    """,
                    (post_id, int(time.time()), video_id),
                )
                conn.commit()
            logger.info("video #%d dispatched → blotato post %s", video_id, post_id)
            return {"ok": True, "blotato_post_id": post_id}
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.exception("video #%d dispatch failed", video_id)
            self._set_status(video_id, "failed", error=err)
            return {"ok": False, "error": err}

    def _set_status(self, video_id: int, status: str, *, error: str = "") -> None:
        with _open_db(self.db_path) as conn:
            if status == "failed":
                conn.execute(
                    "UPDATE tk_videos SET publish_status = ?, publish_error = ? WHERE id = ?",
                    (status, error[:500], video_id),
                )
            else:
                conn.execute(
                    "UPDATE tk_videos SET publish_status = ? WHERE id = ?",
                    (status, video_id),
                )
            conn.commit()

    def refresh_metrics_for_published(self) -> dict[str, Any]:
        """B.2 — append a tk_video_metrics row for every published video that
        has its tiktok_video_id resolved.

        Precise match: groups by account_open_id, paginates TikTok Display
        get_video_list, writes a metrics row only when the API result's id
        equals our stored tiktok_video_id. Videos still missing a
        tiktok_video_id are reported in ``pending_id`` so the operator
        knows to run refresh_dispatch_status first.
        """
        from src.services.tiktok.tiktok_display_service import TikTokDisplayService
        tsvc = TikTokDisplayService()
        if not tsvc.is_configured():
            return {"checked": 0, "appended": 0, "pending_id": 0,
                    "errors": ["TikTok client key/secret not set"]}

        with _open_db(self.db_path) as conn:
            videos = conn.execute(
                """
                SELECT v.id, v.account_open_id, v.tiktok_video_id
                  FROM tk_videos v
                 WHERE v.publish_status = 'published'
                   AND v.account_open_id != ''
                """,
            ).fetchall()
        if not videos:
            return {"checked": 0, "appended": 0, "pending_id": 0, "errors": []}

        pending_id = sum(1 for v in videos if not v["tiktok_video_id"])
        with_id = [v for v in videos if v["tiktok_video_id"]]

        by_account: dict[str, list[sqlite3.Row]] = {}
        for v in with_id:
            by_account.setdefault(v["account_open_id"], []).append(v)

        appended, errors = 0, []
        for open_id, vs in by_account.items():
            try:
                token = tsvc.get_valid_token(open_id)
                if not token:
                    errors.append({"open_id": open_id, "error": "no valid token"})
                    continue
                wanted_ids = {v["tiktok_video_id"]: v for v in vs}
                # Paginate up to a reasonable cap to find the wanted IDs.
                cursor = 0
                pages = 0
                found: dict[str, dict] = {}
                while pages < 5 and len(found) < len(wanted_ids):
                    page = tsvc.get_video_list(token["access_token"], max_count=20, cursor=cursor)
                    items = page.get("videos") or []
                    for item in items:
                        tid = str(item.get("id") or "")
                        if tid in wanted_ids:
                            found[tid] = item
                    cursor = page.get("cursor") or 0
                    if not page.get("has_more"):
                        break
                    pages += 1
                with _open_db(self.db_path) as conn:
                    for tid, item in found.items():
                        v = wanted_ids[tid]
                        conn.execute(
                            """
                            INSERT INTO tk_video_metrics
                                (video_id, view_count, like_count,
                                 comment_count, share_count, fetched_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                v["id"],
                                int(item.get("view_count") or 0),
                                int(item.get("like_count") or 0),
                                int(item.get("comment_count") or 0),
                                int(item.get("share_count") or 0),
                                int(time.time()),
                            ),
                        )
                        appended += 1
                    conn.commit()
                missing = set(wanted_ids) - set(found)
                if missing:
                    errors.append({
                        "open_id": open_id,
                        "error": f"missed in API: {sorted(missing)[:5]}",
                    })
            except Exception as e:
                errors.append({"open_id": open_id, "error": f"{type(e).__name__}: {e}"[:200]})
        return {"checked": len(videos), "appended": appended,
                "pending_id": pending_id, "errors": errors}

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
        q: Optional[str] = None,
        with_latest_metrics: bool = False,
        limit: int = 100,
    ) -> tuple[list[dict], int]:
        clauses, params = [], []
        if pack_id is not None:
            clauses.append("v.pack_id = ?")
            params.append(pack_id)
        if publish_status and publish_status != "all":
            clauses.append("v.publish_status = ?")
            params.append(publish_status)
        q_norm = (q or "").strip()
        if q_norm:
            like = f"%{q_norm}%"
            clauses.append(
                "(p.display_name LIKE ? OR v.caption LIKE ? OR v.hashtags LIKE ? "
                "OR v.video_one_liner LIKE ? OR v.blotato_post_id LIKE ? OR v.tiktok_video_id LIKE ?)"
            )
            params.extend([like, like, like, like, like, like])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _open_db(self.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM tk_videos v LEFT JOIN packs p ON p.id = v.pack_id {where}",
                tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT v.id, v.pack_id, v.account_open_id,
                       v.blotato_account_id, v.local_video_path,
                       v.video_one_liner, v.caption, v.hashtags,
                       v.scheduled_at, v.blotato_post_id, v.tiktok_video_id,
                       v.publish_status, v.published_at, v.publish_error,
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
                if with_latest_metrics:
                    m = conn.execute(
                        """
                        SELECT view_count, like_count, comment_count,
                               share_count, fetched_at
                          FROM tk_video_metrics
                         WHERE video_id = ?
                         ORDER BY fetched_at DESC LIMIT 1
                        """,
                        (d["id"],),
                    ).fetchone()
                    d["latest_metrics"] = dict(m) if m else None
                out.append(d)
        return out, total

    def get_video(self, video_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            r = conn.execute(
                """
                SELECT v.id, v.pack_id, v.account_open_id,
                       v.blotato_account_id, v.local_video_path,
                       v.video_one_liner, v.caption_main_raw_text,
                       v.caption, v.hashtags, v.scheduled_at,
                       v.blotato_post_id, v.tiktok_video_id,
                       v.publish_status, v.published_at, v.publish_error,
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
