"""Pre-flight check runner for enhanced Agent 4 (v2.0).

Executes SQL-based safety checks from alert .md policies before
the Executor runs the forward action.  Each check is a simple
SQL query with an expected result pattern.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from sentri.core.models import PreFlightCheck
from sentri.oracle.connection_pool import OracleConnectionPool
from sentri.oracle.query_runner import QueryRunner
from sentri.policy.alert_patterns import AlertPatterns

logger = logging.getLogger("sentri.agents.preflight")


class PreFlightRunner:
    """Run pre-flight SQL checks before executing a fix."""

    def __init__(
        self,
        alert_patterns: AlertPatterns,
        oracle_pool: Optional[OracleConnectionPool] = None,
        timeout: int = 15,
    ):
        self._patterns = alert_patterns
        self._oracle_pool = oracle_pool or OracleConnectionPool()
        self._query_runner = QueryRunner(timeout_seconds=timeout)

    def run_checks(
        self,
        alert_type: str,
        database_id: str,
        connection_string: str,
        password: str,
        username: str | None = None,
        params: dict | None = None,
    ) -> list[PreFlightCheck]:
        """Run all pre-flight checks for an alert type.

        Returns a list of PreFlightCheck results (each with passed=True/False).
        """
        check_defs = self._patterns.get_preflight_checks(alert_type)
        if not check_defs:
            logger.debug("No pre-flight checks for alert type %s", alert_type)
            return []

        # Connect to Oracle (read-only)
        try:
            conn = self._oracle_pool.get_connection(
                database_id=database_id,
                connection_string=connection_string,
                password=password,
                username=username,
                read_only=True,
            )
        except Exception as e:
            logger.error("Pre-flight connection failed for %s: %s", database_id, e)
            return [
                PreFlightCheck(
                    name="connection",
                    sql="CONNECT",
                    expected="OK",
                    actual=str(e),
                    passed=False,
                    error=str(e),
                )
            ]

        results: list[PreFlightCheck] = []
        try:
            for check_def in check_defs:
                result = self._run_single_check(conn, check_def, params or {})
                results.append(result)
                status = "PASS" if result.passed else "FAIL"
                logger.info(
                    "Pre-flight %s: %s [%s]", check_def["name"], status, result.actual or ""
                )
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return results

    def _run_single_check(self, conn, check_def: dict, params: dict) -> PreFlightCheck:
        """Execute one pre-flight check."""
        name = check_def.get("name", "unnamed")
        sql = check_def.get("sql", "")
        expected = check_def.get("expected", "")

        check = PreFlightCheck(name=name, sql=sql, expected=expected)

        if not sql.strip():
            check.passed = True
            check.actual = "skipped (no SQL)"
            return check

        try:
            rows = self._query_runner.execute_read(conn, sql, params)
            if not rows:
                check.actual = "no rows returned"
                check.passed = self._evaluate(expected, "no rows")
            else:
                # Use first row, first value for simple comparison
                first_row = rows[0]
                keys = list(first_row.keys())
                if keys:
                    check.actual = str(first_row[keys[0]])
                else:
                    check.actual = str(first_row)
                check.passed = self._evaluate(expected, check.actual)
        except Exception as e:
            check.error = str(e)
            check.actual = f"ERROR: {e}"
            check.passed = False

        return check

    @staticmethod
    def _evaluate(expected: str, actual: str) -> bool:
        """Evaluate whether actual result matches expected pattern.

        Supports:
          - Exact match (case-insensitive)
          - "not empty" / "has rows" — just checks actual isn't empty/no-rows
          - "ONLINE", "YES", "TRUE" — exact match
          - "> N", "< N", ">= N" — numeric comparisons
        """
        if not expected:
            return True  # No expectation = always pass

        expected_lower = expected.strip().lower()
        actual_lower = actual.strip().lower()

        # "not empty" or "has rows"
        if expected_lower in ("not empty", "has rows", "exists"):
            return actual_lower not in ("", "no rows", "no rows returned")

        # "no rows" — expect nothing returned
        if expected_lower == "no rows":
            return actual_lower in ("no rows", "no rows returned")

        # Numeric comparisons: "> 0", ">= 1", "< 100"
        cmp_match = re.match(r"^([><=!]+)\s*(\d+(?:\.\d+)?)$", expected.strip())
        if cmp_match:
            op, threshold = cmp_match.group(1), float(cmp_match.group(2))
            try:
                actual_num = float(actual.strip())
            except ValueError:
                return False
            if op == ">":
                return actual_num > threshold
            if op == ">=":
                return actual_num >= threshold
            if op == "<":
                return actual_num < threshold
            if op == "<=":
                return actual_num <= threshold
            if op in ("=", "=="):
                return actual_num == threshold
            if op in ("!=", "<>"):
                return actual_num != threshold

        # Exact match (case-insensitive)
        return expected_lower == actual_lower


def all_passed(checks: list[PreFlightCheck]) -> bool:
    """Return True if every check passed."""
    return all(c.passed for c in checks)


def format_results(checks: list[PreFlightCheck]) -> str:
    """Format pre-flight results for logging/display."""
    lines = []
    for c in checks:
        status = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{status}] {c.name}: expected={c.expected}, actual={c.actual}")
        if c.error:
            lines.append(f"         error: {c.error}")
    return "\n".join(lines)
