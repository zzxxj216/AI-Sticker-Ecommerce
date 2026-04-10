"""SQLite database backup service.

Uses sqlite3.backup() for online hot-backup that does not block
concurrent reads/writes on the source database.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.core.logger import get_logger

logger = get_logger("service.backup")

_CN_TZ = timezone(timedelta(hours=8))
_DEFAULT_DB = Path("data/ops_workbench.db")
_BACKUP_DIR = Path("data/backups")


class BackupService:

    def __init__(
        self,
        db_path: Path = _DEFAULT_DB,
        backup_dir: Path = _BACKUP_DIR,
        keep: int = 10,
    ):
        self.db_path = db_path
        self.backup_dir = backup_dir
        self.keep = keep
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(self) -> dict:
        """Create a hot backup of the database.

        Returns dict with filename, size, created_at.
        """
        ts = datetime.now(_CN_TZ).strftime("%Y%m%d_%H%M%S")
        filename = f"backup_{ts}.db"
        dest = self.backup_dir / filename

        src_conn = sqlite3.connect(str(self.db_path))
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
            logger.info("Backup created: %s", filename)
        finally:
            dst_conn.close()
            src_conn.close()

        self._cleanup_old()

        stat = dest.stat()
        return {
            "filename": filename,
            "size": stat.st_size,
            "size_display": _format_size(stat.st_size),
            "created_at": datetime.fromtimestamp(stat.st_mtime, _CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }

    def list_backups(self) -> list[dict]:
        """List all backup files, newest first."""
        backups = []
        for f in sorted(self.backup_dir.glob("backup_*.db"), reverse=True):
            stat = f.stat()
            backups.append({
                "filename": f.name,
                "size": stat.st_size,
                "size_display": _format_size(stat.st_size),
                "created_at": datetime.fromtimestamp(stat.st_mtime, _CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return backups

    def delete_backup(self, filename: str) -> bool:
        """Delete a specific backup file. Returns True if deleted."""
        path = self.backup_dir / filename
        if not path.exists() or not path.name.startswith("backup_"):
            return False
        path.unlink()
        logger.info("Backup deleted: %s", filename)
        return True

    def get_backup_path(self, filename: str) -> Path | None:
        """Return full path if backup exists, else None."""
        path = self.backup_dir / filename
        if path.exists() and path.name.startswith("backup_"):
            return path
        return None

    def _cleanup_old(self) -> int:
        """Remove oldest backups, keeping only self.keep most recent."""
        files = sorted(
            self.backup_dir.glob("backup_*.db"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for f in files[self.keep:]:
            f.unlink()
            removed += 1
            logger.info("Old backup removed: %s", f.name)
        return removed


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1_073_741_824:
        return f"{size_bytes / 1_073_741_824:.1f} GB"
    if size_bytes >= 1_048_576:
        return f"{size_bytes / 1_048_576:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"
