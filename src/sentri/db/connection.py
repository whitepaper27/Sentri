"""Thread-safe SQLite connection manager for Sentri."""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger("sentri.db")


class Database:
    """Thread-safe SQLite wrapper using WAL mode and a write lock."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._write_lock = threading.Lock()
        self._local = threading.local()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local connection (one per thread for read safety)."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def execute_read(self, sql: str, params: tuple | list | None = None) -> list[sqlite3.Row]:
        """Execute a read query and return all rows."""
        conn = self._get_connection()
        cursor = conn.execute(sql, params or [])
        return cursor.fetchall()

    def execute_read_one(self, sql: str, params: tuple | list | None = None) -> sqlite3.Row | None:
        """Execute a read query and return the first row or None."""
        conn = self._get_connection()
        cursor = conn.execute(sql, params or [])
        return cursor.fetchone()

    def execute_write(self, sql: str, params: tuple | list | None = None) -> int:
        """Execute a write query with thread-safe locking. Returns rowcount."""
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.execute(sql, params or [])
            conn.commit()
            return cursor.rowcount

    def execute_write_returning_id(self, sql: str, params: tuple | list | None = None) -> int:
        """Execute an INSERT and return the last inserted rowid."""
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.execute(sql, params or [])
            conn.commit()
            return cursor.lastrowid

    def execute_script(self, sql_script: str) -> None:
        """Execute a multi-statement SQL script (for schema init)."""
        with self._write_lock:
            conn = self._get_connection()
            conn.executescript(sql_script)

    def initialize_schema(self) -> None:
        """Create all tables if they don't exist, then run pending migrations."""
        from .schema import SCHEMA_SQL

        logger.info("Initializing database schema at %s", self._db_path)
        self.execute_script(SCHEMA_SQL)

        # Apply additive migrations (v2.0+)
        from .migrations import run_migrations

        run_migrations(self)

    def close(self) -> None:
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None
