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
from io import BytesIO
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

    @staticmethod
    def _detect_sticker_boxes(preview_path: Path, expected_count: int) -> list[tuple[int, int, int, int]]:
        """Detect separated sticker regions on a light preview background.

        This is deliberately non-generative: it only finds bounding boxes in
        the existing pixels so split stickers do not get redrawn by an image
        model. The grouped previews are laid out on a clean white background,
        which makes connected-component detection reliable enough for this
        workflow.
        """
        import cv2  # type: ignore
        import numpy as np

        image = cv2.imread(str(preview_path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError(f"cannot read preview image: {preview_path}")
        if image.ndim != 3:
            raise ValueError("preview image must be RGB/RGBA")

        if image.shape[2] == 4:
            bgr = image[:, :, :3]
            alpha = image[:, :, 3]
        else:
            bgr = image
            alpha = np.full(image.shape[:2], 255, dtype=np.uint8)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]

        edge = max(3, min(12, min(width, height) // 80))
        border_pixels = np.concatenate(
            [
                rgb[:edge, :, :].reshape(-1, 3),
                rgb[-edge:, :, :].reshape(-1, 3),
                rgb[:, :edge, :].reshape(-1, 3),
                rgb[:, -edge:, :].reshape(-1, 3),
            ],
            axis=0,
        )
        bg = np.median(border_pixels, axis=0)
        dist = np.linalg.norm(rgb.astype(np.int16) - bg.astype(np.int16), axis=2)
        mask = ((dist > 22) & (alpha > 10)).astype(np.uint8) * 255

        # Fine-grained components: only a light close so each sticker breaks
        # into a handful of pieces (artwork + caption text) without bridging
        # neighbouring stickers. Dense 7x7-style sheets fail with one big
        # morphology kernel — neighbours merge long before captions attach.
        small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, small)
        k = max(3, min(width, height) // 300) | 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
        tiny = width * height * 0.00005
        fine: list[tuple[int, int, int, int]] = []
        for i in range(1, count):
            x, y, w, h, area = (int(v) for v in stats[i])
            if area < tiny:
                continue
            if w > width * 0.92 and h > height * 0.92:
                continue
            fine.append((x, y, x + w, y + h))
        if not fine:
            raise ValueError("local sticker detection found no regions")
        if len(fine) > 1500:
            fine.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
            fine = fine[:1500]

        def rect_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
            dx = max(a[0] - b[2], b[0] - a[2], 0)
            dy = max(a[1] - b[3], b[1] - a[3], 0)
            return max(dx, dy)

        def union(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
            return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

        # Pre-merge touching/overlapping pieces (union-find on gap <= 1px).
        parent = list(range(len(fine)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(fine)):
            for j in range(i + 1, len(fine)):
                if rect_gap(fine[i], fine[j]) <= 1:
                    parent[find(i)] = find(j)
        grouped: dict[int, tuple[int, int, int, int]] = {}
        for i, box in enumerate(fine):
            root = find(i)
            grouped[root] = union(grouped[root], box) if root in grouped else box
        clusters = list(grouped.values())

        # Agglomerative merge: repeatedly join the cheapest pair (smallest
        # gap, penalised when the merged box would balloon past the median
        # sticker size) until exactly expected_count clusters remain. With no
        # expected count, stop once the nearest pair is a real gutter apart.
        max_gap_allowed = min(width, height) * 0.06
        default_stop_gap = max(4.0, min(width, height) * 0.012)
        target = expected_count if expected_count > 0 else 1
        while len(clusters) > target:
            dims = sorted(max(c[2] - c[0], c[3] - c[1]) for c in clusters)
            med = dims[len(dims) // 2]
            best_cost: float | None = None
            best_gap = 0
            bi = bj = -1
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    a, b = clusters[i], clusters[j]
                    g = rect_gap(a, b)
                    if g > max_gap_allowed:
                        continue
                    m = union(a, b)
                    oversize = max(0, max(m[2] - m[0], m[3] - m[1]) - med * 1.35)
                    cost = g + oversize * 0.6
                    if best_cost is None or cost < best_cost:
                        best_cost, best_gap, bi, bj = cost, g, i, j
            if best_cost is None:
                break
            if expected_count <= 0 and best_gap > default_stop_gap:
                break
            merged = union(clusters[bi], clusters[bj])
            clusters = [c for idx, c in enumerate(clusters) if idx not in (bi, bj)] + [merged]

        boxes_raw = [c for c in clusters
                     if (c[2] - c[0]) >= 12 and (c[3] - c[1]) >= 12]
        if not boxes_raw:
            raise ValueError("local sticker detection found no regions")
        if expected_count > 0 and len(boxes_raw) != expected_count:
            logger.warning(
                "local sticker detection found %d regions; expected %d for %s",
                len(boxes_raw),
                expected_count,
                preview_path,
            )

        # Reading order: cluster rows by y-centre (tolerance from median
        # sticker height), then left-to-right inside each row — must match
        # the AI analysis listing order so crops map to the right briefs.
        heights = sorted(c[3] - c[1] for c in boxes_raw)
        row_tol = max(8, int(heights[len(heights) // 2] * 0.6))
        boxes_raw.sort(key=lambda c: (c[1] + c[3]) // 2)
        rows: list[list[tuple[int, int, int, int]]] = []
        row_centers: list[float] = []
        for c in boxes_raw:
            cy = (c[1] + c[3]) / 2
            if rows and abs(cy - row_centers[-1]) <= row_tol:
                rows[-1].append(c)
                row_centers[-1] = sum((b[1] + b[3]) / 2 for b in rows[-1]) / len(rows[-1])
            else:
                rows.append([c])
                row_centers.append(cy)

        pad = max(10, int(min(width, height) * 0.012))
        boxes: list[tuple[int, int, int, int]] = []
        for row in rows:
            for x1, y1, x2, y2 in sorted(row, key=lambda c: c[0]):
                boxes.append((
                    max(0, x1 - pad),
                    max(0, y1 - pad),
                    min(width, x2 + pad),
                    min(height, y2 + pad),
                ))
        return boxes

    @staticmethod
    def _crop_sticker_bytes(preview_path: Path, box: tuple[int, int, int, int]) -> bytes:
        from PIL import Image, ImageChops

        with Image.open(preview_path) as source:
            image = source.convert("RGBA")
            crop = image.crop(box)
            # Make the white sheet background transparent while preserving the
            # exact sticker pixels. The threshold is intentionally conservative.
            bg = Image.new("RGBA", crop.size, (255, 255, 255, 255))
            diff = ImageChops.difference(crop, bg).convert("L")
            alpha = diff.point(lambda p: 0 if p < 18 else 255)
            crop.putalpha(alpha)
            out = BytesIO()
            crop.save(out, format="PNG")
            return out.getvalue()

    @staticmethod
    def _normalize_split_output(image_bytes: bytes) -> bytes:
        """Flatten transparency to white and trim dark border bars.

        gpt-image-2 edit output sometimes comes back with an alpha channel
        (which turns black in any later RGB conversion / dark viewer) or
        with black letterbox bars around the sticker. Both read as 黑边 in
        the UI, so normalize every split result to a clean white-backed PNG.
        """
        from PIL import Image, ImageChops

        with Image.open(BytesIO(image_bytes)) as src:
            rgba = src.convert("RGBA")
        flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        flat.alpha_composite(rgba)
        rgb = flat.convert("RGB")

        width, height = rgb.size
        corners = [
            rgb.getpixel((0, 0)),
            rgb.getpixel((width - 1, 0)),
            rgb.getpixel((0, height - 1)),
            rgb.getpixel((width - 1, height - 1)),
        ]
        border = max(set(corners), key=corners.count)
        if sum(abs(c - 255) for c in border) > 30:
            # Non-white border (black bars / dark frame): crop to content,
            # then re-center on a white square canvas with a small margin.
            bg = Image.new("RGB", rgb.size, border)
            diff = ImageChops.difference(rgb, bg).convert("L")
            bbox = diff.point(lambda p: 255 if p > 16 else 0).getbbox()
            if bbox and (bbox[2] - bbox[0]) > 8 and (bbox[3] - bbox[1]) > 8:
                content = rgb.crop(bbox)
                side = max(content.size)
                pad = max(16, side // 20)
                canvas = Image.new("RGB", (side + pad * 2, side + pad * 2),
                                   (255, 255, 255))
                canvas.paste(content,
                             ((canvas.width - content.width) // 2,
                              (canvas.height - content.height) // 2))
                rgb = canvas

        out = BytesIO()
        rgb.save(out, format="PNG")
        return out.getvalue()

    def _split_one_with_retry(self, sticker_id: int, size: str) -> dict[str, Any]:
        """image_edit one sticker. Marks status, writes file, updates row.

        Sticker splitting intentionally uses image_edit for every sticker so
        sheet layouts with touching/merged sticker regions do not fail local
        connected-component detection.
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

        global_idx = int(row["preview_id"]) * 1000 + int(row["sticker_idx"])
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
                try:
                    image_bytes = self._normalize_split_output(image_bytes)
                except Exception as norm_err:  # noqa: BLE001
                    logger.warning("sticker #%d output normalize failed (%s); "
                                   "keeping raw model output", sticker_id, norm_err)
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
    # One-click repair: retry failed previews + failed sticker splits
    # ------------------------------------------------------------------

    def series_failure_summary(self, series_id: int) -> dict[str, int]:
        """Count failed previews + failed sticker splits for a series (drives
        the repair button's visibility/label)."""
        with _open_db(self.db_path) as conn:
            prev_err = conn.execute(
                "SELECT COUNT(*) FROM pack_previews "
                "WHERE series_id = ? AND generation_status = 'error'",
                (series_id,),
            ).fetchone()[0]
            stk_err = conn.execute(
                """
                SELECT COUNT(*) FROM pack_stickers ps
                  JOIN pack_previews pp ON pp.id = ps.preview_id
                 WHERE pp.series_id = ? AND ps.generation_status = 'error'
                """,
                (series_id,),
            ).fetchone()[0]
        return {"preview_failures": int(prev_err), "sticker_failures": int(stk_err),
                "total": int(prev_err) + int(stk_err)}

    def repair_series(
        self,
        series_id: int,
        *,
        max_workers: int = DEFAULT_MAX_WORKERS,
        size: str = DEFAULT_IMAGE_SIZE,
    ) -> dict[str, Any]:
        """One-click repair for a series:
          1. Re-run every failed / pending PREVIEW (``generate_pending_for_series``).
          2. For previews that are now ``ok`` AND already have sticker rows in
             error/pending state, re-run those SPLITS only.

        Deliberately does NOT split previews that were never split (that's
        'generate', not 'repair') — it only fixes what was attempted and failed.
        Returns ``{'previews': {...}, 'stickers': {...}}``.
        """
        prev_res = self.generate_pending_for_series(
            series_id, max_workers=max_workers, size=size)

        with _open_db(self.db_path) as conn:
            prev_ids = [r[0] for r in conn.execute(
                """
                SELECT DISTINCT pp.id
                  FROM pack_previews pp
                  JOIN pack_stickers ps ON ps.preview_id = pp.id
                 WHERE pp.series_id = ?
                   AND pp.generation_status = 'ok'
                   AND COALESCE(pp.image_path, '') != ''
                   AND ps.generation_status IN ('pending', 'error')
                 ORDER BY pp.id
                """,
                (series_id,),
            ).fetchall()]

        st_attempted = st_ok = st_err = 0
        st_errors: list[dict] = []
        for pid in prev_ids:
            try:
                r = self.split_pending_for_preview(pid, max_workers=max_workers, size=size)
                st_attempted += r.get("attempted", 0)
                st_ok += r.get("ok", 0)
                st_err += r.get("error", 0)
                st_errors.extend(r.get("errors", []))
            except Exception as e:  # noqa: BLE001
                st_err += 1
                st_errors.append({"preview_id": pid, "error": str(e)[:200]})

        logger.info("repair_series #%s: previews ok=%s err=%s | stickers ok=%s err=%s",
                    series_id, prev_res.get("ok"), prev_res.get("error"), st_ok, st_err)
        return {
            "previews": prev_res,
            "stickers": {"attempted": st_attempted, "ok": st_ok,
                         "error": st_err, "errors": st_errors[:20]},
        }

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
