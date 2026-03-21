"""Scheduler Manager — timer-based task scheduling for the Blog Agent.

Uses threading.Timer for one-off scheduled tasks. When a timer fires,
it triggers blog generation and notifies the user via Feishu.
"""

import threading
from datetime import datetime
from typing import Optional, Callable

from src.core.logger import get_logger
from src.services.feishu.agent_memory import TaskState

logger = get_logger("agent.scheduler")


class SchedulerManager:
    """Manages scheduled blog generation tasks using threading.Timer."""

    def __init__(self):
        self._timers: dict[str, threading.Timer] = {}
        self._task_params: dict[str, dict] = {}
        self._generate_callback: Optional[Callable] = None
        self._lock = threading.Lock()

    def set_generate_callback(self, callback: Callable):
        """Set the callback that triggers blog generation.

        The callback signature should be:
            callback(task: TaskState, auto_publish: bool)
        """
        self._generate_callback = callback

    def schedule(self, task: TaskState, auto_publish: bool = True):
        """Schedule a task for future execution."""
        if not task.scheduled_time:
            logger.error(f"Task {task.task_id} has no scheduled_time")
            return

        delay = (task.scheduled_time - datetime.now()).total_seconds()
        if delay <= 0:
            logger.warning(
                f"Task {task.task_id} scheduled_time is in the past, "
                f"executing immediately"
            )
            delay = 1

        lead_time = min(delay * 0.8, 1800)
        gen_delay = max(delay - lead_time, 1)

        logger.info(
            f"Scheduled task {task.task_id}: "
            f"generation starts in {gen_delay:.0f}s, "
            f"target time {task.scheduled_time.strftime('%Y-%m-%d %H:%M')}"
        )

        timer = threading.Timer(
            gen_delay,
            self._on_timer_fire,
            args=(task, auto_publish),
        )
        timer.daemon = True
        timer.start()

        with self._lock:
            self._timers[task.task_id] = timer
            self._task_params[task.task_id] = {"auto_publish": auto_publish}

    def cancel(self, task_id: str) -> bool:
        """Cancel a scheduled task. Returns True if cancelled."""
        with self._lock:
            timer = self._timers.pop(task_id, None)
            self._task_params.pop(task_id, None)

        if timer:
            timer.cancel()
            logger.info(f"Cancelled scheduled task {task_id}")
            return True

        logger.warning(f"No scheduled timer found for task {task_id}")
        return False

    def list_pending(self) -> list[str]:
        with self._lock:
            return list(self._timers.keys())

    def _on_timer_fire(self, task: TaskState, auto_publish: bool):
        """Called when a timer fires — triggers blog generation."""
        logger.info(f"Timer fired for task {task.task_id}")

        with self._lock:
            self._timers.pop(task.task_id, None)
            self._task_params.pop(task.task_id, None)

        if self._generate_callback:
            try:
                self._generate_callback(task, auto_publish)
            except Exception as e:
                logger.error(
                    f"Scheduled generation failed for {task.task_id}: {e}",
                    exc_info=True,
                )
        else:
            logger.error("No generate_callback set on SchedulerManager")

    def shutdown(self):
        """Cancel all pending timers."""
        with self._lock:
            for task_id, timer in self._timers.items():
                timer.cancel()
                logger.info(f"Shutdown: cancelled timer for {task_id}")
            self._timers.clear()
            self._task_params.clear()
