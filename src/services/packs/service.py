"""Pack aggregate service — A.4 of the V2 pipeline.

The packs row is created on demand when the operator confirms a
generated pack_series is "good enough to ship". From that point on,
downstream stages (B.1 TK videos, C.1 TKShop products) reference the
pack_id and treat the pack as the unit of work.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import time
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.preview_gen.prompts import build_preview_prompt
from src.services.storage.pack_store import get_pack_store, make_pack_uid

logger = get_logger("service.packs")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")

VALID_STATUSES = ("active", "archived")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_SOURCE_SUFFIXES = _IMAGE_SUFFIXES | {".pdf"}
DEFAULT_EXTERNAL_AI_MAX_STICKERS_PER_PREVIEW = int(
    os.getenv("TKSHOP_EXTERNAL_IMPORT_MAX_STICKERS_PER_PREVIEW", "80")
)
EXTERNAL_AI_HARD_MAX_STICKERS_PER_PREVIEW = int(
    os.getenv("TKSHOP_EXTERNAL_IMPORT_HARD_MAX_STICKERS_PER_PREVIEW", "160")
)
EXTERNAL_AI_ANALYSIS_MAX_SIDE = int(
    os.getenv("TKSHOP_EXTERNAL_IMPORT_AI_MAX_SIDE", "1600")
)
DEFAULT_EXTERNAL_GROUP_STICKERS_PER_PREVIEW = int(
    os.getenv("TKSHOP_EXTERNAL_IMPORT_GROUP_STICKERS_PER_PREVIEW", "10")
)


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
            has_local_products = bool(conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'local_products'",
            ).fetchone())
            local_product_count_sql = (
                "(SELECT COUNT(*) FROM local_products lp WHERE lp.pack_id = p.id)"
                if has_local_products
                else "0"
            )
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
                         WHERE pr.pack_id = p.id) AS product_count,
                       {local_product_count_sql} AS local_product_count
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
            local_products: list[sqlite3.Row] = []
            local_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'local_products'",
            ).fetchone()
            if local_table:
                local_products = conn.execute(
                    """
                    SELECT id, title, seller_sku, category_id, created_at, updated_at
                      FROM local_products WHERE pack_id = ? ORDER BY id DESC
                    """,
                    (pack_id,),
                ).fetchall()
        d["videos"] = [dict(v) for v in videos]
        d["products"] = [dict(p) for p in products]
        d["local_products"] = [dict(p) for p in local_products]
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
    def _source_suffix(filename: str) -> str:
        suffix = Path(filename or "").suffix.lower()
        return suffix if suffix in _SOURCE_SUFFIXES else ""

    @staticmethod
    def _write_bytes_atomic(target: Path, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)

    @staticmethod
    def _image_to_png_bytes(data: bytes) -> bytes:
        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            if im.mode in ("RGBA", "LA") or "transparency" in im.info:
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                bg.alpha_composite(im.convert("RGBA"))
                im = bg
            out = BytesIO()
            im.convert("RGB").save(out, "PNG")
            return out.getvalue()

    @staticmethod
    def _image_to_analysis_png_bytes(data: bytes, *, max_side: int = EXTERNAL_AI_ANALYSIS_MAX_SIDE) -> bytes:
        from PIL import Image

        with Image.open(BytesIO(data)) as im:
            if im.mode in ("RGBA", "LA") or "transparency" in im.info:
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                bg.alpha_composite(im.convert("RGBA"))
                im = bg
            im = im.convert("RGB")
            if max_side > 0:
                im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            out = BytesIO()
            im.save(out, "PNG", optimize=True)
            return out.getvalue()

    @staticmethod
    def _clean_ai_text(value: Any, *, limit: int = 500) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return text[:limit]

    @staticmethod
    def _normalize_ai_sticker_list(items: Any, *, max_items: int) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        if not isinstance(items, list):
            return out
        for item in items:
            if isinstance(item, dict):
                text = (
                    item.get("brief")
                    or item.get("name")
                    or item.get("title")
                    or item.get("description")
                    or item.get("text")
                    or ""
                )
                if item.get("visible_text") and item.get("description"):
                    text = f"{item.get('visible_text')} / {item.get('description')}"
            else:
                text = item
            clean = PackService._clean_ai_text(text, limit=240)
            if not clean:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(clean)
            if len(out) >= max_items:
                break
        return out

    @staticmethod
    def _build_external_preview_analysis_prompt(
        *,
        pack_name: str,
        preview_idx: int,
        file_name: str,
        max_stickers: int,
    ) -> str:
        return f"""
You are analyzing an uploaded sticker/card pack preview image for an ecommerce workflow.

The image is preview #{preview_idx} from pack "{pack_name}" (source file: {file_name}).
Return ONLY valid JSON, no markdown, matching this schema:
{{
  "pack_name": "short sellable pack name if visible/inferable",
  "preview_theme": "specific theme for this preview",
  "style_anchor": "one concise English style guide for future product copy and image editing",
  "palette": "comma-separated dominant colors, include hex codes when confident",
  "pack_archetype": "external_upload | sticker_pack | card_sticker_pack | decal_pack | label_pack",
  "stickers": [
    "Sticker/card 1: visible text if any / concise visual description",
    "Sticker/card 2: visible text if any / concise visual description"
  ],
  "quality_notes": "short notes about layout, crop, duplicated items, unclear items"
}}

