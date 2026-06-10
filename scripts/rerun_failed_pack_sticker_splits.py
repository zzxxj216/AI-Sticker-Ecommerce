#!/usr/bin/env python3
"""Rerun failed sticker splits for selected packs."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.preview_gen.service import PreviewGenService


DB_PATH = Path("data/ops_workbench.db")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pack_ids", nargs="+", type=int)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    placeholders = ",".join("?" for _ in args.pack_ids)
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, pp.preview_idx, ps.id, ps.sticker_idx
              FROM packs p
              JOIN pack_previews pp ON pp.series_id = p.series_id
              JOIN pack_stickers ps ON ps.preview_id = pp.id
             WHERE p.id IN ({placeholders})
               AND ps.generation_status = 'error'
             ORDER BY p.id, pp.preview_idx, ps.sticker_idx
            """,
            args.pack_ids,
        ).fetchall()

    svc = PreviewGenService()
    print("failed stickers:", rows, flush=True)
    workers = max(1, int(args.workers))
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(svc.regenerate_sticker, int(sticker_id)): row
            for row in rows
            for sticker_id in [row[2]]
        }
        for future in as_completed(futures):
            pack_id, preview_idx, sticker_id, sticker_idx = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = {"status": "error", "error": str(exc)}
            item = {
                "pack_id": int(pack_id),
                "preview_idx": int(preview_idx),
                "sticker_id": int(sticker_id),
                "sticker_idx": int(sticker_idx),
                **result,
            }
            results.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)

    ok = sum(1 for r in results if r.get("status") == "ok")
    err = len(results) - ok
    print(json.dumps({"attempted": len(results), "ok": ok, "error": err}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
