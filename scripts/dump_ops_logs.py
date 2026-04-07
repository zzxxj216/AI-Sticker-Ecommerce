"""Print recent sys_task_jobs and sys_task_logs from ops_workbench.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ops_workbench.db"


def main() -> None:
    if not DB.exists():
        print(f"DB not found: {DB}")
        return
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    print("=== sys_task_jobs (last 15) ===")
    for row in c.execute(
        "SELECT id, job_type, status, started_at, completed_at "
        "FROM sys_task_jobs ORDER BY started_at DESC LIMIT 15"
    ):
        print(dict(row))
    print()
    print("=== sys_task_logs (last 50, newest first) ===")
    for row in c.execute(
        "SELECT id, job_id, log_level, message, created_at "
        "FROM sys_task_logs ORDER BY id DESC LIMIT 50"
    ):
        d = dict(row)
        msg = (d.get("message") or "")[:240]
        print(f"[{d.get('created_at')}] {d.get('job_id')} | {d.get('log_level')}: {msg}")
    print()
    print("=== trend_brief_gen_logs (last 40, newest first) ===")
    try:
        for row in c.execute(
            "SELECT id, trend_id, log_level, source, message, created_at "
            "FROM trend_brief_gen_logs ORDER BY id DESC LIMIT 40"
        ):
            d = dict(row)
            msg = (d.get("message") or "")[:200]
            print(
                f"[{d.get('created_at')}] {d.get('trend_id')} | "
                f"{d.get('source')} | {d.get('log_level')}: {msg}"
            )
    except sqlite3.OperationalError as e:
        print("(table missing or error)", e)
    c.close()


if __name__ == "__main__":
    main()
