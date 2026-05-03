"""Preview generation service — A.3 of the V2 pipeline.

Lifecycle:
  1. ``prepare_previews(series_id)`` — for each preview_brief in the series's
     metadata_json, mint a ``pack_uid`` (if missing) and INSERT a
     pack_previews row with generation_status='pending' + the assembled
     prompt_text. Idempotent: only creates rows for missing preview_idx values.

  2. ``generate_pending_for_series(series_id)`` — fans out parallel image_generate
     calls (capped at 3 workers per W1.8 POC notes — saw 1/3 connection errors,
     so per-call retry x1). Writes PNG bytes via PackStore, updates row
     status to 'ok' / 'error'.

  3. ``regenerate_preview(preview_id)`` — single retry, same pipeline.

The pack_uid is per-series (one series → one future pack), set on first
``prepare_previews`` call so files land at::

    output/packs/{pack_uid}/series_{series_idx}/previews/{preview_idx}.png
    output/packs/{pack_uid}/series_{series_idx}/previews/{preview_idx}.prompt.txt
"""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import AIRouter, get_router
from src.services.preview_gen.prompts import build_preview_prompt, build_split_prompt
from src.services.storage.pack_store import PackStore, get_pack_store, make_pack_uid

logger = get_logger("service.preview_gen")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

