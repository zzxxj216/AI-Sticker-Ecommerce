"""Backfill immutable source preview images for external imports.

External PDF/image imports used to store their rendered overview under
``series_*/previews/*.png``. Later grouped-preview generation may overwrite
those files. This script re-renders the original uploaded source files into
``series_*/source_previews/`` and records the paths in
``metadata_json.source_files[].original_preview_paths``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.packs.service import PackService


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _load_rows(conn: sqlite3.Connection, pack_ids: list[int]) -> list[sqlite3.Row]:
    where = "WHERE s.metadata_json LIKE '%external_import%'"
    params: list[Any] = []
    if pack_ids:
        placeholders = ",".join("?" for _ in pack_ids)
        where += f" AND p.id IN ({placeholders})"
        params.extend(pack_ids)
    return conn.execute(
        f"""
        SELECT p.id AS pack_id, p.pack_uid,
               s.id AS series_id, s.series_idx, s.metadata_json
          FROM packs p
          JOIN pack_series s ON s.id = p.series_id
         {where}
         ORDER BY p.id
        """,
        params,
    ).fetchall()


def backfill(
    *,
    db_path: Path,
    pack_ids: list[int],
    dry_run: bool,
) -> dict[str, Any]:
    svc = PackService(db_path)
    conn = _open_db(db_path)
    rows = _load_rows(conn, pack_ids)
    updated = 0
    written = 0
    skipped = 0
    errors: list[dict[str, Any]] = []

    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            if not isinstance(metadata, dict):
                skipped += 1
                continue

            changed = False
            for source_idx, source in enumerate(metadata.get("source_files") or [], 1):
                if not isinstance(source, dict):
                    continue
                existing = [
                    str(p or "").strip()
                    for p in source.get("original_preview_paths") or []
                    if str(p or "").strip()
                ]
                if existing and all(Path(p).is_file() for p in existing):
                    continue

                source_path = Path(str(source.get("source_path") or ""))
                if not source_path.is_file():
                    skipped += 1
                    errors.append({
                        "pack_id": int(row["pack_id"]),
                        "source_path": source_path.as_posix(),
                        "error": "source file missing",
                    })
                    continue

                suffix = source_path.suffix.lower()
                if suffix == ".pdf":
                    rendered = svc._pdf_pages_to_png_bytes(source_path.read_bytes())
                    labels = [
                        f"{source_path.stem}_page_{i}"
                        for i in range(1, len(rendered) + 1)
                    ]
                elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                    rendered = [svc._image_to_png_bytes(source_path.read_bytes())]
                    labels = [source_path.stem or f"source_{source_idx}"]
                else:
                    skipped += 1
                    errors.append({
                        "pack_id": int(row["pack_id"]),
                        "source_path": source_path.as_posix(),
                        "error": f"unsupported source type: {suffix}",
                    })
                    continue

                paths: list[str] = []
                for page_no, (label, raw) in enumerate(zip(labels, rendered), 1):
                    if dry_run:
                        out = (
                            Path("output/packs")
                            / str(row["pack_uid"])
                            / f"series_{int(row['series_idx'] or 1)}"
                            / "source_previews"
                            / f"{source_idx:02d}_{page_no:02d}_{label}.png"
                        )
                    else:
                        out = svc._write_external_source_preview(
                            pack_uid=row["pack_uid"],
                            series_idx=int(row["series_idx"] or 1),
                            source_idx=source_idx,
                            page_idx=page_no,
                            label=label,
                            image_bytes=raw,
                        )
                    paths.append(out.as_posix())
                    written += 1

                source["original_preview_paths"] = paths
                changed = True

            if changed:
                metadata["source_previews_backfilled_at"] = int(time.time())
                if not dry_run:
                    conn.execute(
                        "UPDATE pack_series SET metadata_json = ? WHERE id = ?",
                        (json.dumps(metadata, ensure_ascii=False), row["series_id"]),
                    )
                updated += 1
        except Exception as exc:  # noqa: BLE001 - report all rows
            errors.append({
                "pack_id": int(row["pack_id"]),
                "error": f"{type(exc).__name__}: {exc}",
            })

    if not dry_run:
        conn.commit()
    conn.close()
    return {
        "db_path": db_path.as_posix(),
        "packs_seen": len(rows),
        "packs_updated": updated,
        "source_previews_written": written,
        "skipped": skipped,
        "dry_run": dry_run,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=Path("data/ops_workbench.db"))
    parser.add_argument(
        "--pack-id",
        type=int,
        action="append",
        default=[],
        help="Only backfill this pack id. Can be provided multiple times.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = backfill(db_path=args.db_path, pack_ids=args.pack_id, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
