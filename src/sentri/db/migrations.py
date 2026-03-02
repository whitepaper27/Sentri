"""Additive schema migrations for the Sentri SQLite database.

Each migration is a (version, sql) tuple.  The ``run_migrations`` function
applies only the migrations whose version is greater than the current
``schema_version`` recorded in the database.

Rules:
  - Migrations are **additive only** (ALTER TABLE ADD COLUMN, CREATE TABLE).
  - Each migration is idempotent — safe to attempt even if already applied.
  - Never drop or rename columns; old code must keep working.
"""

from __future__ import annotations

import logging
import sqlite3

from .connection import Database

logger = logging.getLogger("sentri.db.migrations")

# ---------------------------------------------------------------------------
# Migration definitions — append new migrations at the end.
# ---------------------------------------------------------------------------

MIGRATIONS: list[tuple[int, str]] = [
    # ------------------------------------------------------------------
    # Migration 2: Add profiling columns to environment_registry
    # ------------------------------------------------------------------
    (
        2,
        """
        ALTER TABLE environment_registry ADD COLUMN database_profile TEXT;
        """,
    ),
    (
        2,
        """
        ALTER TABLE environment_registry ADD COLUMN profile_version INTEGER DEFAULT 0;
        """,
    ),
    (
        2,
        """
        ALTER TABLE environment_registry ADD COLUMN profile_updated_at TIMESTAMP;
        """,
    ),
    # ------------------------------------------------------------------
    # Migration 3: Add severity column to workflows
    # ------------------------------------------------------------------
    (
        3,
        """
        ALTER TABLE workflows ADD COLUMN severity TEXT DEFAULT 'MEDIUM';
        """,
    ),
    # ------------------------------------------------------------------
    # Migration 4: Learning engine tables
    # ------------------------------------------------------------------
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS learning_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            database_id TEXT NOT NULL,
            observation_type TEXT NOT NULL,
            data TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'CAPTURED',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP,
            FOREIGN KEY (workflow_id) REFERENCES workflows(id)
        );
        """,
    ),
    (
        4,
        """
        CREATE INDEX IF NOT EXISTS idx_learning_alert
            ON learning_observations(alert_type);
        """,
    ),
    (
        4,
        """
        CREATE INDEX IF NOT EXISTS idx_learning_status
            ON learning_observations(status);
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS md_file_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            content_hash TEXT NOT NULL,
            backup_path TEXT,
            changed_by TEXT NOT NULL,
            change_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    (
        4,
        """
        CREATE INDEX IF NOT EXISTS idx_md_versions_path
            ON md_file_versions(file_path);
        """,
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_current_version(db: Database) -> int:
    """Return the highest schema version recorded in the database."""
    row = db.execute_read_one("SELECT MAX(version) AS v FROM schema_version")
    return row["v"] if row and row["v"] is not None else 0


def run_migrations(db: Database) -> list[int]:
    """Apply all pending migrations and return a list of versions applied."""
    current = get_current_version(db)
    applied: list[int] = []
    versions_done: set[int] = set()

    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        try:
            db.execute_script(sql)
        except sqlite3.OperationalError as exc:
            # "duplicate column name" is fine — means migration already ran
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                logger.debug("Migration v%d skipped (already applied): %s", version, exc)
            else:
                raise
        if version not in versions_done:
            versions_done.add(version)
            applied.append(version)

    # Record newly applied versions
    for v in sorted(versions_done):
        if v > current:
            db.execute_write(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (v,),
            )
            logger.info("Applied schema migration v%d", v)

    if applied:
        logger.info("Migrations complete: applied versions %s", sorted(applied))
    else:
        logger.debug("No pending migrations (current version: %d)", current)

    return sorted(applied)
