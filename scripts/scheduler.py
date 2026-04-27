#!/usr/bin/env python3
"""V2 background scheduler.

Standalone process that runs the periodic jobs the V2 pipeline needs.
Started alongside the web process via ``start.sh``; designed to be
killed with Ctrl-C / SIGTERM cleanly.

Jobs registered today:
  tk_metrics_refresh         every 2h    - refresh TK video metrics (W3 / B.2)
  tk_video_publish_dispatch  every 60s   - send scheduled videos       (W3 / B.1)
  tkshop_status_sync         every 30min - sync product statuses        (W4 / C.4)

The job bodies are stubs at this stage — they only log + record a row
in ``scheduled_jobs``. Real implementations land in their respective
W3 / W4 tasks.

Logging contract:
  Each job invocation inserts a 'running' row into scheduled_jobs on
  start, then updates it to 'ok' / 'error' on finish with affected_rows
  and error message. Lets the web UI show "last refresh at ..." and
  "next refresh ETA" by querying the table.

Missed-job recovery:
  At startup we look at the most recent scheduled_jobs row for each
  registered job. If now - last_finished_at > interval, we kick the job
  immediately rather than waiting a full interval.

Usage::

    python scripts/scheduler.py                  # run forever
    python scripts/scheduler.py --run-once X     # run job X once and exit
    python scripts/scheduler.py --list           # list registered jobs
    python scripts/scheduler.py --db-path ...    # custom DB
"""

from __future__ import annotations

import argparse
import logging
import signal
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# Re-use project root logger if available, else basicConfig.
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.core.logger import get_logger
    logger = get_logger("scheduler")
except Exception:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger("scheduler")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")


# ----------------------------------------------------------------------
# scheduled_jobs persistence
# ----------------------------------------------------------------------

def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@dataclass
class JobResult:
    """Returned by job callbacks. ``affected_rows`` is informational only."""
    affected_rows: int = 0
    note: str = ""


