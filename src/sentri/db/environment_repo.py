"""Repository for the environment_registry table."""

from __future__ import annotations

import logging
from typing import Optional

from sentri.core.models import EnvironmentRecord

from .connection import Database

logger = logging.getLogger("sentri.db.environment")


class EnvironmentRepository:
    """CRUD for database environment registry."""

    def __init__(self, db: Database):
        self._db = db

    def upsert(self, env: EnvironmentRecord) -> None:
        """Insert or replace an environment record."""
        self._db.execute_write(
            """INSERT OR REPLACE INTO environment_registry
               (database_id, database_name, environment, oracle_version,
                architecture, connection_string, autonomy_level,
                critical_schemas, business_owner, dba_owner, last_verified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                env.database_id,
                env.database_name,
                env.environment,
                env.oracle_version,
                env.architecture,
                env.connection_string,
                env.autonomy_level,
                env.critical_schemas,
                env.business_owner,
                env.dba_owner,
            ),
        )
        logger.debug("Upserted environment %s", env.database_id)

    def get(self, database_id: str) -> Optional[EnvironmentRecord]:
        """Fetch a single environment by database_id."""
        row = self._db.execute_read_one(
            "SELECT * FROM environment_registry WHERE database_id = ?",
            (database_id,),
        )
        if row is None:
            return None
        return self._row_to_record(row)

    def list_all(self) -> list[EnvironmentRecord]:
        """List all registered environments."""
        rows = self._db.execute_read(
            "SELECT * FROM environment_registry ORDER BY environment, database_id"
        )
        return [self._row_to_record(r) for r in rows]

    def find_by_environment(self, environment: str) -> list[EnvironmentRecord]:
        """Find all databases in a given environment tier."""
        rows = self._db.execute_read(
            "SELECT * FROM environment_registry WHERE environment = ?",
            (environment,),
        )
        return [self._row_to_record(r) for r in rows]

    def update_profile(self, database_id: str, profile_json: str, version: int) -> int:
        """Store a database profile (Agent 0 output)."""
        return self._db.execute_write(
            """UPDATE environment_registry
               SET database_profile = ?,
                   profile_version = ?,
                   profile_updated_at = CURRENT_TIMESTAMP
               WHERE database_id = ?""",
            (profile_json, version, database_id),
        )

    def get_profile(self, database_id: str) -> Optional[str]:
        """Return the raw JSON profile for a database, or None."""
        row = self._db.execute_read_one(
            "SELECT database_profile FROM environment_registry WHERE database_id = ?",
            (database_id,),
        )
        if row is None:
            return None
        return row["database_profile"]

    @staticmethod
    def _row_to_record(row) -> EnvironmentRecord:
        keys = row.keys()
        return EnvironmentRecord(
            database_id=row["database_id"],
            database_name=row["database_name"],
            environment=row["environment"],
            oracle_version=row["oracle_version"],
            architecture=row["architecture"],
            connection_string=row["connection_string"],
            autonomy_level=row["autonomy_level"],
            critical_schemas=row["critical_schemas"],
            business_owner=row["business_owner"],
            dba_owner=row["dba_owner"],
            created_at=row["created_at"],
            last_verified=row["last_verified"],
            database_profile=row["database_profile"] if "database_profile" in keys else None,
            profile_version=row["profile_version"] if "profile_version" in keys else 0,
            profile_updated_at=row["profile_updated_at"] if "profile_updated_at" in keys else None,
        )
