#!/usr/bin/env python3
"""SQLite migration runner.

Applies SQL files in ``scripts/migrations/`` in lexical order to a target
database. Tracks applied migrations in a ``schema_migrations`` table so reruns
are idempotent.

Usage::

    python scripts/migrate.py                          # apply to data/ops_workbench.db
    python scripts/migrate.py --db-path data/foo.db    # custom target
    python scripts/migrate.py --dry-run                # show pending only
    python scripts/migrate.py --status                 # list applied vs pending
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB_PATH = Path("data/ops_workbench.db")
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _ensure_tracking_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    TEXT PRIMARY KEY,
            applied_at  INTEGER NOT NULL
        )
        """
    )
    conn.commit()


def _list_migration_files() -> list[Path]:
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def _list_applied(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _split_sql(sql: str) -> list[str]:
    """Split a SQL script into individual statements using sqlite3's parser.

    We avoid ``executescript()`` because it issues an implicit COMMIT before
    running, which breaks our explicit BEGIN..COMMIT wrapping (a partially
    successful script could leave half its tables behind).
    """
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            if stmt:
                statements.append(stmt)
            buf = ""
    if buf.strip():
        raise ValueError(f"Incomplete SQL at end of file: {buf!r}")
    return statements


def _apply_one(conn: sqlite3.Connection, sql_file: Path) -> None:
    statements = _split_sql(sql_file.read_text(encoding="utf-8"))
    try:
        conn.execute("BEGIN")
        for stmt in statements:
            conn.execute(stmt)
        conn.execute(
            "INSERT INTO schema_migrations(filename, applied_at) VALUES (?, ?)",
            (sql_file.name, int(time.time())),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _print_status(applied: set[str], all_files: list[Path]) -> None:
    print(f"DB: {DEFAULT_DB_PATH if not _args.db_path else _args.db_path}")
    print(f"Migrations dir: {MIGRATIONS_DIR}")
    print()
    if not all_files:
        print("(no migration files found)")
        return
    print(f"{'Status':<10} {'Filename'}")
    print(f"{'-' * 10} {'-' * 50}")
    for f in all_files:
        marker = "applied" if f.name in applied else "pending"
        print(f"{marker:<10} {f.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Print pending migrations, do not apply")
    parser.add_argument("--status", action="store_true", help="Print applied/pending status and exit")
    global _args
    _args = parser.parse_args()

    files = _list_migration_files()
    if not files:
        print(f"No migration files in {MIGRATIONS_DIR}", file=sys.stderr)
        return 0

    conn = _open_db(_args.db_path)
    try:
        _ensure_tracking_table(conn)
        applied = _list_applied(conn)

        if _args.status:
            _print_status(applied, files)
            return 0

        pending = [f for f in files if f.name not in applied]
        if not pending:
            print(f"Nothing to apply. {len(applied)} migration(s) already on {_args.db_path}.")
            return 0

        print(f"Target DB: {_args.db_path}")
        print(f"Pending migrations ({len(pending)}):")
        for f in pending:
            print(f"  - {f.name}")

        if _args.dry_run:
            print("\n--dry-run set; no changes made.")
            return 0

        for f in pending:
            print(f"Applying {f.name} ...", end=" ", flush=True)
            _apply_one(conn, f)
            print("OK")

        print(f"\nDone. Applied {len(pending)} migration(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
