#!/usr/bin/env python3
"""Archive legacy SQLite DBs and seed V2 ``hot_topics`` from ``tk_hashtags``.

What it does
------------
1. Copy ``data/agent.db``, ``data/ops.db``, ``data/tiktok_trends.db`` (and
   any -shm / -wal sidecar files) to ``data/backups/legacy_dbs/{timestamp}/``.
2. Seed ``hot_topics`` (source='tiktok_cc') from ``tk_hashtags`` rows that
   already live in ``ops_workbench.db`` (the canonical recent data, written
   by ``trend_fetcher``). Idempotent: rows with a topic_name already present
   under source='tiktok_cc' are skipped.
3. Optionally delete the legacy DB files after backup (--delete-originals).

Why this is one-way: tk_hashtags is the historical TikTok Creative Center
crawl data; hot_topics is the V2 multi-source pool. Keeping the old hashtags
visible in the new UI lets ops compare them against fresh OpenAI/Tavily
results without losing the historical work.

Usage::

    python scripts/migrate_legacy_dbs.py --dry-run        # preview only
    python scripts/migrate_legacy_dbs.py                   # backup + seed
    python scripts/migrate_legacy_dbs.py --delete-originals  # also remove old files
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_OPS_DB = Path("data/ops_workbench.db")
LEGACY_DBS = [
    Path("data/agent.db"),
    Path("data/ops.db"),
    Path("data/tiktok_trends.db"),
]
BACKUP_ROOT = Path("data/backups/legacy_dbs")


def _sidecar_files(db_path: Path) -> list[Path]:
    """Return the db file and its WAL/SHM sidecars that exist."""
    candidates = [db_path, Path(str(db_path) + "-shm"), Path(str(db_path) + "-wal")]
    return [p for p in candidates if p.is_file()]


def backup_legacy_dbs(dry_run: bool) -> Path | None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_ROOT / ts
    print(f"[backup] target dir: {target}")

    files_to_copy: list[Path] = []
    for db in LEGACY_DBS:
        files = _sidecar_files(db)
        if not files:
            print(f"  skip {db} (not present)")
            continue
        files_to_copy.extend(files)

    if not files_to_copy:
        print("  nothing to back up")
        return None

    if dry_run:
        for f in files_to_copy:
            print(f"  would copy {f}")
        return target

    target.mkdir(parents=True, exist_ok=True)
    for f in files_to_copy:
        shutil.copy2(f, target / f.name)
        print(f"  copied {f.name}")
    return target


def _parse_unix_from_iso(iso_str: str | None) -> int:
    """Convert an isoformat timestamp to unix epoch seconds.

    Falls back to ``time.time()`` for missing or unparseable input — these
    are legacy rows where preserving an approximate fetched_at is more
    useful than blocking the migration.
    """
    if not iso_str:
        return int(time.time())
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return int(time.time())


def seed_hot_topics_from_tk_hashtags(conn: sqlite3.Connection, dry_run: bool) -> int:
    """Insert rows from tk_hashtags into hot_topics that aren't already seeded."""
    rows = conn.execute(
        """
        SELECT hashtag_id, hashtag_name, video_views, publish_cnt,
               list_data_json, detail_data_json, crawled_at
        FROM tk_hashtags
        """
    ).fetchall()
    print(f"[seed] tk_hashtags source rows: {len(rows)}")

    existing = {
        r[0]
        for r in conn.execute(
            "SELECT topic_name FROM hot_topics WHERE source = 'tiktok_cc'"
        ).fetchall()
    }
    print(f"[seed] hot_topics already has {len(existing)} rows from source='tiktok_cc'")

    inserted = 0
    for hid, name, views, cnt, list_json, detail_json, crawled_at in rows:
        if name in existing:
            continue
        raw: dict = {
            "hashtag_id": hid,
            "publish_cnt": cnt,
        }
        if list_json:
            try:
                raw["list_data"] = json.loads(list_json)
            except Exception:
                raw["list_data_raw"] = list_json
        if detail_json:
            try:
                raw["detail_data"] = json.loads(detail_json)
            except Exception:
                raw["detail_data_raw"] = detail_json

        if not dry_run:
            conn.execute(
                """
                INSERT INTO hot_topics
                    (source, query, topic_name, raw_payload, evidence_urls,
                     hot_score, region, fetched_at, status)
                VALUES ('tiktok_cc', '', ?, ?, '[]', ?, '', ?, 'pending')
                """,
                (
                    name,
                    json.dumps(raw, ensure_ascii=False),
                    float(views or 0),
                    _parse_unix_from_iso(crawled_at),
                ),
            )
        inserted += 1

    if not dry_run:
        conn.commit()
    return inserted


def delete_legacy_files(dry_run: bool) -> None:
    for db in LEGACY_DBS:
        for f in _sidecar_files(db):
            if dry_run:
                print(f"[delete] would remove {f}")
            else:
                f.unlink()
                print(f"[delete] removed {f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ops-db", type=Path, default=DEFAULT_OPS_DB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--delete-originals",
        action="store_true",
        help="Remove legacy DB files (and sidecars) after successful backup",
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip backup step (use only when you've already backed up manually)",
    )
    args = parser.parse_args()

    if not args.ops_db.is_file():
        print(f"ops DB not found: {args.ops_db}", file=sys.stderr)
        return 1

    print("=== Legacy DB migration ===")
    print(f"target ops DB:   {args.ops_db}")
    print(f"dry run:         {args.dry_run}")
    print(f"delete originals:{args.delete_originals}")
    print()

    if not args.skip_backup:
        backup_legacy_dbs(args.dry_run)
        print()
    else:
        print("[backup] skipped (--skip-backup)\n")

    conn = sqlite3.connect(str(args.ops_db), timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        n = seed_hot_topics_from_tk_hashtags(conn, args.dry_run)
        verb = "would insert" if args.dry_run else "inserted"
        print(f"[seed] {verb} {n} new hot_topics rows")
    finally:
        conn.close()
    print()

    if args.delete_originals:
        delete_legacy_files(args.dry_run)
        print()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
