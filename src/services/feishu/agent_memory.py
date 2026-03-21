"""Agent Memory — per-user conversation history and task state management.

Provides sliding-window conversation memory and task tracking for the
Feishu Blog Agent.  Backed by SQLite (via TaskStore) for persistence
across process restarts.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any

from src.core.logger import get_logger

logger = get_logger("agent.memory")


@dataclass
class TaskState:
    """Tracks a single blog generation task."""

    task_id: str
    user_id: str
    chat_id: str
    topic: str
    keywords: list[str]
    status: str = "pending"
    scheduled_time: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    shopify_article_id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    progress_messages: list[str] = field(default_factory=list)
    error: Optional[str] = None

    # Artifact storage for post-generation modification
    content_plan: Optional[dict] = None
    draft_content: Optional[str] = None
    draft_meta: Optional[dict] = None
    image_records: list[dict] = field(default_factory=list)
    images_dir: Optional[str] = None
    markdown_path: Optional[str] = None

    VALID_STATUSES = (
        "pending", "scheduled", "planning", "writing", "reviewing",
        "generating_images", "publishing", "done", "failed", "cancelled",
    )

    def update_status(self, status: str, message: Optional[str] = None):
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}")
        self.status = status
        self.updated_at = datetime.now()
        if message:
            self.progress_messages.append(f"[{status}] {message}")

    def to_summary(self) -> dict:
        return {
            "task_id": self.task_id,
            "topic": self.topic,
            "keywords": self.keywords,
            "status": self.status,
            "scheduled_time": self.scheduled_time.isoformat() if self.scheduled_time else None,
            "shopify_article_id": self.shopify_article_id,
            "created_at": self.created_at.isoformat(),
            "error": self.error,
        }


MAX_SINGLE_MESSAGE_CHARS = 2000
TOKEN_BUDGET = 6000
CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 1 token ~ 4 chars for mixed CJK/English."""
    return len(text) // CHARS_PER_TOKEN + 1


def _truncate_content(content: str, max_chars: int = MAX_SINGLE_MESSAGE_CHARS) -> str:
    """Truncate a single message if it exceeds the limit."""
    if len(content) <= max_chars:
        return content
    half = max_chars // 2
    return content[:half] + f"\n... [truncated {len(content) - max_chars} chars] ...\n" + content[-half:]


