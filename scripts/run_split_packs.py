#!/usr/bin/env python3
"""Split / retry failed stickers for packs by pack_id."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.services.preview_gen.service import get_preview_gen_service  # noqa: E402


def split_stickers_for_series(series_id: int) -> dict:
    svc = get_preview_gen_service()
    series = svc.get_series_with_previews(series_id)
    if not series:
        raise ValueError(f"pack_series #{series_id} not found")
    prepared = attempted = ok = error = skipped = 0
    for preview in series.get("previews", []):
        if preview.get("generation_status") != "ok":
            skipped += 1
            continue
        prep = svc.prepare_stickers(int(preview["id"]))
        prepared += int(prep.get("created") or 0)
        result = svc.split_pending_for_preview(int(preview["id"]))
        attempted += int(result.get("attempted") or 0)
        ok += int(result.get("ok") or 0)
        error += int(result.get("error") or 0)
    return {
        "prepared": prepared,
        "attempted": attempted,
        "ok": ok,
        "error": error,
        "skipped_previews": skipped,
    }


def series_for_pack(db: Path, pack_id: int) -> int:
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT series_id FROM packs WHERE id=?", (pack_id,)).fetchone()
    conn.close()
    if not row or not row[0]:
        raise SystemExit(f"pack #{pack_id} not found or has no series_id")
    return int(row[0])


def summary(db: Path, series_id: int) -> dict:
    conn = sqlite3.connect(db)
    rows = conn.execute(
        """
        SELECT ps.generation_status, COUNT(*)
          FROM pack_stickers ps
          JOIN pack_previews pp ON pp.id = ps.preview_id
         WHERE pp.series_id = ?
         GROUP BY ps.generation_status
        """,
        (series_id,),
    ).fetchall()
    conn.close()
    return {status: count for status, count in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack_ids", nargs="+", type=int)
    args = parser.parse_args()
    db = ROOT / "data" / "ops_workbench.db"
    out = {"packs": []}
    t0 = time.time()
    for pack_id in args.pack_ids:
        series_id = series_for_pack(db, pack_id)
        before = summary(db, series_id)
        print(json.dumps({"pack_id": pack_id, "series_id": series_id, "before": before}, ensure_ascii=False))
        result = split_stickers_for_series(series_id)
        after = summary(db, series_id)
        rec = {
            "pack_id": pack_id,
            "series_id": series_id,
            "before": before,
            "split": result,
            "after": after,
        }
        out["packs"].append(rec)
        print(json.dumps(rec, ensure_ascii=False, indent=2))
    out["elapsed_s"] = round(time.time() - t0, 1)
    print(json.dumps({"done": True, **out}, ensure_ascii=False, indent=2))
    fail = sum(
        rec["after"].get("error", 0) + rec["after"].get("pending", 0)
        for rec in out["packs"]
    )
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
