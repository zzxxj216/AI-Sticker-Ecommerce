"""Pack aggregate service — A.4 of the V2 pipeline.

The packs row is created on demand when the operator confirms a
generated pack_series is "good enough to ship". From that point on,
downstream stages (B.1 TK videos, C.1 TKShop products) reference the
pack_id and treat the pack as the unit of work.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger

logger = get_logger("service.packs")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

VALID_STATUSES = ("active", "archived")


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
            if not ok_stickers:
                raise ValueError("series has no successful selected stickers — "
                                 "generate previews and split first")

            cover_path = ""
            if cover_sticker_id:
                hit = next((s for s in ok_stickers if s["id"] == cover_sticker_id), None)
                if not hit:
                    raise ValueError(f"sticker #{cover_sticker_id} not in this series's ok set")
                cover_path = hit["image_path"]
            else:
                cover_path = ok_stickers[0]["image_path"]

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
                    len(ok_stickers),
                    now,
                ),
            )
            conn.commit()
            pack_id = cur.lastrowid
        logger.info("pack #%d created for series #%d (%d stickers)",
                    pack_id, series_id, len(ok_stickers))
        return {"pack_id": pack_id, "pack_uid": series["pack_uid"],
                "created": True, "total_stickers": len(ok_stickers)}

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_packs(
        self,
        *,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        clauses, params = [], []
        if status and status != "all":
            clauses.append("p.status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with _open_db(self.db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM packs p {where}", tuple(params),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT p.id, p.pack_uid, p.series_id, p.display_name,
                       p.cover_image_path, p.total_stickers, p.status,
                       p.created_at,
                       s.series_idx, s.series_name, s.pack_archetype,
                       s.priority, s.plan_id,
                       t.topic_name
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
        return [dict(r) for r in rows], total

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
                SELECT id, preview_idx, image_path, generation_status
                  FROM pack_previews
                 WHERE series_id = ?
                 ORDER BY preview_idx
                """,
                (d["series_id"],),
            ).fetchall()
            d["previews"] = [dict(p) for p in previews]
        return d

    # ------------------------------------------------------------------
    # Mutate
    # ------------------------------------------------------------------

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

    def refresh_total_stickers(self, pack_id: int) -> int:
        """Recount ok+selected stickers — useful after operator toggles
        is_selected on individual stickers post-creation.
        """
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
            conn.execute("UPDATE packs SET total_stickers = ? WHERE id = ?",
                         (cnt, pack_id))
            conn.commit()
        return cnt


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[PackService] = None


def get_pack_service() -> PackService:
    global _svc
    if _svc is None:
        _svc = PackService()
    return _svc
