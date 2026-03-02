"""Oracle database connection management using python-oracledb."""

from __future__ import annotations

import logging

from sentri.core.exceptions import OracleConnectionError

logger = logging.getLogger("sentri.oracle.pool")


class OracleConnectionPool:
    """Manages connection pools to target Oracle databases.

    Uses python-oracledb in thin mode (no Oracle Client needed).
    """

    def __init__(self):
        self._pools: dict[str, object] = {}
        self._oracledb = None

    def _get_oracledb(self):
        """Lazy import of oracledb to avoid hard failure if not installed."""
        if self._oracledb is None:
            try:
                import oracledb

                oracledb.init_oracle_client()  # Try thick mode first
            except Exception:
                import oracledb  # Falls back to thin mode
            self._oracledb = oracledb
        return self._oracledb

    def get_connection(
        self,
        database_id: str,
        connection_string: str,
        password: str,
        read_only: bool = True,
        username: str | None = None,
    ):
        """Get a connection to a target Oracle database.

        Args:
            database_id: Identifier for the database
            connection_string: Oracle connection string (DSN)
            password: Database password
            read_only: If True, sets session to read-only mode
            username: Explicit username override (else parsed from connection_string)
        """
        oracledb = self._get_oracledb()

        try:
            # Parse connection string: oracle://user@host:port/service
            user, dsn = self._parse_connection_string(connection_string)
            if username:
                user = username  # Config overrides URL-embedded user

            conn = oracledb.connect(user=user, password=password, dsn=dsn)

            # Note: Do NOT set CURRENT_SCHEMA = SYS here.
            # V$ dynamic performance views are public synonyms for V_$ fixed views.
            # Setting CURRENT_SCHEMA = SYS causes Oracle to resolve V$DATABASE as
            # SYS."V$DATABASE" (nonexistent) instead of using the public synonym.
            # DBA_* views and V$ views both work via public synonyms without this.

            logger.debug("Connected to %s (read_only=%s)", database_id, read_only)
            return conn

        except Exception as e:
            raise OracleConnectionError(f"Failed to connect to {database_id}: {e}") from e

    def close_all(self) -> None:
        """Close all connection pools."""
        for name, pool in self._pools.items():
            try:
                pool.close()
                logger.debug("Closed pool for %s", name)
            except Exception:
                pass
        self._pools.clear()

    @staticmethod
    def _parse_connection_string(conn_str: str) -> tuple[str, str]:
        """Parse oracle://user@host:port/service into (user, DSN).

        Returns (username, host:port/service_name).
        """
        # Strip protocol prefix
        clean = conn_str
        if "://" in clean:
            clean = clean.split("://", 1)[1]

        # Split user@host:port/service
        if "@" in clean:
            user, dsn = clean.split("@", 1)
        else:
            user = "sentri_agent"
            dsn = clean

        return user, dsn
