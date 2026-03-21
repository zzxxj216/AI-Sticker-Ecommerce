"""TaskStore — SQLite persistent backend for AgentMemory.

Stores tasks, conversation history, and summaries in a local SQLite
database so they survive process restarts.  All JSON-serializable
fields (lists, dicts) are stored as TEXT columns via json.dumps/loads.

Thread-safe: uses WAL journal mode and short-lived connections per write.
"""

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.core.logger import get_logger

logger = get_logger("agent.store")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    chat_id            TEXT NOT NULL,
    topic              TEXT NOT NULL,
    keywords           TEXT,
    status             TEXT DEFAULT 'pending',
    scheduled_time     TEXT,
    result             TEXT,
    shopify_article_id INTEGER,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    progress_messages  TEXT,
    error              TEXT,
    content_plan       TEXT,
    draft_content      TEXT,
    draft_meta         TEXT,
    image_records      TEXT,
    images_dir         TEXT,
    markdown_path      TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id, created_at);

CREATE TABLE IF NOT EXISTS conversations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT NOT NULL,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, id);

CREATE TABLE IF NOT EXISTS summaries (
    user_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL
);
"""


def _json_dumps(obj) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


def _json_loads(text: Optional[str]):
    if text is None:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _str_to_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


class TaskStore:
    """SQLite-backed persistent store for agent data."""

    def __init__(self, db_path: str = "./data/agent.db"):
        self._db_path = db_path
        self._lock = threading.Lock()

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = self._connect()
        try:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

        logger.info(f"TaskStore initialized: {db_path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def save_task(self, task) -> None:
        """Upsert a TaskState into the database."""
        sql = """
        INSERT OR REPLACE INTO tasks (
            task_id, user_id, chat_id, topic, keywords, status,
            scheduled_time, result, shopify_article_id,
            created_at, updated_at, progress_messages, error,
            content_plan, draft_content, draft_meta,
            image_records, images_dir, markdown_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, (
                    task.task_id,
                    task.user_id,
                    task.chat_id,
                    task.topic,
                    _json_dumps(task.keywords),
                    task.status,
                    _dt_to_str(task.scheduled_time),
                    _json_dumps(task.result),
                    task.shopify_article_id,
                    _dt_to_str(task.created_at),
                    _dt_to_str(task.updated_at),
                    _json_dumps(task.progress_messages),
                    task.error,
                    _json_dumps(task.content_plan),
                    task.draft_content,
                    _json_dumps(task.draft_meta),
                    _json_dumps(task.image_records),
                    task.images_dir,
                    task.markdown_path,
                ))
                conn.commit()
            finally:
                conn.close()

    def load_all_tasks(self):
        """Load all tasks from the database.

        Returns:
            Tuple of (tasks_dict, user_tasks_dict) where
            tasks_dict = {task_id: TaskState} and
            user_tasks_dict = {user_id: [task_id, ...]}.
        """
        from src.services.feishu.agent_memory import TaskState

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at ASC"
            ).fetchall()
        finally:
            conn.close()

        tasks: dict[str, "TaskState"] = {}
        user_tasks: dict[str, list[str]] = {}

        for row in rows:
            task = TaskState(
                task_id=row["task_id"],
                user_id=row["user_id"],
                chat_id=row["chat_id"],
                topic=row["topic"],
                keywords=_json_loads(row["keywords"]) or [],
                status=row["status"] or "pending",
                scheduled_time=_str_to_dt(row["scheduled_time"]),
                result=_json_loads(row["result"]),
                shopify_article_id=row["shopify_article_id"],
                created_at=_str_to_dt(row["created_at"]) or datetime.now(),
                updated_at=_str_to_dt(row["updated_at"]) or datetime.now(),
                progress_messages=_json_loads(row["progress_messages"]) or [],
                error=row["error"],
                content_plan=_json_loads(row["content_plan"]),
                draft_content=row["draft_content"],
                draft_meta=_json_loads(row["draft_meta"]),
                image_records=_json_loads(row["image_records"]) or [],
                images_dir=row["images_dir"],
                markdown_path=row["markdown_path"],
            )
            tasks[task.task_id] = task

            if task.user_id not in user_tasks:
                user_tasks[task.user_id] = []
            user_tasks[task.user_id].append(task.task_id)

        logger.info(f"Loaded {len(tasks)} tasks from database")
        return tasks, user_tasks

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def save_message(self, user_id: str, role: str, content: str, timestamp: str) -> None:
        sql = "INSERT INTO conversations (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)"
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, (user_id, role, content, timestamp))
                conn.commit()
            finally:
                conn.close()

    def load_conversations(self, max_per_user: int = 30) -> dict[str, list[dict]]:
        """Load recent conversations for all users.

        Returns:
            {user_id: [{role, content, timestamp}, ...]}
        """
        conn = self._connect()
        try:
            users = conn.execute(
                "SELECT DISTINCT user_id FROM conversations"
            ).fetchall()

            result: dict[str, list[dict]] = {}
            for u in users:
                uid = u["user_id"]
                rows = conn.execute(
                    "SELECT role, content, timestamp FROM conversations "
                    "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (uid, max_per_user),
                ).fetchall()
                result[uid] = [
                    {"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]}
                    for r in reversed(rows)
                ]
        finally:
            conn.close()

        return result

    def clear_conversations(self, user_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
                conn.commit()
            finally:
                conn.close()

    def trim_conversations(self, user_id: str, keep_last: int = 30) -> None:
        """Delete older messages for a user, keeping only the most recent N."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM conversations WHERE user_id = ? AND id NOT IN "
                    "(SELECT id FROM conversations WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
                    (user_id, user_id, keep_last),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def save_summary(self, user_id: str, summary: str) -> None:
        sql = "INSERT OR REPLACE INTO summaries (user_id, summary) VALUES (?, ?)"
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, (user_id, summary))
                conn.commit()
            finally:
                conn.close()

    def load_summaries(self) -> dict[str, str]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT user_id, summary FROM summaries").fetchall()
        finally:
            conn.close()
        return {r["user_id"]: r["summary"] for r in rows}
