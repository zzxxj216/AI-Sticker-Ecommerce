"""Shopify product image generator — a FIXED 5-image AI image-to-image set.

Unlike the TikTok pipeline (``tkshop.service.auto_design_images``, which lets a
model pick secondary roles), Shopify gets a deterministic, on-brand set defined
by ``shopify.image_style.SHOPIFY_IMAGE_STYLE``:

  1. overview  (main)      — all designs on a clean pure-white background
  2. laptop    (secondary) — stickers on a laptop / notebook
  3. bottle    (secondary) — stickers on a stainless water bottle
  4. handhold  (secondary) — a hand peeling one die-cut sticker, pile behind
  5. flatlay   (secondary) — top-down arrangement with a coin for scale

Stored in the dedicated ``shopify_product_images`` table (migration 026), kept
separate from TikTok's ``tkshop_product_images`` / ``local_product_images``.

Design choices (mirrors patterns proven in the tkshop image designer):
  - Reference is ALWAYS the pack's REAL stickers (image-to-image), never
    text-to-image. ``ref=='grid'`` edits a merged reference grid of all
    stickers; ``ref=='single'`` rotates through individual stickers by index.
  - Generation is STRICTLY SEQUENTIAL — the JieKou image-edit endpoint drops
    connections under concurrency. Slow is fine.
  - Per-image QA: decodable + ``min(w, h) >= 512``; one retry on failure.
  - Per-image failures are collected, never raised; only structural problems
    (product not found / no reference stickers) raise.
  - Files saved to ``output/packs/{pack_uid}/products/shopify/images/{key}.png``
    and the stored ``local_path`` is repo-relative (contains ``output/packs/``)
    so the web layer's ``_local_path_to_public_url`` maps it to ``/v2-outputs``.

Logging is ASCII-only (this Windows host's console is GBK).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from src.core.logger import get_logger
from src.services.ai.router import get_router
from src.services.shopify.image_style import SHOPIFY_IMAGE_STYLE, style_prompt
from src.utils.image_utils import (
    compose_reference_grid,
    compress_image_bytes_for_api,
    read_dimensions,
)

logger = get_logger("service.shopify.images")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH = "data/ops_workbench.db"

# Where Shopify-specific images live on disk (separate from TikTok's images/).
_IMAGE_SUBDIR = "products/shopify/images"

# QA gate: output must decode and have a short side >= this.
_MIN_SHORT_SIDE = 512


# ---------------------------------------------------------------------------
# DB helper (mirrors shopify.sync._open_db; intentionally decoupled)
# ---------------------------------------------------------------------------

def _open_db(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _repo_root() -> Path:
    """Repo root = three levels up from this file (src/services/shopify/)."""
    return Path(__file__).resolve().parents[3]


def _resolve_disk_path(raw: str, root: Path) -> Path:
    """Resolve a stored (possibly repo-relative) path to an absolute disk path."""
    p = Path(raw)
    if p.is_absolute():
        return p
    return (root / p).resolve()


# ---------------------------------------------------------------------------
# Reference collection
# ---------------------------------------------------------------------------

def _collect_pack_info(conn: sqlite3.Connection, local_product_id: int) -> dict[str, Any]:
    """Return {local_product_id, pack_id, pack_uid, display_name} for a product.

    Raises ValueError('local product not found') when the row is missing.
    """
    row = conn.execute(
        """
        SELECT lp.id AS local_product_id, lp.pack_id,
               p.pack_uid, p.display_name
          FROM local_products lp
          LEFT JOIN packs p ON p.id = lp.pack_id
         WHERE lp.id = ?
        """,
        (local_product_id,),
    ).fetchone()
    if not row:
        raise ValueError("local product not found")
    return dict(row)


def _collect_sticker_paths(
    conn: sqlite3.Connection, info: dict[str, Any], root: Path,
) -> list[Path]:
    """Collect the pack's REAL sticker image paths as the image-to-image refs.

    Strategy (mirrors the spec): selected/ok pack stickers first; if that yields
    nothing, fall back to local_product_images (main/secondary), then pack
    preview sheets. Only paths that exist on disk are returned.
    """
    candidates: list[str] = []

    pack_id = info.get("pack_id")
    if pack_id:
        rows = conn.execute(
            """
            SELECT ps.image_path
              FROM pack_stickers ps
              JOIN pack_previews pp ON pp.id = ps.preview_id
              JOIN pack_series s ON s.id = pp.series_id
              JOIN packs p ON p.series_id = s.id
             WHERE p.id = ?
               AND ps.generation_status = 'ok'
               AND ps.is_selected = 1
             ORDER BY pp.preview_idx, ps.sticker_idx
            """,
            (pack_id,),
        ).fetchall()
        candidates = [r["image_path"] for r in rows if r["image_path"]]

    def _existing(paths: list[str]) -> list[Path]:
        seen: set[str] = set()
        out: list[Path] = []
        for raw in paths:
            if not raw:
                continue
            p = _resolve_disk_path(raw, root)
            if not p.is_file():
                continue
            key = p.as_posix()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        return out

    resolved = _existing(candidates)
    if resolved:
        return resolved

    # Fallback 1: local_product_images (the product's master gallery).
    rows = conn.execute(
        """
        SELECT local_path FROM local_product_images
         WHERE local_product_id = ? AND role IN ('main', 'secondary')
         ORDER BY (role = 'main') DESC, sort_order, id
        """,
        (info["local_product_id"],),
    ).fetchall()
    resolved = _existing([r["local_path"] for r in rows])
    if resolved:
        logger.info(
            "shopify_images: lp #%d falling back to %d local_product_images refs",
            info["local_product_id"], len(resolved),
        )
        return resolved

    # Fallback 2: pack preview sheets.
    if pack_id:
        rows = conn.execute(
            """
            SELECT pp.image_path
              FROM pack_previews pp
              JOIN pack_series s ON s.id = pp.series_id
              JOIN packs p ON p.series_id = s.id
             WHERE p.id = ?
               AND pp.generation_status = 'ok'
             ORDER BY pp.preview_idx
            """,
            (pack_id,),
        ).fetchall()
        resolved = _existing([r["image_path"] for r in rows])
        if resolved:
            logger.info(
                "shopify_images: lp #%d falling back to %d pack preview refs",
                info["local_product_id"], len(resolved),
            )
    return resolved


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_shopify_images(
    local_product_id: int,
    *,
    replace: bool = True,
    quality_main: str = "high",
    quality_secondary: str = "medium",
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """Generate the fixed Shopify image set for a local product.

    For each item in ``SHOPIFY_IMAGE_STYLE`` (overview/laptop/bottle/handhold/
    flatlay), in order:
      - pick reference bytes: ``ref=='grid'`` -> a merged grid of ALL sticker
        paths; ``ref=='single'`` -> one sticker (rotated by item index).
      - call ``get_router().image_edit(ref, style_prompt(item), size='1024x1024',
        quality=quality_main if role=='main' else quality_secondary,
        task='shopify_image:edit', related_table='shopify_product_images',
        related_id=local_product_id)``.
      - QA: decodable + ``min(w, h) >= 512``; on failure retry once.
      - save bytes to
        ``output/packs/{pack_uid}/products/shopify/images/{key}.png``.
      - insert a ``shopify_product_images`` row.

    If ``replace`` is True, existing rows for this product are deleted (files
    best-effort unlinked) before generating.

    Per-image failures are collected, never raised. Raises only on
    'local product not found' / 'no reference stickers available'.

    Returns ``{ok, local_product_id, generated, failed, images, errors}``.
    """
    root = _repo_root()
    router = get_router()

    with _open_db(db_path) as conn:
        info = _collect_pack_info(conn, local_product_id)
        sticker_paths = _collect_sticker_paths(conn, info, root)

    if not sticker_paths:
        raise ValueError("no reference stickers available")

    pack_uid = info.get("pack_uid")
    if not pack_uid:
        # Product has no linked pack/pack_uid; nowhere under output/packs/ to
        # write, and the web layer can't map the URL. Treat as structural.
        raise ValueError("no reference stickers available")

    out_dir = root / "output" / "packs" / pack_uid / _IMAGE_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Merged grid reference (shared by all ref=='grid' items). Compress for the
    # image-edit API the same way the tkshop designer does (avoids transport
    # drops on multi-MiB bodies).
    grid_bytes = compose_reference_grid(
        [p.as_posix() for p in sticker_paths],
        cell=384 if len(sticker_paths) >= 12 else 512,
        max_side=1536,
    )
    grid_bytes = compress_image_bytes_for_api(
        grid_bytes, max_side=1536, max_bytes=1_800_000,
    )

    # Single-sticker reference bytes cache (compressed on read).
    _single_cache: dict[str, bytes] = {}

    def _single_ref(idx: int) -> bytes:
        pick = sticker_paths[idx % len(sticker_paths)]
        key = pick.as_posix()
        if key not in _single_cache:
            _single_cache[key] = compress_image_bytes_for_api(
                pick.read_bytes(), max_side=1536, max_bytes=1_800_000,
            )
        return _single_cache[key]

    def _qa_ok(out_bytes: bytes) -> Optional[str]:
        try:
            w, h = read_dimensions(out_bytes)
        except Exception as e:  # noqa: BLE001 - any decode failure
            return f"undecodable ({type(e).__name__})"
        if min(w, h) < _MIN_SHORT_SIDE:
            return f"too small ({w}x{h})"
        return None

    # Replace: clear prior rows + best-effort unlink their files.
    if replace:
        with _open_db(db_path) as conn:
            rows = conn.execute(
                "SELECT id, local_path FROM shopify_product_images "
                "WHERE local_product_id = ?",
                (local_product_id,),
            ).fetchall()
            if rows:
                conn.execute(
                    "DELETE FROM shopify_product_images WHERE local_product_id = ?",
                    (local_product_id,),
                )
                conn.commit()
        for r in rows:
            raw = r["local_path"]
            if not raw:
                continue
            try:
                _resolve_disk_path(raw, root).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 - best effort
                pass
        if rows:
            logger.info(
                "shopify_images: cleared %d prior image(s) for lp #%d",
                len(rows), local_product_id,
            )

    images: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # SEQUENTIAL generation (image_edit drops connections under parallelism).
    for idx, item in enumerate(SHOPIFY_IMAGE_STYLE):
        key = item["key"]
        role = item["role"]
        ref_kind = item.get("ref", "grid")
        prompt = style_prompt(item)
        quality = quality_main if role == "main" else quality_secondary
        ref_bytes = grid_bytes if ref_kind == "grid" else _single_ref(idx)

        out_bytes: Optional[bytes] = None
        last_err = ""
        for attempt in (1, 2):  # generate + one retry
            try:
                candidate = router.image_edit(
                    ref_bytes,
                    prompt,
                    size="1024x1024",
                    quality=quality,
                    task="shopify_image:edit",
                    related_table="shopify_product_images",
                    related_id=local_product_id,
                )
            except Exception as e:  # noqa: BLE001 - never raise per-image
                last_err = f"{type(e).__name__}: {e}"[:300]
                logger.warning(
                    "shopify_images: %s edit failed (attempt %d): %s",
                    key, attempt, last_err,
                )
                continue
            reason = _qa_ok(candidate)
            if reason is None:
                out_bytes = candidate
                break
            last_err = f"failed QA: {reason}"
            logger.warning(
                "shopify_images: %s QA rejected (attempt %d): %s",
                key, attempt, reason,
            )

        if out_bytes is None:
            errors.append({"style_key": key, "role": role, "error": last_err})
            continue

        # Save bytes + insert row.
        try:
            disk_path = out_dir / f"{key}.png"
            disk_path.write_bytes(out_bytes)
            rel_path = (
                f"output/packs/{pack_uid}/{_IMAGE_SUBDIR}/{key}.png"
            )
            with _open_db(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO shopify_product_images
                        (local_product_id, role, style_key, source,
                         local_path, sort_order, ai_prompt, created_at)
                    VALUES (?, ?, ?, 'shopify_ai', ?, ?, ?, ?)
                    """,
                    (
                        local_product_id, role, key, rel_path, idx,
                        prompt, int(time.time()),
                    ),
                )
                conn.commit()
            images.append(
                {"style_key": key, "role": role, "local_path": rel_path}
            )
            logger.info(
                "shopify_images: lp #%d generated %s (%s)",
                local_product_id, key, role,
            )
        except Exception as e:  # noqa: BLE001 - never raise per-image
            err = f"save/insert failed: {type(e).__name__}: {e}"[:300]
            errors.append({"style_key": key, "role": role, "error": err})
            logger.warning("shopify_images: %s %s", key, err)

    return {
        "ok": bool(images) and not errors,
        "local_product_id": local_product_id,
        "generated": len(images),
        "failed": len(errors),
        "images": images,
        "errors": errors,
    }


def list_shopify_images(
    local_product_id: int, *, db_path: str = DEFAULT_DB_PATH,
) -> list[dict]:
    """Return ``shopify_product_images`` rows (dicts) for a product, ordered
    main-first then by sort_order, id."""
    with _open_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, local_product_id, role, style_key, source,
                   local_path, sort_order, ai_prompt, created_at
              FROM shopify_product_images
             WHERE local_product_id = ?
             ORDER BY (role = 'main') DESC, sort_order, id
            """,
            (local_product_id,),
        ).fetchall()
    return [dict(r) for r in rows]
