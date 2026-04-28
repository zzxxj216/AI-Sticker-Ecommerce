"""Fire-and-forget background task helper for long-running AI operations.

Why this exists: image generation (60-150s/call), sticker split (60-150s/call),
caption gen (~30s), detail gen (~30s), synthesis (~30-60s) all currently
block the request handler — even though FastAPI runs sync handlers in a
threadpool so other browser tabs *should* work, the operator's submitting
tab is held hostage on the spinner button until completion. They can't
navigate away to check other packs / kick off a parallel job / look at
the analytics.

Pattern: route handler kicks off the work via ``run_async()`` and
immediately 303s the operator back to a detail page. The detail page
reads status from the existing ``generation_status`` columns
(pack_previews / pack_stickers) or from this module's in-memory task
registry, and uses ``<meta http-equiv="refresh" content="10">`` when any
task is still in flight.

Tradeoffs:
- In-memory tracking dies on server restart — running tasks finish (the
  thread keeps going) but is_running() forgets about them. Acceptable
  because the AUTHORITATIVE state is the DB row's status; this registry
  is only for "is there a captioning job pending for video #N where the
  row doesn't have an explicit generating flag".
- No retry / error capture beyond what the wrapped service already does.
  Each long service call is responsible for catching exceptions and
  setting status='error' on its DB row.
- Daemon threads — they die with uvicorn. Operator restarting kills any
  in-flight image gen mid-call (DB row stays 'generating' forever). A
  housekeeping job could reap stale 'generating' rows older than ~30min.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from src.core.logger import get_logger

logger = get_logger("web.background")

# task_id → {"thread": Thread, "started_at": int, "label": str}
_tasks: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def run_async(
    task_id: str,
    fn: Callable[..., Any],
    *args: Any,
    label: str = "",
    **kwargs: Any,
) -> bool:
    """Start ``fn(*args, **kwargs)`` in a daemon thread.

    Returns True if a new thread was started, False if a task with this
    ``task_id`` is still running (we don't queue — caller can decide to
    surface "已在运行" to the operator).
    """
    with _lock:
        existing = _tasks.get(task_id)
        if existing and existing["thread"].is_alive():
            logger.info("run_async skipped — %s already running", task_id)
            return False

        def _wrapped() -> None:
            t0 = time.time()
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("background task %s failed", task_id)
            else:
                logger.info("background task %s finished in %.1fs",
                            task_id, time.time() - t0)
            finally:
                with _lock:
                    cur = _tasks.get(task_id)
                    if cur and cur["thread"] is threading.current_thread():
                        _tasks.pop(task_id, None)

        t = threading.Thread(target=_wrapped, name=task_id, daemon=True)
        _tasks[task_id] = {
            "thread":     t,
            "started_at": int(time.time()),
            "label":      label or task_id,
        }
        t.start()
        logger.info("background task %s started (label=%s)", task_id, label or task_id)
        return True


def is_running(task_id: str) -> bool:
    with _lock:
        existing = _tasks.get(task_id)
        return bool(existing and existing["thread"].is_alive())


def list_running() -> list[dict]:
    """Snapshot of running tasks — used by the system health page."""
    with _lock:
        out = []
        for tid, info in _tasks.items():
            if info["thread"].is_alive():
                out.append({
                    "task_id":    tid,
                    "label":      info["label"],
                    "started_at": info["started_at"],
                    "elapsed_s":  int(time.time() - info["started_at"]),
                })
        return out
