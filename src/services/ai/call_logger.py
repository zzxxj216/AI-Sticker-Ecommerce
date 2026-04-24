"""Context manager that records every AI call into ``ai_call_logs``.

Usage::

    with AICallLog(
        service="openai",
        model="gpt-5.4-pro",
        task="topic_plan:main",
        related_table="topic_plans",
        related_id=42,
        prompt_summary=prompt[:200],
    ) as log:
        result = openai.generate(prompt)
        log.set_usage(
            input_tokens=result["usage"]["input_tokens"],
            output_tokens=result["usage"]["output_tokens"],
            cost=estimate_text_cost(...),
        )
        # exit -> updates row with finished status, latency, tokens, cost

If the body raises, the context manager records status='error' with the
exception message and re-raises.
"""

from __future__ import annotations

import sqlite3
import time
import traceback
from pathlib import Path
from typing import Optional

from src.core.logger import get_logger

logger = get_logger("service.ai.call_logger")

DEFAULT_DB_PATH = Path("data/ops_workbench.db")


def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class AICallLog:
    """Context manager wrapping a single AI call for ai_call_logs persistence."""

    def __init__(
        self,
        service: str,
        model: str,
        task: str,
        *,
        related_table: str = "",
        related_id: Optional[int] = None,
        prompt_summary: str = "",
        db_path: Path = DEFAULT_DB_PATH,
    ):
        self.service = service
        self.model = model
        self.task = task
        self.related_table = related_table
        self.related_id = related_id
        self.prompt_summary = prompt_summary[:500]  # cap length
        self.db_path = db_path

        self._row_id: Optional[int] = None
        self._start_ts: float = 0.0
        self._input_tokens = 0
        self._output_tokens = 0
        self._cost = 0.0

    def __enter__(self) -> "AICallLog":
        self._start_ts = time.time()
        try:
            conn = _open_db(self.db_path)
            cur = conn.execute(
                """
                INSERT INTO ai_call_logs
                  (service, model, task, related_table, related_id,
                   prompt_summary, prompt_tokens, completion_tokens,
                   latency_ms, cost_estimate, status, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 'pending', '', ?)
                """,
                (
                    self.service,
                    self.model,
                    self.task,
                    self.related_table,
                    self.related_id,
                    self.prompt_summary,
                    int(self._start_ts),
                ),
            )
            self._row_id = cur.lastrowid
            conn.commit()
            conn.close()
        except Exception as e:
            # Logging must never break the actual call — degrade gracefully.
            logger.warning("Failed to insert ai_call_log start row: %s", e)
            self._row_id = None
        return self

    def set_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost: float = 0.0,
    ) -> None:
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._cost = cost

    def __exit__(self, exc_type, exc, tb) -> bool:
        latency_ms = int((time.time() - self._start_ts) * 1000)
        if exc is None:
            status = "ok"
            err_msg = ""
        else:
            status = "error"
            err_msg = (str(exc) or exc.__class__.__name__)[:1000]
            logger.warning(
                "AI call failed (service=%s model=%s task=%s): %s",
                self.service, self.model, self.task, err_msg,
            )

        if self._row_id is None:
            # The start-row insert failed; nothing to update. Return False so
            # any exception still propagates.
            return False

        try:
            conn = _open_db(self.db_path)
            conn.execute(
                """
                UPDATE ai_call_logs
                   SET prompt_tokens     = ?,
                       completion_tokens = ?,
                       latency_ms        = ?,
                       cost_estimate     = ?,
                       status            = ?,
                       error             = ?
                 WHERE id = ?
                """,
                (
                    self._input_tokens,
                    self._output_tokens,
                    latency_ms,
                    self._cost,
                    status,
                    err_msg,
                    self._row_id,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Failed to update ai_call_log finish row: %s", e)

        return False  # never swallow exceptions
