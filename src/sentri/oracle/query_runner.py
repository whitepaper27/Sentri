"""Safe Oracle query execution with parameterization and timeouts."""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional

from sentri.core.exceptions import OracleQueryError, VerificationTimeoutError

logger = logging.getLogger("sentri.oracle.query")

# Regex to find :name bind placeholders in SQL (not inside quotes)
_BIND_PLACEHOLDER_RE = re.compile(r":([a-zA-Z_]\w*)")


class QueryRunner:
    """Execute parameterized queries against Oracle with timeout enforcement."""

    def __init__(self, timeout_seconds: int = 30):
        self.timeout = timeout_seconds

    @staticmethod
    def _filter_params(sql: str, params: dict | None) -> dict:
        """Return only the params that have matching :placeholder in the SQL."""
        if not params:
            return {}
        needed = set(_BIND_PLACEHOLDER_RE.findall(sql))
        return {k: v for k, v in params.items() if k in needed}

    def execute_read(
        self,
        connection,
        sql: str,
        params: dict | None = None,
        timeout: int | None = None,
    ) -> list[dict]:
        """Execute a read-only query and return results as list of dicts.

        Args:
            connection: An oracledb connection
            sql: Parameterized SQL (use :name style placeholders)
            params: Dict of parameter values
            timeout: Override default timeout
        """
        effective_timeout = timeout or self.timeout
        filtered = self._filter_params(sql, params)
        result: list[dict] = []
        error: Optional[Exception] = None

        def _run():
            nonlocal result, error
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.execute(sql, filtered)
                columns = [col[0].lower() for col in cursor.description or []]
                rows = cursor.fetchall()
                result = [dict(zip(columns, row)) for row in rows]
            except Exception as e:
                error = e
            finally:
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception:
                        pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=effective_timeout)

        if thread.is_alive():
            # Cancel the in-flight query if possible
            try:
                connection.cancel()
            except Exception:
                pass
            raise VerificationTimeoutError(
                f"Query timed out after {effective_timeout}s: {sql[:100]}"
            )
        if error:
            raise OracleQueryError(f"Query failed: {error}") from error

        return result

    def execute_write(
        self,
        connection,
        sql: str,
        params: dict | None = None,
        timeout: int | None = None,
    ) -> int:
        """Execute a write query (DML/DDL) and return rows affected.

        Does NOT auto-commit. Caller is responsible for commit/rollback.
        """
        effective_timeout = timeout or self.timeout
        rowcount: int = 0
        error: Optional[Exception] = None

        filtered = self._filter_params(sql, params)

        def _run():
            nonlocal rowcount, error
            cursor = None
            try:
                cursor = connection.cursor()
                cursor.execute(sql, filtered)
                rowcount = cursor.rowcount
            except Exception as e:
                error = e
            finally:
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception:
                        pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=effective_timeout)

        if thread.is_alive():
            try:
                connection.cancel()
            except Exception:
                pass
            raise OracleQueryError(f"Write query timed out after {effective_timeout}s: {sql[:100]}")
        if error:
            raise OracleQueryError(f"Write query failed: {error}") from error

        return rowcount