Rules:
- Identify the distinct visible sticker/card designs in reading order, left-to-right and top-to-bottom.
- Include up to {max_stickers} designs. Do not invent hidden designs outside the image.
- If the image is a single sheet containing many designs, list every distinct visible design you can identify.
- If a design has readable text, preserve that text exactly as visible.
- Keep each sticker list item short but specific enough for an image-edit split prompt.
- Use English for style_anchor and sticker descriptions, except keep visible non-English text exactly.
""".strip()

    @staticmethod
    def _fallback_brief_from_preview(preview: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        idx = int(preview["preview_idx"])
        filename = str(preview["prompt_text"] or f"preview_{idx}")
        return {
            "preview_idx": idx,
            "theme": f"Imported preview {idx}",
            "stickers": [f"Visible sticker/card artwork from imported preview {idx}"],
            "external_import": True,
            "ai_enriched": False,
            "fallback": True,
            "source_prompt": filename[:200],
        }

    @staticmethod
    def _distribute_evenly(items: list[Any], group_count: int) -> list[list[Any]]:
        group_count = max(1, min(int(group_count or 1), max(1, len(items))))
        groups: list[list[Any]] = []
        n = len(items)
        start = 0
        for i in range(group_count):
            remaining_items = n - start
            remaining_groups = group_count - i
            size = (remaining_items + remaining_groups - 1) // remaining_groups
            groups.append(items[start:start + size])
            start += size
        return [g for g in groups if g]

    @staticmethod
    def _target_group_count(
        *,
        total_items: int,
        target_preview_count: int = 0,
        stickers_per_preview: int = DEFAULT_EXTERNAL_GROUP_STICKERS_PER_PREVIEW,
    ) -> int:
        if total_items <= 0:
            return 0
        if target_preview_count and target_preview_count > 0:
            return max(1, min(int(target_preview_count), total_items))
        per = max(1, int(stickers_per_preview or DEFAULT_EXTERNAL_GROUP_STICKERS_PER_PREVIEW))
        return max(1, (total_items + per - 1) // per)

    @staticmethod
    def _build_external_group_prompt(
        *,
        group_idx: int,
        group_count: int,
        stickers: list[str],
        style_anchor: str,
    ) -> str:
        lines = "\n".join(f"{i}. {s}" for i, s in enumerate(stickers, 1))
        return (
            "Create a clean ecommerce sticker/card sheet preview by extracting "
            f"ONLY this subset from the uploaded full pack sheet: group {group_idx} "
            f"of {group_count}.\n\n"
            f"Subset designs:\n{lines}\n\n"
            "Keep the exact artwork, text, colors, outlines, and visual style from "
            "the source image. Arrange only these designs as a neat grid on a clean "
            "white background, with every sticker/card fully visible, evenly spaced, "
            "no overlap, no extra designs, no packaging, no mockup, no hands, square "
            "1:1 product preview composition. "
            f"Style reference: {style_anchor[:500]}"
        )

    @staticmethod
    def _build_external_local_group_prompt(
        *,
        group_idx: int,
        group_count: int,
        stickers: list[str],
    ) -> str:
        lines = "\n".join(f"{i}. {s}" for i, s in enumerate(stickers, 1))
        return (
            "Local pixel-preserving grouped preview assembled from the uploaded "
            f"source artwork: group {group_idx} of {group_count}.\n\n"
            f"Included designs:\n{lines}\n\n"
            "No AI redraw was used for this preview; source sticker regions were "
            "cropped locally and arranged on a clean white ecommerce sheet."
        )

    @staticmethod
    def _crop_external_box_bytes(preview_path: Path, box: tuple[int, int, int, int]) -> bytes:
        from PIL import Image

        with Image.open(preview_path) as source:
            crop = source.convert("RGBA").crop(box)
            out = BytesIO()
            crop.save(out, format="PNG")
            return out.getvalue()

    @staticmethod
    def _compose_external_group_preview(crops: list[bytes], *, canvas_size: int = 1024) -> bytes:
        from PIL import Image

        if not crops:
            raise ValueError("no local sticker crops to compose")
        n = len(crops)
        cols = max(1, int((n * 1.15) ** 0.5 + 0.999))
        rows = max(1, (n + cols - 1) // cols)
        margin = 54 if n <= 10 else 42
        gap = 24 if n <= 10 else 18
        cell_w = max(1, (canvas_size - margin * 2 - gap * (cols - 1)) // cols)
        cell_h = max(1, (canvas_size - margin * 2 - gap * (rows - 1)) // rows)
        sheet = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 255))

        for idx, crop_bytes in enumerate(crops):
            with Image.open(BytesIO(crop_bytes)) as src:
                sticker = src.convert("RGBA")
            w, h = sticker.size
            if w <= 0 or h <= 0:
                continue
            scale = min(cell_w / w, cell_h / h, 1.2)
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            if new_size != sticker.size:
                sticker = sticker.resize(new_size, Image.Resampling.LANCZOS)
            row = idx // cols
            col = idx % cols
            x = margin + col * (cell_w + gap) + (cell_w - sticker.width) // 2
            y = margin + row * (cell_h + gap) + (cell_h - sticker.height) // 2
            sheet.alpha_composite(sticker, (x, y))

        out = BytesIO()
        sheet.convert("RGB").save(out, format="PNG")
        return out.getvalue()

    def _extract_external_local_crops(
        self,
        source_briefs: list[dict[str, Any]],
    ) -> tuple[dict[tuple[int, int], bytes], list[dict[str, Any]], list[dict[str, Any]]]:
        from src.services.preview_gen.service import PreviewGenService

        crop_map: dict[tuple[int, int], bytes] = {}
        crop_errors: list[dict[str, Any]] = []
        crop_warnings: list[dict[str, Any]] = []
        for brief in source_briefs:
            source_idx = int(brief.get("source_preview_idx") or brief.get("preview_idx") or 0)
            image_path = Path(str(brief.get("source_image_path") or ""))
            stickers = list(brief.get("stickers") or [])
            if source_idx <= 0 or not image_path.is_file() or not stickers:
                continue
            try:
                boxes = PreviewGenService._detect_sticker_boxes(image_path, len(stickers))
            except Exception as e:  # noqa: BLE001
                crop_errors.append({
                    "source_preview_idx": source_idx,
                    "error": f"{type(e).__name__}: {e}"[:500],
                })
                continue

            listed_before = len(stickers)
            if len(boxes) > len(stickers):
                for extra_idx in range(listed_before + 1, len(boxes) + 1):
                    stickers.append(
                        f"Visible sticker/card artwork from imported preview {source_idx} item {extra_idx}"
                    )
                brief["stickers"] = stickers
                crop_warnings.append({
                    "source_preview_idx": source_idx,
                    "detected": len(boxes),
                    "listed": listed_before,
                    "note": "local crop detected extra sticker regions; placeholder descriptions were added",
                })
            elif len(boxes) < len(stickers):
                brief["stickers"] = stickers[:len(boxes)]
                crop_warnings.append({
                    "source_preview_idx": source_idx,
                    "detected": len(boxes),
                    "listed": len(stickers),
                    "note": "local crop detected fewer regions than AI listed; AI list was trimmed to detected regions",
                })

            for item_idx, box in enumerate(boxes, 1):
                try:
                    crop_map[(source_idx, item_idx)] = self._crop_external_box_bytes(image_path, box)
                except Exception as e:  # noqa: BLE001
                    crop_errors.append({
                        "source_preview_idx": source_idx,
                        "source_item_idx": item_idx,
                        "error": f"{type(e).__name__}: {e}"[:500],
                    })
        return crop_map, crop_errors, crop_warnings

    @staticmethod
    def _pdf_pages_to_png_bytes(data: bytes, *, max_side: int = 2048) -> list[bytes]:
        """Render every PDF page to PNG bytes.

        PyMuPDF is preferred because it is already available in the runtime;
        pypdfium2 is a fallback. Keeping this local avoids adding a hard
        import at module load time for deployments that only import images.
        """
        try:
            import fitz  # type: ignore

            with fitz.open(stream=data, filetype="pdf") as doc:
                if len(doc) == 0:
                    raise ValueError("PDF has no pages")
                rendered: list[bytes] = []
                for page in doc:
                    rect = page.rect
                    scale = min(3.0, max(1.0, max_side / max(rect.width, rect.height)))
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    rendered.append(pix.tobytes("png"))
                return rendered
        except Exception as first_error:
            try:
                import pypdfium2 as pdfium  # type: ignore
                from PIL import Image

                pdf = pdfium.PdfDocument(BytesIO(data))
                try:
                    if len(pdf) == 0:
                        raise ValueError("PDF has no pages")
                    rendered: list[bytes] = []
                    for page in pdf:
                        bitmap = page.render(scale=2.0)
                        pil = bitmap.to_pil().convert("RGB")
                        pil.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                        out = BytesIO()
                        pil.save(out, "PNG")
                        rendered.append(out.getvalue())
                    return rendered
                finally:
                    pdf.close()
            except Exception as second_error:
                raise ValueError(
                    "failed to render PDF pages "
                    f"({type(first_error).__name__}: {first_error}; "
                    f"{type(second_error).__name__}: {second_error})"
                ) from second_error

    def _preview_pngs_from_source(self, asset: dict[str, Any]) -> list[tuple[str, bytes]]:
        filename = asset.get("filename") or "preview.png"
        suffix = Path(filename).suffix.lower()
        data = asset.get("data") or b""
        if suffix == ".pdf":
            pages = self._pdf_pages_to_png_bytes(data)
            return [(f"{Path(filename).stem}_page_{i}", raw) for i, raw in enumerate(pages, 1)]
        if suffix in _IMAGE_SUFFIXES:
            return [(Path(filename).stem or "preview", self._image_to_png_bytes(data))]
        raise ValueError(f"unsupported source file type: {filename}")

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

    def import_external_pack(
        self,
        *,
        display_name: str,
        source_assets: list[dict[str, Any]],
        total_stickers: int = 0,
        style_anchor: str = "",
        palette: str = "",
        pack_archetype: str = "external_upload",
        topic_name: str = "",
        auto_named: bool = False,
    ) -> dict[str, Any]:
        """Create a complete preview-only pack from operator-supplied files.

        This is the lightweight import path for sticker/card packs designed
        outside the system. It creates the normal hot_topic -> topic_plan ->
        pack_series -> pack_previews -> packs chain, but deliberately does
        not create ``pack_stickers`` rows. Downstream product copy and main
        image generation can continue from the imported preview images.
        """
        assets = [a for a in (source_assets or []) if a.get("data")]
        if not assets:
            raise ValueError("at least one PDF or image file is required")

        final_name = (display_name or "").strip()[:200]
        if not final_name:
            final_name = Path(assets[0].get("filename") or "external_pack").stem
        if not final_name:
            final_name = "External Sticker Pack"
        topic = (topic_name or final_name).strip()[:200]
        now = int(time.time())
        pack_uid = make_pack_uid(final_name)
        store = get_pack_store()
        store.init_pack_dir(pack_uid)
        series_idx = 1
        series_dir = store.series_dir(pack_uid, series_idx)
        source_dir = series_dir / "source_files"
        source_dir.mkdir(parents=True, exist_ok=True)

        saved_sources: list[dict[str, Any]] = []
        preview_rows: list[dict[str, Any]] = []
        preview_idx = 0
        for source_idx, asset in enumerate(assets, 1):
            filename = Path(asset.get("filename") or f"source_{source_idx}").name
            source_suffix = self._source_suffix(filename)
            if not source_suffix:
                raise ValueError(f"unsupported source file type: {filename}")
            stem = self._safe_asset_stem(filename, f"source_{source_idx}")
            source_path = source_dir / f"{source_idx:02d}_{stem}{source_suffix}"
            self._write_bytes_atomic(source_path, asset["data"])

            saved_source = {
                "filename": filename,
                "source_path": source_path.as_posix(),
                "preview_paths": [],
            }
            for page_label, preview_png in self._preview_pngs_from_source(asset):
                preview_idx += 1
                prompt_text = f"external imported preview from {filename}"
                if source_suffix == ".pdf":
                    prompt_text += f" ({page_label})"
                preview_path = store.write_preview(
                    pack_uid,
                    series_idx,
                    preview_idx,
                    preview_png,
                    prompt_text=prompt_text,
                )
                saved_source["preview_paths"].append(preview_path.as_posix())
                preview_rows.append({
                    "preview_idx": preview_idx,
                    "filename": filename,
                    "page_label": page_label,
                    "image_path": preview_path.as_posix(),
                    "prompt_text": prompt_text,
                })
            saved_sources.append(saved_source)

        if not preview_rows:
            raise ValueError("no preview images could be extracted from uploaded files")

        expected_total = int(total_stickers or 0)
        if expected_total <= 0:
            expected_total = max(1, len(preview_rows) * 10)

        source_names = [r["filename"] for r in saved_sources]
        metadata = {
            "external_import": True,
            "external_imported_at": now,
            "auto_named": bool(auto_named),
            "source_files": saved_sources,
            "preview_briefs": [
                {
                    "preview_idx": r["preview_idx"],
                    "theme": Path(r["filename"]).stem or f"Preview {r['preview_idx']}",
                    "stickers": [
                        f"External uploaded sticker/card artwork from {r['filename']}"
                    ],
                    "external_import": True,
                }
                for r in preview_rows
            ],
            "recommended_total_stickers": expected_total,
        }
        style_text = (style_anchor or "").strip()
        if not style_text:
            style_text = (
                f"External uploaded sticker/card pack named {final_name}. "
                "Use the imported preview artwork as the authoritative visual "
                "source for product copy and product-image generation."
            )
        plan_payload = {
            "external_import": True,
            "pack_uid": pack_uid,
            "display_name": final_name,
            "auto_named": bool(auto_named),
            "source_files": source_names,
            "total_stickers": expected_total,
        }

        with _open_db(self.db_path) as conn:
            cur_topic = conn.execute(
                """
                INSERT INTO hot_topics
                    (source, query, topic_name, raw_payload, evidence_urls,
                     hot_score, region, fetched_at, status, theme_summary,
                     parent_topic_ids)
                VALUES ('external_upload', '', ?, ?, '[]', 0, '', ?,
                        'selected', ?, '[]')
                """,
                (
                    topic,
                    json.dumps(plan_payload, ensure_ascii=False),
                    now,
                    style_text,
                ),
            )
            topic_id = int(cur_topic.lastrowid)

            cur_plan = conn.execute(
                """
                INSERT INTO topic_plans
                    (topic_id, config, main_raw_text, series_payload,
                     status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'approved', ?, ?)
                """,
                (
                    topic_id,
                    json.dumps(
                        {
                            "external_import": True,
                            "previews_per_series": len(preview_rows),
                            "stickers_per_preview": 0,
                            "total_stickers": expected_total,
                        },
                        ensure_ascii=False,
                    ),
                    f"External upload import for {final_name}.",
                    json.dumps(plan_payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            plan_id = int(cur_plan.lastrowid)

            cur_series = conn.execute(
                """
                INSERT INTO pack_series
                    (plan_id, series_idx, series_name, style_anchor, palette,
                     pack_archetype, priority, metadata_json, is_selected,
                     pack_uid)
                VALUES (?, 1, ?, ?, ?, ?, 'medium', ?, 1, ?)
                """,
                (
                    plan_id,
                    final_name,
                    style_text,
                    (palette or "").strip(),
                    (pack_archetype or "external_upload").strip(),
                    json.dumps(metadata, ensure_ascii=False),
                    pack_uid,
                ),
            )
            series_id = int(cur_series.lastrowid)

            for r in preview_rows:
                conn.execute(
                    """
                    INSERT INTO pack_previews
                        (series_id, preview_idx, prompt_text, image_path,
                         model_used, generation_status, generated_at)
                    VALUES (?, ?, ?, ?, 'external_upload', 'ok', ?)
                    """,
                    (
                        series_id,
                        int(r["preview_idx"]),
                        r["prompt_text"],
                        r["image_path"],
                        now,
                    ),
                )

            cover_path = preview_rows[0]["image_path"]
            cur_pack = conn.execute(
                """
                INSERT INTO packs
                    (pack_uid, series_id, display_name, cover_image_path,
                     total_stickers, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    pack_uid,
                    series_id,
                    final_name,
                    cover_path,
                    expected_total,
                    now,
                ),
            )
            pack_id = int(cur_pack.lastrowid)
            conn.commit()

        store.write_plan(pack_uid, plan_payload)
        store.write_plan_main_raw(pack_uid, f"External upload import for {final_name}.")
        store.write_style_anchor(pack_uid, series_idx, style_text)
        logger.info(
            "external pack import -> pack #%d (%d previews, total=%d)",
            pack_id, len(preview_rows), expected_total,
        )
        return {
            "pack_id": pack_id,
            "pack_uid": pack_uid,
            "topic_id": topic_id,
            "plan_id": plan_id,
            "series_id": series_id,
            "preview_count": len(preview_rows),
            "total_stickers": expected_total,
            "cover_image_path": cover_path,
        }

    def _analyze_external_preview_with_ai(
        self,
        *,
        pack_name: str,
        preview: sqlite3.Row,
        max_stickers: int,
    ) -> dict[str, Any]:
        image_path = Path(preview["image_path"] or "")
        if not image_path.is_file():
            raise ValueError(f"preview file missing: {image_path}")

        from src.core.config import config
        from src.services.ai.base import try_parse_json
        from src.services.ai.call_logger import AICallLog
        from src.services.ai.gemini_service import GeminiService

        image_bytes = self._image_to_analysis_png_bytes(image_path.read_bytes())
        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        model = config.gemini_text_model
        prompt = self._build_external_preview_analysis_prompt(
            pack_name=pack_name,
            preview_idx=int(preview["preview_idx"]),
            file_name=str(preview["prompt_text"] or image_path.name),
            max_stickers=max_stickers,
        )
        gemini = GeminiService(model=model)
        with AICallLog(
            service="gemini",
            model=model,
            task="external_pack:analyze_preview",
            related_table="pack_previews",
            related_id=int(preview["id"]),
            prompt_summary=prompt[:500],
            db_path=self.db_path,
        ) as log:
            result = gemini.analyze_image(
                image_b64,
                prompt,
                media_type="image/png",
                max_tokens=8192,
            )
            usage = result.get("usage") or {}
            log.set_usage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cost=float(result.get("cost") or 0.0),
            )
        parsed = try_parse_json(result.get("text") or "")
        if not isinstance(parsed, dict):
            raise ValueError("AI did not return a JSON object")
        return parsed

    def enrich_external_pack_with_ai(
        self,
        pack_id: int,
        *,
        split: bool = False,
        group_previews: bool = True,
        target_preview_count: int = 0,
        stickers_per_preview: int = DEFAULT_EXTERNAL_GROUP_STICKERS_PER_PREVIEW,
        max_stickers_per_preview: int = DEFAULT_EXTERNAL_AI_MAX_STICKERS_PER_PREVIEW,
        replace_existing_stickers: bool = True,
        split_workers: int = 1,
    ) -> dict[str, Any]:
        """Analyze imported preview images and optionally create grouped previews.

        External uploads initially have preview images but no reliable
        ``pack_stickers`` rows. This method lets Gemini read each preview,
        identify every sticker/card design, then distributes those designs
        across one or more preview sheet rows. ``split`` is intentionally
        false by default: single-sticker extraction is a later/manual stage.
        """
        try:
            max_stickers = int(max_stickers_per_preview or 0)
        except (TypeError, ValueError):
            max_stickers = DEFAULT_EXTERNAL_AI_MAX_STICKERS_PER_PREVIEW
        max_stickers = max(1, min(max_stickers, EXTERNAL_AI_HARD_MAX_STICKERS_PER_PREVIEW))
        split_workers = max(1, int(split_workers or 1))

        with _open_db(self.db_path) as conn:
            pack = conn.execute(
                """
                SELECT p.id, p.display_name, p.total_stickers, p.pack_uid,
                       p.cover_image_path,
                       s.id AS series_id, s.series_idx, s.series_name,
                       s.style_anchor, s.palette, s.pack_archetype,
                       s.metadata_json
                  FROM packs p
                  JOIN pack_series s ON s.id = p.series_id
                 WHERE p.id = ?
                """,
                (int(pack_id),),
            ).fetchone()
            if not pack:
                raise ValueError(f"pack #{pack_id} not found")
            previews = conn.execute(
                """
                SELECT id, preview_idx, prompt_text, image_path,
                       model_used, generation_status, generated_at
                  FROM pack_previews
                 WHERE series_id = ?
                   AND generation_status = 'ok'
                   AND COALESCE(image_path, '') != ''
                 ORDER BY preview_idx
                """,
                (int(pack["series_id"]),),
            ).fetchall()

        if not previews:
            raise ValueError(f"pack #{pack_id} has no generated preview images")

        source_previews = list(previews)

        try:
            metadata = json.loads(pack["metadata_json"] or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except Exception:
            metadata = {}
        old_briefs = {
            int(b.get("preview_idx") or 0): b
            for b in (metadata.get("preview_briefs") or [])
            if isinstance(b, dict)
        }

        analyses: list[dict[str, Any]] = []
        source_briefs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        style_candidates: list[str] = []
        palette_candidates: list[str] = []
        archetype_candidates: list[str] = []
        pack_name_candidates: list[str] = []

        for preview in previews:
            preview_idx = int(preview["preview_idx"])
            try:
                raw = self._analyze_external_preview_with_ai(
                    pack_name=pack["display_name"] or pack["series_name"] or f"pack_{pack_id}",
                    preview=preview,
                    max_stickers=max_stickers,
                )
                stickers = self._normalize_ai_sticker_list(
                    raw.get("stickers")
                    or raw.get("cards")
                    or raw.get("designs")
                    or raw.get("items"),
                    max_items=max_stickers,
                )
                if not stickers:
                    previous = old_briefs.get(preview_idx) or {}
                    stickers = self._normalize_ai_sticker_list(
                        previous.get("stickers"),
                        max_items=max_stickers,
                    )
                if not stickers:
                    stickers = self._fallback_brief_from_preview(preview)["stickers"]

                theme = self._clean_ai_text(
                    raw.get("preview_theme") or raw.get("theme") or f"Imported preview {preview_idx}",
                    limit=120,
                )
                brief = {
                    "source_preview_idx": preview_idx,
                    "theme": theme or f"Imported preview {preview_idx}",
                    "stickers": stickers,
                    "external_import": True,
                    "ai_enriched": True,
                    "source_preview_id": int(preview["id"]),
                    "source_image_path": preview["image_path"],
                }
                source_briefs.append(brief)
                clean_analysis = {
                    "preview_idx": preview_idx,
                    "preview_id": int(preview["id"]),
                    "theme": brief["theme"],
                    "sticker_count": len(stickers),
                    "style_anchor": self._clean_ai_text(raw.get("style_anchor"), limit=800),
                    "palette": self._clean_ai_text(raw.get("palette"), limit=300),
                    "pack_archetype": self._clean_ai_text(raw.get("pack_archetype"), limit=80),
                    "pack_name": self._clean_ai_text(raw.get("pack_name"), limit=160),
                    "quality_notes": self._clean_ai_text(raw.get("quality_notes"), limit=500),
                }
                analyses.append(clean_analysis)
                if clean_analysis["style_anchor"]:
                    style_candidates.append(clean_analysis["style_anchor"])
                if clean_analysis["palette"]:
                    palette_candidates.append(clean_analysis["palette"])
                if clean_analysis["pack_archetype"]:
                    archetype_candidates.append(clean_analysis["pack_archetype"])
                if clean_analysis["pack_name"]:
                    pack_name_candidates.append(clean_analysis["pack_name"])
            except Exception as e:  # noqa: BLE001 - keep background job moving per preview
                err = f"{type(e).__name__}: {e}"
                logger.warning(
                    "external pack #%d preview #%d AI enrichment failed: %s",
                    pack_id,
                    preview_idx,
                    err,
                )
                errors.append({
                    "preview_idx": preview_idx,
                    "preview_id": int(preview["id"]),
                    "error": err[:500],
                })
                previous = old_briefs.get(preview_idx)
                if previous and previous.get("ai_enriched") and previous.get("stickers"):
                    brief = dict(previous)
                    brief["source_preview_idx"] = preview_idx
                    brief["source_preview_id"] = int(preview["id"])
                    brief["source_image_path"] = preview["image_path"]
                    brief["ai_reused_after_error"] = True
                    brief["ai_error"] = err[:300]
                else:
                    brief = {
                        "source_preview_idx": preview_idx,
                        "theme": f"Imported preview {preview_idx}",
                        "stickers": [],
                        "external_import": True,
                        "ai_enriched": False,
                        "ai_error": err[:300],
                        "source_preview_id": int(preview["id"]),
                        "source_image_path": preview["image_path"],
                    }
                source_briefs.append(brief)

        source_briefs.sort(key=lambda b: int(b.get("source_preview_idx") or b.get("preview_idx") or 0))
        local_crop_map: dict[tuple[int, int], bytes] = {}
        local_crop_errors: list[dict[str, Any]] = []
        local_crop_warnings: list[dict[str, Any]] = []
        if group_previews:
            local_crop_map, local_crop_errors, local_crop_warnings = self._extract_external_local_crops(source_briefs)
        all_sticker_items: list[dict[str, Any]] = []
        seen_stickers: set[str] = set()
        for brief in source_briefs:
            for source_item_idx, sticker in enumerate(brief.get("stickers") or [], 1):
                clean = self._clean_ai_text(sticker, limit=240)
                if not clean:
                    continue
                key = clean.casefold()
                if key in seen_stickers:
                    continue
                seen_stickers.add(key)
                all_sticker_items.append({
                    "text": clean,
                    "source_preview_idx": int(brief.get("source_preview_idx") or brief.get("preview_idx") or 0),
                    "source_item_idx": source_item_idx,
                    "source_preview_id": int(brief.get("source_preview_id") or 0),
                    "source_image_path": brief.get("source_image_path") or "",
                })
        total_expected = len(all_sticker_items)
        group_count = self._target_group_count(
            total_items=total_expected,
            target_preview_count=int(target_preview_count or 0),
            stickers_per_preview=int(stickers_per_preview or DEFAULT_EXTERNAL_GROUP_STICKERS_PER_PREVIEW),
        )
        grouped_items = self._distribute_evenly(all_sticker_items, group_count) if total_expected else []
        grouped_briefs: list[dict[str, Any]] = []
        for idx, items in enumerate(grouped_items, 1):
            source_id_counts = Counter(int(item.get("source_preview_id") or 0) for item in items)
            source_idx_counts = Counter(int(item.get("source_preview_idx") or 0) for item in items)
            primary_source_id = source_id_counts.most_common(1)[0][0] if source_id_counts else 0
            primary_source_idx = source_idx_counts.most_common(1)[0][0] if source_idx_counts else 0
            primary_source_path = next(
                (
                    str(item.get("source_image_path") or "")
                    for item in items
                    if int(item.get("source_preview_id") or 0) == primary_source_id
                    and str(item.get("source_image_path") or "")
                ),
                "",
            )
            stickers = [str(item.get("text") or "") for item in items if item.get("text")]
            grouped_briefs.append({
                "preview_idx": idx,
                "theme": f"{pack['display_name'] or pack['series_name'] or 'External pack'} group {idx}/{len(grouped_items)}",
                "stickers": stickers,
                "external_import": True,
                "ai_enriched": True,
                "grouped_preview": True,
                "source_preview_idx": primary_source_idx,
                "source_preview_id": primary_source_id,
                "source_preview_ids": sorted({
                    int(item.get("source_preview_id") or 0)
                    for item in items
                    if int(item.get("source_preview_id") or 0) > 0
                }),
                "source_image_path": primary_source_path,
                "source_items": [
                    {
                        "source_preview_idx": int(item.get("source_preview_idx") or 0),
                        "source_item_idx": int(item.get("source_item_idx") or 0),
                    }
                    for item in items
                ],
            })
        if not grouped_briefs:
            grouped_briefs = [
                {
                    "preview_idx": int(b.get("source_preview_idx") or b.get("preview_idx") or i),
                    "theme": b.get("theme") or f"Imported preview {i}",
                    "stickers": b.get("stickers") or [],
                    "external_import": True,
                    "ai_enriched": bool(b.get("ai_enriched")),
                    "source_preview_id": b.get("source_preview_id"),
                    "source_image_path": b.get("source_image_path"),
                }
                for i, b in enumerate(source_briefs, 1)
            ]
        now = int(time.time())

        current_style = self._clean_ai_text(pack["style_anchor"], limit=1200)
        ai_style = self._clean_ai_text(" ".join(style_candidates[:3]), limit=1500)
        default_prefix = "External uploaded sticker/card pack named"
        if ai_style and (not current_style or current_style.startswith(default_prefix)):
            style_text = ai_style
        elif ai_style and ai_style not in current_style:
            style_text = f"{current_style}\n\nAI observed style: {ai_style}".strip()
        else:
            style_text = current_style

        current_palette = self._clean_ai_text(pack["palette"], limit=500)
        palette_text = current_palette or self._clean_ai_text("; ".join(palette_candidates[:3]), limit=500)
        current_archetype = self._clean_ai_text(pack["pack_archetype"], limit=80)
        archetype_text = current_archetype
        for candidate in archetype_candidates:
            cand = self._clean_ai_text(candidate, limit=80)
            if cand and cand != "external_upload":
                archetype_text = cand
                break
        archetype_text = archetype_text or "external_upload"

        metadata["external_import"] = True
        metadata["ai_enriched"] = bool(analyses)
        metadata["ai_enriched_at"] = now
        metadata["ai_enrich_errors"] = errors
        metadata["ai_preview_analyses"] = analyses
        metadata["ai_source_preview_briefs"] = source_briefs
        metadata["preview_grouping"] = {
            "enabled": bool(group_previews),
            "target_preview_count": int(target_preview_count or 0),
            "stickers_per_preview": int(stickers_per_preview or 0),
            "actual_preview_count": len(grouped_briefs),
            "generation_method": "local_crop_compose" if group_previews else "source_preview",
            "local_crop_warnings": local_crop_warnings,
        }
        metadata["recommended_total_stickers"] = total_expected
        suggested_pack_name = ""
        if pack_name_candidates:
            suggested_pack_name = self._clean_ai_text(pack_name_candidates[0], limit=160)
            metadata["ai_suggested_pack_name"] = suggested_pack_name
        should_apply_ai_name = (
            bool(metadata.get("auto_named"))
            and bool(suggested_pack_name)
            and suggested_pack_name.casefold() != str(pack["display_name"] or "").casefold()
        )

        generated_previews: list[dict[str, Any]] = []
        preview_generation_errors: list[dict[str, Any]] = []
        if group_previews and grouped_briefs and total_expected > 0:
            preview_generation_errors.extend(local_crop_errors)
            store = get_pack_store()
            for brief in grouped_briefs:
                preview_idx = int(brief["preview_idx"])
                prompt_text = self._build_external_local_group_prompt(
                    group_idx=preview_idx,
                    group_count=len(grouped_briefs),
                    stickers=list(brief.get("stickers") or []),
                )
                try:
                    crops: list[bytes] = []
                    missing_items: list[dict[str, int]] = []
                    for item in brief.get("source_items") or []:
                        key = (
                            int(item.get("source_preview_idx") or 0),
                            int(item.get("source_item_idx") or 0),
                        )
                        crop = local_crop_map.get(key)
                        if crop:
                            crops.append(crop)
                        else:
                            missing_items.append({
                                "source_preview_idx": key[0],
                                "source_item_idx": key[1],
                            })
                    if missing_items:
                        raise ValueError(f"missing local crops: {missing_items[:5]}")
                    out_bytes = self._compose_external_group_preview(crops)
                    img_path = store.write_preview(
                        pack["pack_uid"],
                        int(pack["series_idx"] or 1),
                        preview_idx,
                        out_bytes,
                        prompt_text=prompt_text,
                    )
                    generated_previews.append({
                        "preview_idx": preview_idx,
                        "image_path": img_path.as_posix(),
                        "prompt_text": prompt_text,
                        "model_used": "external_local_group_preview",
                    })
                except Exception as e:  # noqa: BLE001
                    err = f"{type(e).__name__}: {e}"
                    logger.warning(
                        "external pack #%d local grouped preview #%d failed: %s",
                        pack_id,
                        preview_idx,
                        err,
                    )
                    preview_generation_errors.append({
                        "preview_idx": preview_idx,
                        "error": err[:500],
                    })
        if not group_previews:
            source_briefs_by_idx = {
                int(b.get("source_preview_idx") or b.get("preview_idx") or 0): b
                for b in source_briefs
            }
            for preview in source_previews:
                idx = int(preview["preview_idx"])
                src_brief = source_briefs_by_idx.get(idx) or {}
                generated_previews.append({
                    "preview_idx": idx,
                    "image_path": preview["image_path"],
                    "prompt_text": build_preview_prompt(
                        style_anchor=style_text,
                        palette=palette_text,
                        preview_theme=src_brief.get("theme") or f"Imported preview {idx}",
                        stickers=list(src_brief.get("stickers") or []),
                    ),
                })

        previews_replaced = (
            bool(group_previews)
            and bool(generated_previews)
            and not preview_generation_errors
            and len(generated_previews) == len(grouped_briefs)
        )

        if group_previews:
            effective_briefs = grouped_briefs if previews_replaced else [
                {
                    "preview_idx": int(b.get("source_preview_idx") or b.get("preview_idx") or i),
                    "theme": b.get("theme") or f"Imported preview {i}",
                    "stickers": b.get("stickers") or [],
                    "external_import": True,
                    "ai_enriched": bool(b.get("ai_enriched")),
                    "source_preview_id": b.get("source_preview_id"),
                    "source_image_path": b.get("source_image_path"),
                }
                for i, b in enumerate(source_briefs, 1)
            ]
        else:
            effective_briefs = [
                {
                    "preview_idx": int(b.get("source_preview_idx") or b.get("preview_idx") or i),
                    "theme": b.get("theme") or f"Imported preview {i}",
                    "stickers": b.get("stickers") or [],
                    "external_import": True,
                    "ai_enriched": bool(b.get("ai_enriched")),
                    "source_preview_id": b.get("source_preview_id"),
                    "source_image_path": b.get("source_image_path"),
                }
                for i, b in enumerate(source_briefs, 1)
            ]
        briefs_by_idx = {int(b.get("preview_idx") or 0): b for b in effective_briefs}

        metadata["preview_grouping"]["generated_preview_count"] = len(generated_previews)
        metadata["preview_grouping"]["previews_replaced"] = previews_replaced
        metadata["preview_grouping"]["errors"] = preview_generation_errors
        metadata["preview_briefs"] = effective_briefs

        with _open_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE pack_series
                   SET style_anchor = ?,
                       palette = ?,
                       pack_archetype = ?,
                       metadata_json = ?
                 WHERE id = ?
                """,
                (
                    style_text,
                    palette_text,
                    archetype_text,
                    json.dumps(metadata, ensure_ascii=False),
                    int(pack["series_id"]),
                ),
            )
            if previews_replaced:
                conn.execute("DELETE FROM pack_stickers WHERE preview_id IN (SELECT id FROM pack_previews WHERE series_id = ?)",
                             (int(pack["series_id"]),))
                conn.execute("DELETE FROM pack_previews WHERE series_id = ?", (int(pack["series_id"]),))
                for row in generated_previews:
                    conn.execute(
                        """
                        INSERT INTO pack_previews
                            (series_id, preview_idx, prompt_text, image_path,
                             model_used, generation_status, generated_at)
                        VALUES (?, ?, ?, ?, ?, 'ok', ?)
                        """,
                        (
                            int(pack["series_id"]),
                            int(row["preview_idx"]),
                            row["prompt_text"],
                            row["image_path"],
                            row.get("model_used") or "external_local_group_preview",
                            now,
                        ),
                    )
                conn.execute(
                    """
                    UPDATE packs
                       SET cover_image_path = ?
                     WHERE id = ?
                    """,
                    (generated_previews[0]["image_path"], int(pack_id)),
                )
            conn.execute(
                "UPDATE packs SET total_stickers = ? WHERE id = ?",
                (
                    int(total_expected or int(pack["total_stickers"] or 0)),
                    int(pack_id),
                ),
            )
            if should_apply_ai_name:
                conn.execute(
                    "UPDATE packs SET display_name = ? WHERE id = ?",
                    (suggested_pack_name, int(pack_id)),
                )
                conn.execute(
                    "UPDATE pack_series SET series_name = ? WHERE id = ?",
                    (suggested_pack_name, int(pack["series_id"])),
                )
            replace_preview_ids = [
                int(b.get("source_preview_id") or 0)
                for b in grouped_briefs
                if b.get("stickers") and int(b.get("source_preview_id") or 0) > 0
            ]
            if replace_existing_stickers and split and replace_preview_ids:
                placeholders = ",".join("?" for _ in replace_preview_ids)
                conn.execute(
                    f"DELETE FROM pack_stickers WHERE preview_id IN ({placeholders})",
                    tuple(replace_preview_ids),
                )
            conn.commit()

        if style_text:
            try:
                get_pack_store().write_style_anchor(
                    pack["pack_uid"],
                    int(pack["series_idx"] or 1),
                    style_text,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("failed to write enriched style anchor for pack #%d: %s", pack_id, e)

        split_result = {
            "prepared": 0,
            "attempted": 0,
            "ok": 0,
            "error": 0,
            "errors": [],
            "skipped": 0,
        }
        if split and total_expected > 0:
            from src.services.preview_gen import get_preview_gen_service

            preview_svc = get_preview_gen_service()
            with _open_db(self.db_path) as conn:
                split_previews = conn.execute(
                    """
                    SELECT id, preview_idx, image_path, generation_status
                      FROM pack_previews
                     WHERE series_id = ?
                     ORDER BY preview_idx
                    """,
                    (int(pack["series_id"]),),
                ).fetchall()
            for preview in split_previews:
                brief = briefs_by_idx.get(int(preview["preview_idx"]))
                if not brief or not brief.get("stickers"):
                    split_result["skipped"] += 1
                    continue
                try:
                    prep = preview_svc.prepare_stickers(int(preview["id"]))
                    split_result["prepared"] += int(prep.get("created") or 0)
                    if prep.get("skipped_reason"):
                        split_result["skipped"] += 1
                        continue
                    one = preview_svc.split_pending_for_preview(
                        int(preview["id"]),
                        max_workers=split_workers,
                    )
                    split_result["attempted"] += int(one.get("attempted") or 0)
                    split_result["ok"] += int(one.get("ok") or 0)
                    split_result["error"] += int(one.get("error") or 0)
                    split_result["errors"].extend(one.get("errors") or [])
                except Exception as e:  # noqa: BLE001
                    split_result["error"] += 1
                    split_result["errors"].append({
                        "preview_id": int(preview["id"]),
                        "error": f"{type(e).__name__}: {e}"[:300],
                    })

        logger.info(
            "external pack #%d AI enrichment complete: previews=%d stickers=%d split_ok=%d split_err=%d",
            pack_id,
            len(previews),
            total_expected,
            split_result["ok"],
            split_result["error"],
        )
        return {
            "pack_id": int(pack_id),
            "series_id": int(pack["series_id"]),
            "source_preview_count": len(source_previews),
            "preview_count": len(generated_previews) or len(source_previews),
            "total_stickers": total_expected,
            "ai_ok": len(analyses),
            "ai_errors": errors,
            "grouped_previews": len(generated_previews) if previews_replaced else 0,
            "previews_replaced": previews_replaced,
            "group_preview_errors": preview_generation_errors,
            "split": split_result,
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
            local_products = 0
            local_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'local_products'",
            ).fetchone()
            if local_table:
                local_products = conn.execute(
                    "SELECT COUNT(*) FROM local_products WHERE pack_id = ?", (pack_id,),
                ).fetchone()[0]
            if videos or products or local_products:
                raise ValueError(
                    f"pack #{pack_id} has downstream work "
                    f"({videos} videos, {products} products, "
                    f"{local_products} local products); archive instead"
                )

            try:
                conn.execute("DELETE FROM packs WHERE id = ?", (pack_id,))
            except sqlite3.IntegrityError as e:
                raise ValueError(
                    f"pack #{pack_id} is still referenced by downstream records; archive instead"
                ) from e
            conn.commit()
        return {
            "deleted": True,
            "videos": videos,
            "products": products,
            "local_products": local_products,
        }


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_svc: Optional[PackService] = None


def get_pack_service() -> PackService:
    global _svc
    if _svc is None:
        _svc = PackService()
    return _svc
