"""Pack aggregate service — A.4 of the V2 pipeline.

The packs row is created on demand when the operator confirms a
generated pack_series is "good enough to ship". From that point on,
downstream stages (B.1 TK videos, C.1 TKShop products) reference the
pack_id and treat the pack as the unit of work.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.storage.pack_store import get_pack_store, make_pack_uid

logger = get_logger("service.packs")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

VALID_STATUSES = ("active", "archived")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _open_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class PackService:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Promote series → pack
    # ------------------------------------------------------------------

    def create_pack_from_series(
        self,
        series_id: int,
        *,
        display_name: Optional[str] = None,
        cover_sticker_id: Optional[int] = None,
        allow_pending_previews: bool = False,
    ) -> dict[str, Any]:
        """Mint a packs row from this series. Idempotent — if a pack already
        exists for this series_id, returns its info instead of duplicating.

        Validates that the series has at least one is_selected sticker with
        generation_status='ok'. ``cover_sticker_id`` defaults to the first
        ok+selected sticker by sticker_idx. ``display_name`` defaults to the
        series_name.
        """
        with _open_db(self.db_path) as conn:
            series = conn.execute(
                """
                SELECT id, plan_id, series_idx, series_name, pack_uid
                  FROM pack_series WHERE id = ?
                """,
                (series_id,),
            ).fetchone()
            if not series:
                raise ValueError(f"pack_series #{series_id} not found")
            if not series["pack_uid"]:
                raise ValueError("series has no pack_uid yet — generate previews first")

            existing = conn.execute(
                "SELECT * FROM packs WHERE series_id = ?", (series_id,),
            ).fetchone()
            if existing:
                return {"pack_id": existing["id"], "pack_uid": existing["pack_uid"],
                        "created": False, "reason": "pack already exists"}

            ok_stickers = conn.execute(
                """
                SELECT ps.id, ps.sticker_idx, ps.image_path
                  FROM pack_stickers ps
                  JOIN pack_previews pp ON pp.id = ps.preview_id
                 WHERE pp.series_id = ?
                   AND ps.generation_status = 'ok'
                   AND ps.is_selected = 1
                 ORDER BY pp.preview_idx, ps.sticker_idx
                """,
                (series_id,),
            ).fetchall()
            ok_previews = conn.execute(
                """
                SELECT id, preview_idx, image_path
                  FROM pack_previews
                 WHERE series_id = ?
                   AND generation_status = 'ok'
                 ORDER BY preview_idx
                """,
                (series_id,),
            ).fetchall()
            preview_rows = conn.execute(
                """
                SELECT id, preview_idx, prompt_text, image_path, generation_status, generated_at
                  FROM pack_previews
                 WHERE series_id = ?
                 ORDER BY preview_idx
                """,
                (series_id,),
            ).fetchall()
            if not ok_stickers and not ok_previews and not (
                allow_pending_previews and preview_rows
            ):
                raise ValueError(
                    "series has neither generated previews nor split stickers — "
                    "generate previews first")

            cover_path = ""
            if cover_sticker_id:
                hit = next((s for s in ok_stickers if s["id"] == cover_sticker_id), None)
                if not hit:
                    raise ValueError(f"sticker #{cover_sticker_id} not in this series's ok set")
                cover_path = hit["image_path"]
            elif ok_stickers:
                cover_path = ok_stickers[0]["image_path"]
            elif ok_previews:
                # Preview-only fallback — use the first generated preview as cover
                cover_path = ok_previews[0]["image_path"]
            else:
                # Background generation has been queued but no image is ready yet.
                # The UI will show status counts and use the first generated
                # preview/sticker as an effective cover once it exists.
                cover_path = ""

            # total_stickers = actual ok stickers if any have been split,
            # otherwise the *expected* count from the briefs (so the operator
            # sees the target number, not zero, until splitting runs).
            if ok_stickers:
                total = len(ok_stickers)
            else:
                meta_row = conn.execute(
                    "SELECT metadata_json FROM pack_series WHERE id = ?",
                    (series_id,),
                ).fetchone()
                expected = 0
                try:
                    md = json.loads(meta_row["metadata_json"] or "{}")
                    for b in md.get("preview_briefs", []):
                        expected += len(b.get("stickers") or [])
                except Exception:
                    pass
                total = expected

            now = int(time.time())
            cur = conn.execute(
                """
                INSERT INTO packs
                    (pack_uid, series_id, display_name, cover_image_path,
                     total_stickers, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    series["pack_uid"], series_id,
                    display_name or series["series_name"] or f"pack_{series_id}",
                    cover_path,
                    total,
                    now,
                ),
            )
            conn.commit()
            pack_id = cur.lastrowid
        logger.info(
            "pack #%d created for series #%d (target %d stickers, "
            "%d split / %d previews)",
            pack_id, series_id, total, len(ok_stickers), len(ok_previews),
        )
        return {"pack_id": pack_id, "pack_uid": series["pack_uid"],
                "created": True, "total_stickers": total,
                "preview_only": not ok_stickers}

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_packs(
        self,
        *,
        status: Optional[str] = None,
        query_substring: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        clauses, params = [], []
        if status and status != "all":
            clauses.append("p.status = ?")
            params.append(status)
        if query_substring:
            clauses.append(
                "(CAST(p.id AS TEXT) LIKE ? OR p.display_name LIKE ? OR p.pack_uid LIKE ? OR "
                "s.series_name LIKE ? OR t.topic_name LIKE ? OR p.status LIKE ?)"
            )
            like = f"%{query_substring}%"
            params.extend([like, like, like, like, like, like])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _open_db(self.db_path) as conn:
            total = conn.execute(
                f"""
                SELECT COUNT(*)
                  FROM packs       p
             LEFT JOIN pack_series s  ON s.id = p.series_id
             LEFT JOIN topic_plans tp ON tp.id = s.plan_id
             LEFT JOIN hot_topics  t  ON t.id = tp.topic_id
                  {where}
                """,
                tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT p.id, p.pack_uid, p.series_id, p.display_name,
                       p.cover_image_path, p.total_stickers, p.status,
                       COALESCE(
                         NULLIF(p.cover_image_path, ''),
                         (SELECT ps.image_path
                            FROM pack_stickers ps
                            JOIN pack_previews pp2 ON pp2.id = ps.preview_id
                           WHERE pp2.series_id = s.id
                             AND ps.generation_status = 'ok'
                             AND ps.is_selected = 1
                             AND ps.image_path <> ''
                           ORDER BY pp2.preview_idx, ps.sticker_idx
                           LIMIT 1),
                         (SELECT pp3.image_path
                            FROM pack_previews pp3
                           WHERE pp3.series_id = s.id
                             AND pp3.generation_status = 'ok'
                             AND pp3.image_path <> ''
                           ORDER BY pp3.preview_idx
                           LIMIT 1),
                         ''
                       ) AS effective_cover_image_path,
                       p.created_at,
                       s.series_idx, s.series_name, s.pack_archetype,
                       s.priority, s.plan_id,
                       t.topic_name,
                       (SELECT COUNT(*) FROM pack_stickers ps
                          JOIN pack_previews pp ON pp.id = ps.preview_id
                         WHERE pp.series_id = s.id
                           AND ps.generation_status = 'ok') AS sticker_ok_count,
                        (SELECT COUNT(*) FROM pack_previews pp
                          WHERE pp.series_id = s.id
                            AND pp.generation_status = 'ok') AS preview_ok_count
                        ,
                       (SELECT COUNT(*) FROM pack_previews pp
                          WHERE pp.series_id = s.id) AS preview_total_count,
                       (SELECT COUNT(*) FROM pack_previews pp
                          WHERE pp.series_id = s.id
                            AND pp.generation_status = 'generating') AS preview_generating_count,
                       (SELECT COUNT(*) FROM pack_previews pp
                          WHERE pp.series_id = s.id
                            AND pp.generation_status = 'pending') AS preview_pending_count,
                       (SELECT COUNT(*) FROM pack_previews pp
                          WHERE pp.series_id = s.id
                            AND pp.generation_status = 'error') AS preview_error_count,
                       (SELECT COUNT(*) FROM pack_stickers ps
                          JOIN pack_previews pp ON pp.id = ps.preview_id
                         WHERE pp.series_id = s.id) AS sticker_total_count,
                       (SELECT COUNT(*) FROM pack_stickers ps
                          JOIN pack_previews pp ON pp.id = ps.preview_id
                         WHERE pp.series_id = s.id
                           AND ps.generation_status = 'generating') AS sticker_generating_count,
                       (SELECT COUNT(*) FROM pack_stickers ps
                          JOIN pack_previews pp ON pp.id = ps.preview_id
                         WHERE pp.series_id = s.id
                           AND ps.generation_status = 'pending') AS sticker_pending_count,
                       (SELECT COUNT(*) FROM pack_stickers ps
                          JOIN pack_previews pp ON pp.id = ps.preview_id
                         WHERE pp.series_id = s.id
                           AND ps.generation_status = 'error') AS sticker_error_count
                       ,
                       (SELECT COUNT(*) FROM tk_videos v
                         WHERE v.pack_id = p.id) AS video_count,
                       (SELECT COUNT(*) FROM tkshop_products pr
                         WHERE pr.pack_id = p.id) AS product_count
                  FROM packs       p
             LEFT JOIN pack_series s ON s.id = p.series_id
             LEFT JOIN topic_plans tp ON tp.id = s.plan_id
             LEFT JOIN hot_topics  t ON t.id = tp.topic_id
                  {where}
                 ORDER BY p.id DESC
                 LIMIT ? OFFSET ?
                """,
                tuple(params) + (limit, offset),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["preview_only"] = (d.get("sticker_ok_count") or 0) == 0
            out.append(d)
        return out, total

    def list_pack_candidates(self, *, limit: int = 100) -> list[dict]:
        """Series that have not yet been promoted to a pack.

        Used by the V2 pack workbench's manual-add panel so operators no
        longer need to jump back to a series detail page just to click
        "保存为卡包".
        """
        with _open_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.plan_id, s.series_idx, s.series_name,
                       s.pack_uid, s.pack_archetype, s.priority,
                       t.topic_name,
                       (SELECT COUNT(*)
                          FROM pack_previews pp
                         WHERE pp.series_id = s.id
                           AND pp.generation_status = 'ok') AS preview_ok_count,
                       (SELECT COUNT(*)
                          FROM pack_previews pp
                         WHERE pp.series_id = s.id
                           AND pp.generation_status = 'generating') AS preview_generating_count,
                       (SELECT COUNT(*)
                          FROM pack_stickers ps
                          JOIN pack_previews pp ON pp.id = ps.preview_id
                         WHERE pp.series_id = s.id
                           AND ps.generation_status = 'ok'
                           AND ps.is_selected = 1) AS sticker_ok_count
                  FROM pack_series s
             LEFT JOIN packs       p  ON p.series_id = s.id
             LEFT JOIN topic_plans tp ON tp.id = s.plan_id
             LEFT JOIN hot_topics  t  ON t.id = tp.topic_id
                 WHERE p.id IS NULL
              ORDER BY preview_ok_count DESC,
                       sticker_ok_count DESC,
                       s.id DESC
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pack_with_downstream(self, pack_id: int) -> Optional[dict]:
        d = self.get_pack(pack_id)
        if not d:
            return None
        with _open_db(self.db_path) as conn:
            videos = conn.execute(
                """
                SELECT id, publish_status, scheduled_at, published_at, caption
                  FROM tk_videos WHERE pack_id = ? ORDER BY id DESC
                """,
                (pack_id,),
            ).fetchall()
            products = conn.execute(
                """
                SELECT id, publish_status, title, tiktok_product_id, created_at
                  FROM tkshop_products WHERE pack_id = ? ORDER BY id DESC
                """,
                (pack_id,),
            ).fetchall()
        d["videos"] = [dict(v) for v in videos]
        d["products"] = [dict(p) for p in products]
        return d

    def get_pack(self, pack_id: int) -> Optional[dict]:
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT p.id, p.pack_uid, p.series_id, p.display_name,
                       p.cover_image_path, p.total_stickers, p.status,
                       p.created_at,
                       s.series_idx, s.series_name, s.style_anchor,
                       s.palette, s.pack_archetype, s.priority,
                       s.metadata_json, s.plan_id,
                       tp.topic_id,
                       t.topic_name
                  FROM packs       p
             LEFT JOIN pack_series s  ON s.id  = p.series_id
             LEFT JOIN topic_plans tp ON tp.id = s.plan_id
             LEFT JOIN hot_topics  t  ON t.id  = tp.topic_id
                 WHERE p.id = ?
                """,
                (pack_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            stickers = conn.execute(
                """
                SELECT ps.id, ps.sticker_idx, ps.name, ps.description,
                       ps.image_path, ps.generation_status, ps.is_selected,
                       pp.preview_idx, pp.id AS preview_id
                  FROM pack_stickers ps
                  JOIN pack_previews pp ON pp.id = ps.preview_id
                 WHERE pp.series_id = ?
                 ORDER BY pp.preview_idx, ps.sticker_idx
                """,
                (d["series_id"],),
            ).fetchall()
            d["stickers"] = [dict(s) for s in stickers]
            previews = conn.execute(
                """
                SELECT id, preview_idx, prompt_text, image_path,
                       generation_status, generated_at
                  FROM pack_previews
                 WHERE series_id = ?
                 ORDER BY preview_idx
                """,
                (d["series_id"],),
            ).fetchall()
            d["previews"] = [dict(p) for p in previews]

        fallback_sticker = next(
            (
                s.get("image_path")
                for s in d["stickers"]
                if s.get("generation_status") == "ok"
                and int(s.get("is_selected", 1)) == 1
                and s.get("image_path")
            ),
            "",
        )
        fallback_preview = next(
            (
                p.get("image_path")
                for p in d["previews"]
                if p.get("generation_status") == "ok" and p.get("image_path")
            ),
            "",
        )
        d["effective_cover_image_path"] = (
            d.get("cover_image_path") or fallback_sticker or fallback_preview or ""
        )
        d["preview_summary"] = self._status_summary(
            d["previews"],
            expected=self._expected_preview_count(d.get("metadata_json") or "{}"),
        )
        d["sticker_summary"] = self._status_summary(
            d["stickers"],
            expected=self._expected_sticker_count(d.get("metadata_json") or "{}"),
        )
        return d

    @staticmethod
    def _status_summary(rows: list[dict], *, expected: int = 0) -> dict[str, int]:
        ok = sum(1 for r in rows if r.get("generation_status") == "ok")
        error = sum(1 for r in rows if r.get("generation_status") == "error")
        generating = sum(1 for r in rows if r.get("generation_status") == "generating")
        pending = sum(1 for r in rows if r.get("generation_status") == "pending")
        selected_ok = sum(
            1 for r in rows
            if r.get("generation_status") == "ok" and int(r.get("is_selected", 1)) == 1
        )
        return {
            "ok": ok,
            "selected_ok": selected_ok,
            "error": error,
            "generating": generating,
            "pending": pending,
            "total": len(rows),
            "expected": expected,
            "missing": max(0, expected - ok - error - generating - pending),
        }

    @staticmethod
    def _expected_preview_count(metadata_json: str) -> int:
        try:
            md = json.loads(metadata_json or "{}")
            return len(md.get("preview_briefs") or [])
        except Exception:
            return 0

    @staticmethod
    def _expected_sticker_count(metadata_json: str) -> int:
        try:
            md = json.loads(metadata_json or "{}")
            return sum(len(b.get("stickers") or []) for b in md.get("preview_briefs", []))
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Mutate
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_asset_stem(filename: str, fallback: str) -> str:
        stem = Path(filename or "").stem or fallback
        stem = _SAFE_NAME_RE.sub("_", stem).strip("._")
        return (stem[:80] or fallback)

    @staticmethod
    def _asset_suffix(filename: str) -> str:
        suffix = Path(filename or "").suffix.lower()
        return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp"} else ".png"

    @staticmethod
    def _write_bytes_atomic(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)

    def upload_manual_pack(
        self,
        series_id: int,
        *,
        display_name: str | None = None,
        sticker_assets: list[dict[str, Any]],
        cover_asset: dict[str, Any] | None = None,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """Attach operator-made sticker images to a series and create/update its pack.

        ``sticker_assets`` items are ``{"filename": str, "data": bytes}``.
        The method creates one synthetic ok preview row to group the uploaded
        stickers, then creates or updates the aggregate ``packs`` row so the
        normal pack management / video / product flows can continue.
        """
        sticker_assets = [a for a in (sticker_assets or []) if a.get("data")]
        if not sticker_assets:
            raise ValueError("at least one sticker image is required")

        now = int(time.time())
        store = get_pack_store()

        with _open_db(self.db_path) as conn:
            series = conn.execute(
                """
                SELECT s.id, s.series_idx, s.series_name, s.style_anchor,
                       s.pack_uid, s.metadata_json,
                       tp.topic_id, t.topic_name
                  FROM pack_series s
             LEFT JOIN topic_plans tp ON tp.id = s.plan_id
             LEFT JOIN hot_topics t ON t.id = tp.topic_id
                 WHERE s.id = ?
                """,
                (series_id,),
            ).fetchone()
            if not series:
                raise ValueError(f"pack_series #{series_id} not found")

            pack_uid = series["pack_uid"] or make_pack_uid(
                series["topic_name"] or series["series_name"] or f"series_{series_id}"
            )
            if not series["pack_uid"]:
                conn.execute(
                    "UPDATE pack_series SET pack_uid = ? WHERE id = ?",
                    (pack_uid, series_id),
                )
            store.init_pack_dir(pack_uid)
            store.write_style_anchor(
                pack_uid, int(series["series_idx"] or 1), series["style_anchor"] or "",
            )

            if replace_existing:
                conn.execute(
                    """
                    DELETE FROM pack_stickers
                     WHERE preview_id IN (
                       SELECT id FROM pack_previews WHERE series_id = ?
                     )
                    """,
                    (series_id,),
                )
                conn.execute("DELETE FROM pack_previews WHERE series_id = ?", (series_id,))

            next_preview_idx = (
                conn.execute(
                    "SELECT COALESCE(MAX(preview_idx), 0) + 1 FROM pack_previews WHERE series_id = ?",
                    (series_id,),
                ).fetchone()[0]
                or 1
            )
            next_sticker_idx = (
                conn.execute(
                    """
                    SELECT COALESCE(MAX(ps.sticker_idx), 0) + 1
                      FROM pack_stickers ps
                      JOIN pack_previews pp ON pp.id = ps.preview_id
                     WHERE pp.series_id = ?
                    """,
                    (series_id,),
                ).fetchone()[0]
                or 1
            )

            series_dir = store.series_dir(pack_uid, int(series["series_idx"] or 1))
            manual_dir = series_dir / "manual_uploads"
            sticker_dir = manual_dir / "stickers"
            preview_dir = manual_dir / "previews"
            sticker_dir.mkdir(parents=True, exist_ok=True)
            preview_dir.mkdir(parents=True, exist_ok=True)

            cover_path = ""
            if cover_asset and cover_asset.get("data"):
                suffix = self._asset_suffix(cover_asset.get("filename") or "cover.png")
                cover_path_obj = preview_dir / f"manual_preview_{next_preview_idx}{suffix}"
                self._write_bytes_atomic(cover_path_obj, cover_asset["data"])
                cover_path = str(cover_path_obj)

            saved_stickers: list[dict[str, Any]] = []
            for offset, asset in enumerate(sticker_assets):
                idx = int(next_sticker_idx) + offset
                filename = asset.get("filename") or f"sticker_{idx}.png"
                suffix = self._asset_suffix(filename)
                stem = self._safe_asset_stem(filename, f"sticker_{idx}")
                target = sticker_dir / f"{idx:03d}_{stem}{suffix}"
                self._write_bytes_atomic(target, asset["data"])
                saved_stickers.append(
                    {
                        "sticker_idx": idx,
                        "name": stem,
                        "image_path": str(target),
                        "filename": filename,
                    }
                )

            if not cover_path:
                cover_path = saved_stickers[0]["image_path"]

            cur_preview = conn.execute(
                """
                INSERT INTO pack_previews
                    (series_id, preview_idx, prompt_text, image_path,
                     model_used, generation_status, generated_at)
                VALUES (?, ?, ?, ?, 'manual_upload', 'ok', ?)
                """,
                (
                    series_id,
                    int(next_preview_idx),
                    "manual uploaded pack preview",
                    cover_path,
                    now,
                ),
            )
            preview_id = int(cur_preview.lastrowid)

            for st in saved_stickers:
                conn.execute(
                    """
                    INSERT INTO pack_stickers
                        (preview_id, sticker_idx, name, description, image_path,
                         is_selected, prompt_text, model_used, generation_status, generated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, 'manual_upload', 'ok', ?)
                    """,
                    (
                        preview_id,
                        int(st["sticker_idx"]),
                        st["name"],
                        f"Manual uploaded file: {st['filename']}",
                        st["image_path"],
                        "manual uploaded sticker",
                        now,
                    ),
                )

            brief = {
                "preview_idx": int(next_preview_idx),
                "theme": "手动上传卡包",
                "stickers": [st["name"] for st in saved_stickers],
                "manual_upload": True,
            }
            try:
                metadata = json.loads(series["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            old_briefs = [] if replace_existing else list(metadata.get("preview_briefs") or [])
            metadata["preview_briefs"] = old_briefs + [brief]
            metadata["manual_pack_uploaded"] = True
            metadata["manual_pack_uploaded_at"] = now
            conn.execute(
                "UPDATE pack_series SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), series_id),
            )

            total = conn.execute(
                """
                SELECT COUNT(*)
                  FROM pack_stickers ps
                  JOIN pack_previews pp ON pp.id = ps.preview_id
                 WHERE pp.series_id = ?
                   AND ps.generation_status = 'ok'
                   AND ps.is_selected = 1
                """,
                (series_id,),
            ).fetchone()[0]

            existing = conn.execute(
                "SELECT id FROM packs WHERE series_id = ?",
                (series_id,),
            ).fetchone()
            final_name = (display_name or "").strip()[:200] or series["series_name"] or f"pack_{series_id}"
            if existing:
                pack_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE packs
                       SET pack_uid = ?,
                           display_name = ?,
                           cover_image_path = ?,
                           total_stickers = ?,
                           status = 'active'
                     WHERE id = ?
                    """,
                    (pack_uid, final_name, cover_path, int(total), pack_id),
                )
                created = False
            else:
                cur_pack = conn.execute(
                    """
                    INSERT INTO packs
                        (pack_uid, series_id, display_name, cover_image_path,
                         total_stickers, status, created_at)
                    VALUES (?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (pack_uid, series_id, final_name, cover_path, int(total), now),
                )
                pack_id = int(cur_pack.lastrowid)
                created = True

            conn.commit()

        logger.info(
            "manual pack upload series #%d -> pack #%d (%d stickers, created=%s)",
            series_id, pack_id, len(saved_stickers), created,
        )
        return {
            "pack_id": pack_id,
            "pack_uid": pack_uid,
            "preview_id": preview_id,
            "created": created,
            "uploaded_stickers": len(saved_stickers),
            "total_stickers": int(total),
        }

    def update_status(self, pack_id: int, new_status: str) -> bool:
        if new_status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {new_status!r} (allowed: {VALID_STATUSES})")
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE packs SET status = ? WHERE id = ?",
                (new_status, pack_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def rename(self, pack_id: int, display_name: str) -> bool:
        display_name = display_name.strip()[:200]
        if not display_name:
            raise ValueError("display_name cannot be empty")
        with _open_db(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE packs SET display_name = ? WHERE id = ?",
                (display_name, pack_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_cover(self, pack_id: int, sticker_id: int) -> bool:
        with _open_db(self.db_path) as conn:
            sticker = conn.execute(
                """
                SELECT ps.image_path, pp.series_id
                  FROM pack_stickers ps
                  JOIN pack_previews pp ON pp.id = ps.preview_id
                 WHERE ps.id = ? AND ps.generation_status = 'ok'
                """,
                (sticker_id,),
            ).fetchone()
            if not sticker:
                raise ValueError(f"sticker #{sticker_id} not found or not generated")
            pack = conn.execute(
                "SELECT series_id FROM packs WHERE id = ?", (pack_id,),
            ).fetchone()
            if not pack:
                raise ValueError(f"pack #{pack_id} not found")
            if sticker["series_id"] != pack["series_id"]:
                raise ValueError("sticker does not belong to this pack's series")
            cur = conn.execute(
                "UPDATE packs SET cover_image_path = ? WHERE id = ?",
                (sticker["image_path"], pack_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_cover_from_preview(self, pack_id: int, preview_id: int) -> bool:
        with _open_db(self.db_path) as conn:
            preview = conn.execute(
                """
                SELECT image_path, series_id
                  FROM pack_previews
                 WHERE id = ? AND generation_status = 'ok'
                """,
                (preview_id,),
            ).fetchone()
            if not preview:
                raise ValueError(f"preview #{preview_id} not found or not generated")
            pack = conn.execute(
                "SELECT series_id FROM packs WHERE id = ?", (pack_id,),
            ).fetchone()
            if not pack:
                raise ValueError(f"pack #{pack_id} not found")
            if preview["series_id"] != pack["series_id"]:
                raise ValueError("preview does not belong to this pack's series")
            cur = conn.execute(
                "UPDATE packs SET cover_image_path = ? WHERE id = ?",
                (preview["image_path"], pack_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def refresh_total_stickers(self, pack_id: int) -> int:
        """Recount: prefer real ok+selected stickers, fall back to expected
        sticker count from metadata.preview_briefs when nothing has been
        split yet (preview-only pack)."""
        with _open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT series_id FROM packs WHERE id = ?", (pack_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"pack #{pack_id} not found")
            cnt = conn.execute(
                """
                SELECT COUNT(*) FROM pack_stickers ps
                  JOIN pack_previews pp ON pp.id = ps.preview_id
                 WHERE pp.series_id = ?
                   AND ps.generation_status = 'ok'
                   AND ps.is_selected = 1
                """,
                (row["series_id"],),
            ).fetchone()[0]
            if cnt == 0:
                meta = conn.execute(
                    "SELECT metadata_json FROM pack_series WHERE id = ?",
                    (row["series_id"],),
                ).fetchone()
                try:
                    md = json.loads(meta["metadata_json"] or "{}")
                    cnt = sum(len(b.get("stickers") or [])
                              for b in md.get("preview_briefs", []))
                except Exception:
                    pass
            conn.execute("UPDATE packs SET total_stickers = ? WHERE id = ?",
                         (cnt, pack_id))
            conn.commit()
        return cnt

    def delete_pack(self, pack_id: int) -> dict[str, Any]:
        """Delete only the aggregate ``packs`` row.

        Source series, previews and stickers are intentionally preserved, so
        a mistaken delete can be recreated from the same series. Downstream
        videos/products block deletion to avoid orphaning work; operators can
        archive those packs instead.
        """
        with _open_db(self.db_path) as conn:
            pack = conn.execute(
                "SELECT id FROM packs WHERE id = ?", (pack_id,),
            ).fetchone()
            if not pack:
                raise ValueError(f"pack #{pack_id} not found")

            videos = conn.execute(
                "SELECT COUNT(*) FROM tk_videos WHERE pack_id = ?", (pack_id,),
            ).fetchone()[0]
            products = conn.execute(
                "SELECT COUNT(*) FROM tkshop_products WHERE pack_id = ?", (pack_id,),
            ).fetchone()[0]
            if videos or products:
                raise ValueError(
                    f"pack #{pack_id} has downstream work "
                    f"({videos} videos, {products} products); archive instead"
                )

            conn.execute("DELETE FROM packs WHERE id = ?", (pack_id,))
            conn.commit()
        return {"deleted": True, "videos": videos, "products": products}


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[PackService] = None


def get_pack_service() -> PackService:
    global _svc
    if _svc is None:
        _svc = PackService()
    return _svc
