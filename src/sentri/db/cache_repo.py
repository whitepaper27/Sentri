"""Repository for the cache table."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .connection import Database

logger = logging.getLogger("sentri.db.cache")


class CacheRepository:
    """Key-value cache with TTL support."""

    def __init__(self, db: Database):
        self._db = db

    def set(self, key: str, value: str, ttl_seconds: int = 3600) -> None:
        """Set a cache entry with TTL."""
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        self._db.execute_write(
            """INSERT OR REPLACE INTO cache (key, value, expires_at)
               VALUES (?, ?, ?)""",
            (key, value, expires),
        )

    def get(self, key: str) -> Optional[str]:
        """Get a cache entry if it exists and hasn't expired."""
        now = datetime.now(timezone.utc).isoformat()
        row = self._db.execute_read_one(
            "SELECT value FROM cache WHERE key = ? AND (expires_at IS NULL OR expires_at > ?)",
            (key, now),
        )
        return row["value"] if row else None

    def delete(self, key: str) -> None:
        """Delete a cache entry."""
        self._db.execute_write("DELETE FROM cache WHERE key = ?", (key,))

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        now = datetime.now(timezone.utc).isoformat()
        return self._db.execute_write(
            "DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