class JobContext:
    """Wraps a job invocation: insert 'running' row, update on exit."""

    def __init__(self, job_name: str, db_path: Path):
        self.job_name = job_name
        self.db_path = db_path
        self._row_id: Optional[int] = None
        self.affected_rows = 0

    def __enter__(self) -> "JobContext":
        try:
            conn = _open_db(self.db_path)
            cur = conn.execute(
                """
                INSERT INTO scheduled_jobs (job_name, started_at, status)
                VALUES (?, ?, 'running')
                """,
                (self.job_name, int(time.time())),
            )
            self._row_id = cur.lastrowid
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("[%s] failed to insert scheduled_jobs row: %s",
                           self.job_name, e)
            self._row_id = None
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            status, err = "ok", ""
        else:
            status, err = "error", (str(exc) or exc.__class__.__name__)[:1000]
            logger.exception("[%s] job raised: %s", self.job_name, err)

        if self._row_id is None:
            return False

        try:
            conn = _open_db(self.db_path)
            conn.execute(
                """
                UPDATE scheduled_jobs
                   SET finished_at = ?,
                       status = ?,
                       affected_rows = ?,
                       error = ?
                 WHERE id = ?
                """,
                (
                    int(time.time()),
                    status,
                    self.affected_rows,
                    err,
                    self._row_id,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("[%s] failed to update scheduled_jobs row: %s",
                           self.job_name, e)
        return False


def _last_finished_at(db_path: Path, job_name: str) -> Optional[int]:
    try:
        conn = _open_db(db_path)
        row = conn.execute(
            """
            SELECT finished_at FROM scheduled_jobs
            WHERE job_name = ? AND status = 'ok' AND finished_at IS NOT NULL
            ORDER BY finished_at DESC LIMIT 1
            """,
            (job_name,),
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


# ----------------------------------------------------------------------
# Scheduler
# ----------------------------------------------------------------------

JobFn = Callable[["JobContext"], None]


@dataclass
class _RegisteredJob:
    name: str
    interval_s: int
    fn: JobFn


class Scheduler:
    """Threading.Timer-based scheduler. One Timer per job, re-armed after each run."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._jobs: dict[str, _RegisteredJob] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._stop_event = threading.Event()

    def register(self, name: str, interval_s: int, fn: JobFn) -> None:
        if name in self._jobs:
            raise ValueError(f"job already registered: {name}")
        self._jobs[name] = _RegisteredJob(name, interval_s, fn)
        logger.info("registered job %s (every %ds)", name, interval_s)

    def list_jobs(self) -> list[_RegisteredJob]:
        return list(self._jobs.values())

    def run_once(self, name: str) -> None:
        if name not in self._jobs:
            raise KeyError(f"unknown job: {name}")
        job = self._jobs[name]
        logger.info("[%s] running once", name)
        with JobContext(name, self.db_path) as ctx:
            job.fn(ctx)
        logger.info("[%s] run_once done (affected_rows=%d)", name, ctx.affected_rows)

    def _arm(self, job: _RegisteredJob, delay_s: float) -> None:
        if self._stop_event.is_set():
            return
        timer = threading.Timer(delay_s, self._tick, args=(job,))
        timer.daemon = True
        self._timers[job.name] = timer
        timer.start()

    def _tick(self, job: _RegisteredJob) -> None:
        if self._stop_event.is_set():
            return
        try:
            with JobContext(job.name, self.db_path) as ctx:
                job.fn(ctx)
        except Exception:
            # JobContext already logged + recorded the error.
            pass
        finally:
            self._arm(job, job.interval_s)

    def start(self) -> None:
        """Arm every job. Late-running jobs are kicked immediately."""
        now = int(time.time())
        for job in self._jobs.values():
            last = _last_finished_at(self.db_path, job.name)
            if last is None or (now - last) >= job.interval_s:
                logger.info("[%s] last_finished=%s, due now -> running immediately",
                            job.name, last)
                # Kick immediately (in a Timer with delay=0 so we don't block start).
                self._arm(job, 0)
            else:
                wait = job.interval_s - (now - last)
                logger.info("[%s] last_finished=%d, next run in %ds",
                            job.name, last, wait)
                self._arm(job, wait)

    def stop(self) -> None:
        self._stop_event.set()
        for t in self._timers.values():
            t.cancel()
        logger.info("scheduler stopped")


# ----------------------------------------------------------------------
# Job stubs (real implementations land in W3 / W4)
# ----------------------------------------------------------------------

def tk_metrics_refresh_job(ctx: JobContext) -> None:
    """B.2 — pull TK Display metrics for every published video."""
    from src.services.tk_videos import get_tk_video_service
    result = get_tk_video_service().refresh_metrics_for_published()
    ctx.affected_rows = result.get("appended", 0)
    if result.get("errors"):
        logger.warning("[%s] errors: %s", ctx.job_name, result["errors"][:3])
    logger.info("[%s] checked=%d appended=%d errors=%d",
                ctx.job_name, result.get("checked", 0),
                result.get("appended", 0), len(result.get("errors", [])))


def tk_video_publish_dispatch_job(ctx: JobContext) -> None:
    """B.1 — send any tk_videos with scheduled_at <= now via Blotato."""
    from src.services.tk_videos import get_tk_video_service
    result = get_tk_video_service().dispatch_due()
    ctx.affected_rows = result.get("ok", 0)
    if result.get("errors"):
        logger.warning("[%s] errors: %s", ctx.job_name, result["errors"][:3])
    if result.get("due", 0) > 0 or result.get("ok", 0) > 0:
        logger.info("[%s] due=%d ok=%d error=%d skipped=%d",
                    ctx.job_name, result.get("due", 0), result.get("ok", 0),
                    result.get("error", 0), result.get("skipped", 0))


def tkshop_status_sync_job(ctx: JobContext) -> None:
    """C.4 — sync TKShop product statuses from the wrapper server."""
    from src.services.tkshop import get_tkshop_service
    result = get_tkshop_service().sync_statuses()
    ctx.affected_rows = result.get("updated", 0)
    if result.get("errors"):
        logger.warning("[%s] errors: %s", ctx.job_name, result["errors"][:3])
    logger.info("[%s] checked=%d updated=%d errors=%d",
                ctx.job_name, result.get("checked", 0),
                result.get("updated", 0), len(result.get("errors", [])))


def build_default_scheduler(db_path: Path = DEFAULT_DB_PATH) -> Scheduler:
    s = Scheduler(db_path=db_path)
    s.register("tk_metrics_refresh",         2 * 60 * 60, tk_metrics_refresh_job)
    s.register("tk_video_publish_dispatch",            60, tk_video_publish_dispatch_job)
    s.register("tkshop_status_sync",        30 * 60,      tkshop_status_sync_job)
    return s


# ----------------------------------------------------------------------
# CLI entrypoint
# ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--list", action="store_true",
                        help="List registered jobs and exit")
    parser.add_argument("--run-once", metavar="JOB",
                        help="Run a single job once and exit")
    args = parser.parse_args()

    scheduler = build_default_scheduler(db_path=args.db_path)

    if args.list:
        for j in scheduler.list_jobs():
            print(f"{j.name:<28} every {j.interval_s}s")
        return 0

    if args.run_once:
        scheduler.run_once(args.run_once)
        return 0

    # Long-running mode
    def _on_signal(signum, frame):
        logger.info("received signal %s, stopping", signum)
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_signal)

    logger.info("scheduler starting (db=%s)", args.db_path)
    scheduler.start()

    # Block main thread; Timer threads do the work.
    try:
        while not scheduler._stop_event.wait(timeout=3600):
            pass
    except KeyboardInterrupt:
        scheduler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
