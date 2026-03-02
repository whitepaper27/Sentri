"""Repository for the md_file_versions table (v2.0).

Tracks every change made to policy .md files by the learning engine,
enabling one-command rollback and full audit history.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from .connection import Database

logger = logging.getLogger("sentri.db.md_versions")


class MdVersionRepository:
    """CRUD for .md file version tracking."""

    def __init__(self, db: Database):
        self._db = db

    def record_version(
        self,
        file_path: str,
        content_hash: str,
        changed_by: str,
        backup_path: Optional[str] = None,
        change_reason: Optional[str] = None,
    ) -> int:
        """Record a new version of a .md file. Returns the version id."""
        # Determine the next version number for this file
        row = self._db.execute_read_one(
            "SELECT MAX(version) as max_v FROM md_file_versions WHERE file_path = ?",
            (file_path,),
        )
        next_version = (row["max_v"] or 0) + 1 if row else 1

        return self._db.execute_write_returning_id(
            """INSERT INTO md_file_versions
               (file_path, version, content_hash, backup_path,
                changed_by, change_reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_path, next_version, content_hash, backup_path, changed_by, change_reason),
        )

    def get_latest_version(self, file_path: str) -> Optional[dict]:
        """Get the most recent version record for a file."""
        row = self._db.execute_read_one(
            """SELECT * FROM md_file_versions
               WHERE file_path = ?
               ORDER BY version DESC LIMIT 1""",
            (file_path,),
        )
        if row is None:
            return None
        return dict(row)

    def get_history(self, file_path: str) -> list[dict]:
        """Get the full version history for a file."""
        rows = self._db.execute_read(
            """SELECT * FROM md_file_versions
               WHERE file_path = ?
               ORDER BY version DESC""",
            (file_path,),
        )
        return [dict(r) for r in rows]

    def list_tracked_files(self) -> list[str]:
        """List all .md files that have version records."""
        rows = self._db.execute_read(
            "SELECT DISTINCT file_path FROM md_file_versions ORDER BY file_path"
        )
        return [row["file_path"] for row in rows]

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute a SHA-256 hash of file content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