# JieKou /v3/gpt-image-2-edit drops connections under concurrency >= 3
# (verified during W3.3 smoke: 2/5 splits failed with RemoteProtocolError
# at workers=3). image_generate (text-to-image) is more reliable; image
# edit (image-to-image) is the flaky one. Cap workers at 2 globally and
# raise per-call retries to 3.
DEFAULT_MAX_WORKERS = 2
DEFAULT_RETRY_ON_ERROR = 3
DEFAULT_IMAGE_SIZE = "1024x1024"


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class PreviewGenService:
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
    # Read helpers
    # ------------------------------------------------------------------

    def _get_series_row(self, conn: sqlite3.Connection, series_id: int) -> Optional[sqlite3.Row]:
        return conn.execute(
            """
            SELECT s.id, s.plan_id, s.series_idx, s.series_name, s.style_anchor,
                   s.palette, s.metadata_json, s.is_selected, s.pack_uid,
                   t.topic_name
              FROM pack_series s
              JOIN topic_plans p ON p.id = s.plan_id
              JOIN hot_topics  t ON t.id = p.topic_id
             WHERE s.id = ?
            """,
            (series_id,),
        ).fetchone()

    @staticmethod
    def _parse_briefs(metadata_json: str) -> list[dict]:
        try:
            md = json.loads(metadata_json or "{}")
        except Exception:
            return []
        return md.get("preview_briefs") or []

    # ------------------------------------------------------------------
    # Phase 1: prepare (mint pack_uid + insert pending rows)
    # ------------------------------------------------------------------

    def ensure_pack_uid(self, series_id: int) -> str:
        """Mint a pack_uid for this series if it doesn't already have one."""
        with _open_db(self.db_path) as conn:
            row = self._get_series_row(conn, series_id)
            if not row:
                raise ValueError(f"pack_series #{series_id} not found")
            if row["pack_uid"]:
                return row["pack_uid"]
            uid = make_pack_uid(row["topic_name"] or row["series_name"] or "series")
            conn.execute("UPDATE pack_series SET pack_uid = ? WHERE id = ?",
                         (uid, series_id))
            conn.commit()
            self.store.init_pack_dir(uid)
            self.store.write_style_anchor(uid, row["series_idx"], row["style_anchor"] or "")
            return uid

    def prepare_previews(self, series_id: int) -> dict[str, Any]:
        """Create pending pack_previews rows from the series's preview_briefs.

        Idempotent — only inserts rows for preview_idx values that don't
        already exist. Returns ``{'pack_uid': str, 'created': int, 'existing': int}``.
        """
        uid = self.ensure_pack_uid(series_id)

        with _open_db(self.db_path) as conn:
            row = self._get_series_row(conn, series_id)
            briefs = self._parse_briefs(row["metadata_json"])
            if not briefs:
                return {"pack_uid": uid, "created": 0, "existing": 0,
                        "skipped_reason": "no preview_briefs in metadata"}

            existing_idx = {
                r[0] for r in conn.execute(
                    "SELECT preview_idx FROM pack_previews WHERE series_id = ?",
                    (series_id,),
                ).fetchall()
            }

            created = 0
            for b in briefs:
                preview_idx = int(b.get("preview_idx") or 0)
                if preview_idx <= 0 or preview_idx in existing_idx:
                    continue
                prompt_text = build_preview_prompt(
                    style_anchor=row["style_anchor"] or "",
                    palette=row["palette"] or "",
                    preview_theme=str(b.get("theme") or ""),
                    stickers=list(b.get("stickers") or []),
                )
                conn.execute(
                    """
                    INSERT INTO pack_previews
                        (series_id, preview_idx, prompt_text, image_path,
                         model_used, generation_status, generated_at)
                    VALUES (?, ?, ?, '', '', 'pending', NULL)
                    """,
                    (series_id, preview_idx, prompt_text),
                )
                created += 1
            conn.commit()
        return {"pack_uid": uid, "created": created,
                "existing": len(existing_idx), "skipped_reason": ""}

    # ------------------------------------------------------------------
    # Phase 2: generate (call image_generate per pending row)
    # ------------------------------------------------------------------

    def generate_pending_for_series(
        self,
        series_id: int,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        size: str = DEFAULT_IMAGE_SIZE,
    ) -> dict[str, Any]:
        """Fan out image_generate calls for every pending preview in this series.

        Uses ThreadPoolExecutor with ``max_workers``. Each call does up to
        ``DEFAULT_RETRY_ON_ERROR + 1`` attempts before giving up. Returns
        ``{'attempted': int, 'ok': int, 'error': int, 'errors': [...]}``.
        """
        with _open_db(self.db_path) as conn:
            pending = conn.execute(
                """
                SELECT id FROM pack_previews
                 WHERE series_id = ?
                   AND (
                        generation_status IN ('pending', 'error')
                        OR (
                            generation_status = 'generating'
                            AND COALESCE(image_path, '') = ''
                        )
                   )
                 ORDER BY preview_idx
                """,
                (series_id,),
            ).fetchall()
        ids = [r[0] for r in pending]
        if not ids:
            return {"attempted": 0, "ok": 0, "error": 0, "errors": []}

        ok_count, err_count, errors = 0, 0, []
        # Cap workers at the number of pending tasks.
        workers = max(1, min(max_workers, len(ids)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(self._generate_one_with_retry, pid, size): pid
                       for pid in ids}
            for fut in as_completed(futures):
                pid = futures[fut]
                try:
                    result = fut.result()
                    if result["status"] == "ok":
                        ok_count += 1
                    else:
                        err_count += 1
                        errors.append({"preview_id": pid, "error": result["error"][:200]})
                except Exception as e:
                    err_count += 1
                    errors.append({"preview_id": pid, "error": str(e)[:200]})

        return {"attempted": len(ids), "ok": ok_count, "error": err_count,
                "errors": errors}

    def regenerate_preview(self, preview_id: int, *, size: str = DEFAULT_IMAGE_SIZE) -> dict[str, Any]:
        """Force re-run a single preview. Resets status to 'pending' first."""
        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE pack_previews SET generation_status = 'pending' WHERE id = ?",
                (preview_id,),
            )
            conn.commit()
        return self._generate_one_with_retry(preview_id, size)

    def regenerate_preview_with_prompt(
        self,
        preview_id: int,
        *,
        prompt_text: str = "",
        feedback_text: str = "",
        use_ai: bool = False,
        size: str = DEFAULT_IMAGE_SIZE,
    ) -> dict[str, Any]:
        """Update a preview prompt, optionally AI-refine it from feedback, then
        regenerate the preview image in-place.

        ``write_preview`` uses the same preview filename, so a successful run
        overwrites the old image on disk while preserving the DB row.
        """
        prompt_text = (prompt_text or "").strip()
        feedback_text = (feedback_text or "").strip()
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pp.id, pp.prompt_text, pp.series_id, pp.preview_idx,
                       s.style_anchor, s.palette, s.series_name
                  FROM pack_previews pp
                  JOIN pack_series   s ON s.id = pp.series_id
                 WHERE pp.id = ?
                """,
                (preview_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"preview #{preview_id} not found")

        base_prompt = prompt_text or row["prompt_text"] or ""
        if use_ai and feedback_text:
            ai_prompt = (
                "You are improving an image-generation prompt for a sticker-pack "
                "preview image. Keep the same overall sticker-pack production format, "
                "but incorporate the user's feedback. Return ONLY the final prompt, "
                "no markdown, no commentary.\n\n"
                f"Series name: {row['series_name'] or ''}\n"
                f"Style anchor:\n{row['style_anchor'] or ''}\n\n"
                f"Palette:\n{row['palette'] or ''}\n\n"
                f"Current prompt:\n{base_prompt}\n\n"
                f"User feedback / desired change:\n{feedback_text}\n"
            )
            improved = self.router.text_complete(
                ai_prompt,
                system="Rewrite image prompts precisely and concisely.",
                temperature=0.45,
                max_tokens=6000,
                task="preview_prompt:revise",
                related_table="pack_previews",
                related_id=preview_id,
            ).strip()
            if improved:
                base_prompt = improved.strip().strip("`")

        if not base_prompt:
            raise ValueError("prompt_text cannot be empty")

        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE pack_previews
                   SET prompt_text = ?, generation_status = 'pending'
                 WHERE id = ?
                """,
                (base_prompt, preview_id),
            )
            conn.commit()
        return self._generate_one_with_retry(preview_id, size)

    # ------------------------------------------------------------------
    # Inner worker: per-preview generate + write file + DB update
    # ------------------------------------------------------------------

    def _generate_one_with_retry(self, preview_id: int, size: str) -> dict[str, Any]:
        """Run image_generate for one row with bounded retries.

        Marks the row as 'generating' on entry so concurrent UI sees activity,
        then 'ok' or 'error' on exit.
        """
        # Mark generating
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pp.id, pp.preview_idx, pp.prompt_text, pp.series_id,
                       s.series_idx, s.pack_uid
                  FROM pack_previews pp
                  JOIN pack_series   s ON s.id = pp.series_id
                 WHERE pp.id = ?
                """,
                (preview_id,),
            ).fetchone()
            if not row:
                return {"status": "error", "error": f"preview #{preview_id} not found"}
            if not row["pack_uid"]:
                return {"status": "error", "error": "series has no pack_uid (call ensure_pack_uid first)"}
            if not row["prompt_text"]:
                return {"status": "error", "error": "prompt_text is empty"}
            conn.execute(
                "UPDATE pack_previews SET generation_status = 'generating' WHERE id = ?",
                (preview_id,),
            )
            conn.commit()

        last_err = ""
        for attempt in range(DEFAULT_RETRY_ON_ERROR + 1):
            try:
                t0 = time.time()
                images = self.router.image_generate(
                    row["prompt_text"],
                    n=1,
                    size=size,
                    task=f"preview_gen{':retry' + str(attempt) if attempt else ''}",
                    related_table="pack_previews",
                    related_id=preview_id,
                )
                if not images:
                    raise RuntimeError("image_generate returned no images")
                image_bytes = images[0]
                img_path = self.store.write_preview(
                    pack_uid=row["pack_uid"],
                    series_idx=row["series_idx"],
                    preview_idx=row["preview_idx"],
                    image_bytes=image_bytes,
                    prompt_text=row["prompt_text"],
                )
                # Path stored relative to project root for portability of the URL mount.
                rel = img_path.as_posix()
                with _open_db(self.db_path) as conn:
                    conn.execute(
                        """
                        UPDATE pack_previews
                           SET image_path = ?, model_used = ?,
                               generation_status = 'ok', generated_at = ?
                         WHERE id = ?
                        """,
                        (rel, self.router.DEFAULT_IMAGE_MODEL, int(time.time()), preview_id),
                    )
                    conn.commit()
                logger.info("preview #%d generated in %.1fs → %s",
                            preview_id, time.time() - t0, rel)
                return {"status": "ok", "preview_id": preview_id, "image_path": rel}
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                logger.warning("preview #%d attempt %d/%d failed: %s",
                               preview_id, attempt + 1,
                               DEFAULT_RETRY_ON_ERROR + 1, last_err)
                if attempt < DEFAULT_RETRY_ON_ERROR:
                    time.sleep(2 ** attempt)

        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE pack_previews SET generation_status = 'error' WHERE id = ?",
                (preview_id,),
            )
            conn.commit()
        return {"status": "error", "preview_id": preview_id, "error": last_err}

    # ------------------------------------------------------------------
    # Split phase — image_edit per sticker
    # ------------------------------------------------------------------

    def prepare_stickers(self, preview_id: int) -> dict[str, Any]:
        """Create pending pack_stickers rows from this preview's brief.

        The brief is looked up from the parent series's metadata_json
        (preview_briefs[preview_idx]). Idempotent — only creates rows
        for missing sticker_idx values.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pp.id, pp.preview_idx, pp.image_path,
                       pp.generation_status,
                       s.id AS series_id, s.style_anchor, s.metadata_json
                  FROM pack_previews pp
                  JOIN pack_series   s ON s.id = pp.series_id
                 WHERE pp.id = ?
                """,
                (preview_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"preview #{preview_id} not found")
            if row["generation_status"] != "ok" or not row["image_path"]:
                return {"created": 0, "existing": 0,
                        "skipped_reason": "preview not generated yet"}

            briefs = self._parse_briefs(row["metadata_json"])
            brief = next(
                (b for b in briefs if int(b.get("preview_idx") or 0) == row["preview_idx"]),
                None,
            )
            if not brief or not brief.get("stickers"):
                return {"created": 0, "existing": 0,
                        "skipped_reason": "no sticker brief for this preview_idx"}

            stickers = list(brief.get("stickers") or [])
            existing_idx = {
                r[0] for r in conn.execute(
                    "SELECT sticker_idx FROM pack_stickers WHERE preview_id = ?",
                    (preview_id,),
                ).fetchall()
            }
            created = 0
            for idx, brief_text in enumerate(stickers, 1):
                if idx in existing_idx:
                    continue
                prompt = build_split_prompt(
                    sticker_brief=brief_text,
                    sticker_idx=idx,
                    total_stickers=len(stickers),
                    style_anchor=row["style_anchor"] or "",
                )
                conn.execute(
                    """
                    INSERT INTO pack_stickers
                        (preview_id, sticker_idx, name, description,
                         image_path, prompt_text, model_used,
                         generation_status, generated_at, is_selected)
                    VALUES (?, ?, ?, ?, '', ?, '', 'pending', NULL, 1)
                    """,
                    (preview_id, idx,
                     (brief_text.split("/", 1)[0]).strip()[:200],
                     brief_text[:500], prompt),
                )
                created += 1
            conn.commit()
        return {"created": created, "existing": len(existing_idx),
                "total": len(stickers), "skipped_reason": ""}

    def split_pending_for_preview(
        self,
        preview_id: int,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        size: str = DEFAULT_IMAGE_SIZE,
    ) -> dict[str, Any]:
        """Run image_edit for every pending sticker under this preview."""
        with _open_db(self.db_path) as conn:
            ids = [r[0] for r in conn.execute(
                """
                SELECT id FROM pack_stickers
                 WHERE preview_id = ? AND generation_status IN ('pending', 'error')
                 ORDER BY sticker_idx
                """,
                (preview_id,),
            ).fetchall()]
        if not ids:
            return {"attempted": 0, "ok": 0, "error": 0, "errors": []}

        ok_n, err_n, errors = 0, 0, []
        workers = max(1, min(max_workers, len(ids)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(self._split_one_with_retry, sid, size): sid
                       for sid in ids}
            for fut in as_completed(futures):
                sid = futures[fut]
                try:
                    r = fut.result()
                    if r["status"] == "ok":
                        ok_n += 1
                    else:
                        err_n += 1
                        errors.append({"sticker_id": sid, "error": r["error"][:200]})
                except Exception as e:
                    err_n += 1
                    errors.append({"sticker_id": sid, "error": str(e)[:200]})

        return {"attempted": len(ids), "ok": ok_n, "error": err_n,
                "errors": errors}

    def regenerate_sticker(self, sticker_id: int, *, size: str = DEFAULT_IMAGE_SIZE) -> dict[str, Any]:
        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE pack_stickers SET generation_status = 'pending' WHERE id = ?",
                (sticker_id,),
            )
            conn.commit()
        return self._split_one_with_retry(sticker_id, size)

    def _split_one_with_retry(self, sticker_id: int, size: str) -> dict[str, Any]:
        """image_edit one sticker. Marks status, writes file, updates row.

        Loads the parent preview's PNG bytes from disk and feeds them to
        AIRouter.image_edit alongside the sticker's prompt_text.
        """
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT ps.id, ps.sticker_idx, ps.prompt_text, ps.preview_id,
                       pp.image_path AS preview_image_path,
                       s.series_idx, s.pack_uid
                  FROM pack_stickers ps
                  JOIN pack_previews pp ON pp.id = ps.preview_id
                  JOIN pack_series   s  ON s.id  = pp.series_id
                 WHERE ps.id = ?
                """,
                (sticker_id,),
            ).fetchone()
            if not row:
                return {"status": "error", "error": f"sticker #{sticker_id} not found"}
            if not row["preview_image_path"]:
                return {"status": "error", "error": "parent preview has no image yet"}
            preview_path = Path(row["preview_image_path"])
            if not preview_path.is_file():
                return {"status": "error", "error": f"preview file missing: {preview_path}"}
            conn.execute(
                "UPDATE pack_stickers SET generation_status = 'generating' WHERE id = ?",
                (sticker_id,),
            )
            conn.commit()

        source_bytes = preview_path.read_bytes()
        last_err = ""
        for attempt in range(DEFAULT_RETRY_ON_ERROR + 1):
            try:
                t0 = time.time()
                image_bytes = self.router.image_edit(
                    source_bytes,
                    row["prompt_text"],
                    size=size,
                    task=f"sticker_split{':retry' + str(attempt) if attempt else ''}",
                    related_table="pack_stickers",
                    related_id=sticker_id,
                )
                # Sticker filename is unique within the series, so we use
                # an idx that combines preview + sticker so multiple previews
                # in the same series don't collide.
                global_idx = row["preview_id"] * 1000 + row["sticker_idx"]
                img_path = self.store.write_sticker(
                    pack_uid=row["pack_uid"],
                    series_idx=row["series_idx"],
                    sticker_idx=global_idx,
                    image_bytes=image_bytes,
                    meta={
                        "preview_id": row["preview_id"],
                        "sticker_idx": row["sticker_idx"],
                        "prompt": row["prompt_text"][:500],
                    },
                )
                rel = img_path.as_posix()
                with _open_db(self.db_path) as conn:
                    conn.execute(
                        """
                        UPDATE pack_stickers
                           SET image_path = ?, model_used = ?,
                               generation_status = 'ok', generated_at = ?
                         WHERE id = ?
                        """,
                        (rel, self.router.DEFAULT_IMAGE_MODEL,
                         int(time.time()), sticker_id),
                    )
                    conn.commit()
                logger.info("sticker #%d split in %.1fs → %s",
                            sticker_id, time.time() - t0, rel)
                return {"status": "ok", "sticker_id": sticker_id, "image_path": rel}
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                logger.warning("sticker #%d attempt %d/%d failed: %s",
                               sticker_id, attempt + 1,
                               DEFAULT_RETRY_ON_ERROR + 1, last_err)
                if attempt < DEFAULT_RETRY_ON_ERROR:
                    time.sleep(2 ** attempt)

        with _open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE pack_stickers SET generation_status = 'error' WHERE id = ?",
                (sticker_id,),
            )
            conn.commit()
        return {"status": "error", "sticker_id": sticker_id, "error": last_err}

    # ------------------------------------------------------------------
    # Read APIs for UI
    # ------------------------------------------------------------------

    def get_series_with_previews(self, series_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            row = self._get_series_row(conn, series_id)
            if not row:
                return None
            previews = conn.execute(
                """
                SELECT id, preview_idx, prompt_text, image_path, model_used,
                       generation_status, generated_at
                  FROM pack_previews
                 WHERE series_id = ?
                 ORDER BY preview_idx
                """,
                (series_id,),
            ).fetchall()
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.get("metadata_json") or "{}")
        except Exception:
            d["metadata"] = {}
        d["previews"] = [dict(p) for p in previews]
        return d

    def get_preview(self, preview_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT pp.id, pp.series_id, pp.preview_idx, pp.prompt_text,
                       pp.image_path, pp.model_used, pp.generation_status,
                       pp.generated_at,
                       s.series_idx, s.series_name, s.pack_uid, s.style_anchor,
                       s.plan_id, s.metadata_json
                  FROM pack_previews pp
                  JOIN pack_series   s ON s.id = pp.series_id
                 WHERE pp.id = ?
                """,
                (preview_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            stickers = conn.execute(
                """
                SELECT id, sticker_idx, name, description, image_path,
                       prompt_text, model_used, generation_status, generated_at,
                       is_selected
                  FROM pack_stickers
                 WHERE preview_id = ?
                 ORDER BY sticker_idx
                """,
                (preview_id,),
            ).fetchall()
        d["stickers"] = [dict(s) for s in stickers]
        # Counts for status grid
        ok_n = sum(1 for s in d["stickers"] if s["generation_status"] == "ok")
        err_n = sum(1 for s in d["stickers"] if s["generation_status"] == "error")
        gen_n = sum(1 for s in d["stickers"] if s["generation_status"] == "generating")
        # Expected count from briefs
        try:
            briefs = (json.loads(d.get("metadata_json") or "{}").get("preview_briefs") or [])
            brief = next((b for b in briefs
                          if int(b.get("preview_idx") or 0) == d["preview_idx"]), None)
            expected = len(brief.get("stickers") or []) if brief else 0
        except Exception:
            expected = 0
        d["sticker_summary"] = {
            "ok": ok_n, "error": err_n, "generating": gen_n,
            "total": len(d["stickers"]), "expected": expected,
        }
        return d

    def get_sticker(self, sticker_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT ps.id, ps.preview_id, ps.sticker_idx, ps.name,
                       ps.description, ps.image_path, ps.prompt_text,
                       ps.model_used, ps.generation_status, ps.generated_at,
                       ps.is_selected,
                       pp.preview_idx, pp.image_path AS preview_image_path,
                       s.id AS series_id, s.series_idx, s.series_name,
                       s.pack_uid, s.plan_id
                  FROM pack_stickers   ps
                  JOIN pack_previews   pp ON pp.id = ps.preview_id
                  JOIN pack_series     s  ON s.id  = pp.series_id
                 WHERE ps.id = ?
                """,
                (sticker_id,),
            ).fetchone()
        return dict(row) if row else None

    def toggle_sticker_selection(self, sticker_id: int, is_selected: bool) -> bool:
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE pack_stickers SET is_selected = ? WHERE id = ?",
                (1 if is_selected else 0, sticker_id),
            )
            conn.commit()
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[PreviewGenService] = None


def get_preview_gen_service() -> PreviewGenService:
    global _svc
    if _svc is None:
        _svc = PreviewGenService()
    return _svc
