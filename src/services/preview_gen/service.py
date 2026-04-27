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
from src.services.preview_gen.prompts import build_preview_prompt
from src.services.storage.pack_store import PackStore, get_pack_store, make_pack_uid

logger = get_logger("service.preview_gen")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

# W1.8 POC observed ~1/3 transient APIConnectionError on image_edit;
# image_generate was more reliable but we still want bounded concurrency
# (each call holds a 1024x1024 b64 in memory, ~3MB).
DEFAULT_MAX_WORKERS = 3
DEFAULT_RETRY_ON_ERROR = 1
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
                 WHERE series_id = ? AND generation_status IN ('pending', 'error')
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
                       s.plan_id
                  FROM pack_previews pp
                  JOIN pack_series   s ON s.id = pp.series_id
                 WHERE pp.id = ?
                """,
                (preview_id,),
            ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[PreviewGenService] = None


def get_preview_gen_service() -> PreviewGenService:
    global _svc
    if _svc is None:
        _svc = PreviewGenService()
    return _svc