class AgentMemory:
    """Per-user conversation history and task state manager.

    Backed by SQLite for persistence.  In-memory caches are populated
    from the database on startup and kept in sync via write-through.

    Implements three layers of context protection:
    1. Single-message truncation -- long tool results are capped
    2. Token-budget sliding window -- older messages are dropped to
       fit within the LLM context budget
    3. Summary preservation -- dropped messages are condensed into a
       brief summary so the agent retains awareness of past context

    Thread-safe: all mutations are protected by a lock.
    """

    def __init__(
        self,
        max_history: int = 30,
        token_budget: int = TOKEN_BUDGET,
        db_path: str = "./data/agent.db",
    ):
        self.max_history = max_history
        self.token_budget = token_budget
        self._lock = threading.Lock()

        from src.services.feishu.store import TaskStore
        self._store = TaskStore(db_path)

        tasks, user_tasks = self._store.load_all_tasks()
        self._tasks: dict[str, TaskState] = tasks
        self._user_tasks: dict[str, list[str]] = user_tasks

        self._conversations: dict[str, list[dict]] = self._store.load_conversations(max_history)
        self._summaries: dict[str, str] = self._store.load_summaries()

        logger.info(
            f"AgentMemory loaded: {len(self._tasks)} tasks, "
            f"{len(self._conversations)} users with conversations"
        )

    # ------------------------------------------------------------------
    # Conversation management
    # ------------------------------------------------------------------

    def add_message(self, user_id: str, role: str, content: str):
        """Append a message to a user's conversation history.

        Long messages are automatically truncated to prevent context blowup.
        Persists to SQLite.
        """
        content = _truncate_content(content)
        timestamp = datetime.now().isoformat()

        with self._lock:
            if user_id not in self._conversations:
                self._conversations[user_id] = []
            self._conversations[user_id].append({
                "role": role,
                "content": content,
                "timestamp": timestamp,
            })
            if len(self._conversations[user_id]) > self.max_history:
                self._conversations[user_id] = self._conversations[user_id][-self.max_history:]

        self._store.save_message(user_id, role, content, timestamp)
        self._store.trim_conversations(user_id, self.max_history)

    def get_history(self, user_id: str, token_budget: Optional[int] = None) -> list[dict]:
        """Return conversation history trimmed to fit the token budget.

        Newest messages are kept first. If messages are dropped, a summary
        of the dropped portion is prepended as a system message.
        """
        budget = token_budget or self.token_budget

        with self._lock:
            all_msgs = list(self._conversations.get(user_id, []))
            summary = self._summaries.get(user_id, "")

        if not all_msgs:
            return []

        kept: list[dict] = []
        used_tokens = 0

        for msg in reversed(all_msgs):
            msg_tokens = _estimate_tokens(msg["content"])
            if used_tokens + msg_tokens > budget and kept:
                break
            kept.append(msg)
            used_tokens += msg_tokens

        kept.reverse()

        dropped_count = len(all_msgs) - len(kept)
        if dropped_count > 0:
            dropped_msgs = all_msgs[:dropped_count]
            new_summary = self._build_summary(dropped_msgs, summary)
            with self._lock:
                self._summaries[user_id] = new_summary
            self._store.save_summary(user_id, new_summary)

            summary_msg = {
                "role": "system",
                "content": f"[Previous conversation summary]: {new_summary}",
                "timestamp": dropped_msgs[-1].get("timestamp", ""),
            }
            return [summary_msg] + kept

        if summary:
            summary_msg = {
                "role": "system",
                "content": f"[Previous conversation summary]: {summary}",
                "timestamp": "",
            }
            return [summary_msg] + kept

        return kept

    @staticmethod
    def _build_summary(dropped_msgs: list[dict], existing_summary: str) -> str:
        """Build a compact summary of dropped messages.

        Uses a simple extractive approach (no LLM call) to keep it fast.
        """
        parts = []
        if existing_summary:
            parts.append(existing_summary)

        for msg in dropped_msgs:
            role = msg["role"]
            content = msg["content"][:150]
            if role == "user":
                parts.append(f"User asked: {content}")
            elif role == "assistant":
                parts.append(f"Bot replied: {content}")
            elif role == "tool":
                parts.append(f"Tool result: {content}")

        combined = " | ".join(parts)
        if len(combined) > 500:
            combined = combined[:500] + "..."
        return combined

    def clear_history(self, user_id: str):
        with self._lock:
            self._conversations.pop(user_id, None)
            self._summaries.pop(user_id, None)
        self._store.clear_conversations(user_id)

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def create_task(
        self,
        user_id: str,
        chat_id: str,
        topic: str,
        keywords: list[str],
        scheduled_time: Optional[datetime] = None,
    ) -> TaskState:
        """Create and register a new task. Persists to SQLite."""
        task_id = f"blog-{uuid.uuid4().hex[:8]}"
        status = "scheduled" if scheduled_time else "pending"
        task = TaskState(
            task_id=task_id,
            user_id=user_id,
            chat_id=chat_id,
            topic=topic,
            keywords=keywords,
            status=status,
            scheduled_time=scheduled_time,
        )
        with self._lock:
            self._tasks[task_id] = task
            if user_id not in self._user_tasks:
                self._user_tasks[user_id] = []
            self._user_tasks[user_id].append(task_id)

        self._store.save_task(task)
        return task

    def save_task(self, task: TaskState) -> None:
        """Persist a task's current state to SQLite.

        Call this after externally mutating a TaskState (e.g. after
        pipeline updates status, stores artifacts, etc.).
        """
        self._store.save_task(task)

    def get_task(self, task_id: str) -> Optional[TaskState]:
        with self._lock:
            return self._tasks.get(task_id)

    def get_latest_task(self, user_id: str) -> Optional[TaskState]:
        """Get the most recent task for a user."""
        with self._lock:
            task_ids = self._user_tasks.get(user_id, [])
            if not task_ids:
                return None
            return self._tasks.get(task_ids[-1])

    def get_user_tasks(self, user_id: str, limit: int = 10) -> list[TaskState]:
        with self._lock:
            task_ids = self._user_tasks.get(user_id, [])
            tasks = [self._tasks[tid] for tid in reversed(task_ids) if tid in self._tasks]
            return tasks[:limit]

    def resolve_task_id(self, user_id: str, task_id_or_latest: str) -> Optional[str]:
        """Resolve 'latest' to the actual latest task_id, or return as-is."""
        if task_id_or_latest.lower() == "latest":
            task = self.get_latest_task(user_id)
            return task.task_id if task else None
        return task_id_or_latest
