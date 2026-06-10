#!/usr/bin/env python3
"""Rerun sticker splitting for selected packs using the current split service."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.preview_gen.service import PreviewGenService


DB_PATH = Path("data/ops_workbench.db")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pack_ids", nargs="+", type=int)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    placeholders = ",".join("?" for _ in args.pack_ids)
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.display_name, pp.id, pp.preview_idx
              FROM packs p
              JOIN pack_previews pp ON pp.series_id = p.series_id
             WHERE p.id IN ({placeholders})
               AND pp.generation_status = 'ok'
             ORDER BY p.id, pp.preview_idx
            """,
            args.pack_ids,
        ).fetchall()

    svc = PreviewGenService()
    print("previews:", [(r[0], r[2], r[3]) for r in rows], flush=True)
    results = []
    for pack_id, _name, preview_id, preview_idx in rows:
        result = svc.split_pending_for_preview(
            int(preview_id),
            max_workers=max(1, int(args.workers)),
        )
        item = {
            "pack_id": int(pack_id),
            "preview_id": int(preview_id),
            "preview_idx": int(preview_idx),
            **result,
        }
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)

    print("RESULT", json.dumps(results, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
