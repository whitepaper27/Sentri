"""Convenience wrapper for loading health check patterns from checks/*.md.

Mirrors AlertPatterns for the checks/ directory. Used by ProactiveAgent
to discover, schedule, and execute proactive health checks.
"""

from __future__ import annotations

import logging

from .loader import PolicyLoader

logger = logging.getLogger("sentri.policy.checks")


class CheckPatterns:
    """Provides access to health check queries, schedules, and thresholds."""

    def __init__(self, policy_loader: PolicyLoader):
        self._loader = policy_loader

    def get_health_query(self, check_type: str) -> str:
        """Get the SQL health-check query for a check type."""
        policy = self._loader.load_check(check_type)
        section = policy.get("health_query", {})
        if isinstance(section, dict):
            return self._first_sql(section.get("sql", ""))
        return ""

    def get_threshold(self, check_type: str) -> dict:
        """Get threshold values for evaluating health query results."""
        policy = self._loader.load_check(check_type)
        section = policy.get("threshold", {})
        if isinstance(section, dict):
            items = section.get("items", [])
            result = {}
            for item in items:
                if ":" in str(item):
                    key, val = str(item).split(":", 1)
                    key = key.strip().strip("-").strip()
                    val = val.strip()
                    # Try numeric conversion
                    try:
                        if "." in val:
                            result[key] = float(val)
                        else:
                            result[key] = int(val)
                    except ValueError:
                        result[key] = val
            return result
        return {}

    def get_schedule(self, check_type: str) -> str:
        """Get the schedule for this check (from frontmatter).

        Values: every_6_hours, daily, weekly.
        """
        policy = self._loader.load_check(check_type)
        fm = policy.get("frontmatter", {})
        return str(fm.get("schedule", "daily")).strip()

    def get_routes_to(self, check_type: str) -> str:
        """Get which specialist agent handles findings for this check."""
        policy = self._loader.load_check(check_type)
        fm = policy.get("frontmatter", {})
        return str(fm.get("routes_to", "storage_agent")).strip()

    def get_severity(self, check_type: str) -> str:
        """Get the severity level from frontmatter."""
        policy = self._loader.load_check(check_type)
        fm = policy.get("frontmatter", {})
        return str(fm.get("severity", "MEDIUM")).strip().upper()

    def get_recommended_action(self, check_type: str) -> str:
        """Get the recommended remediation SQL template."""
        policy = self._loader.load_check(check_type)
        section = policy.get("recommended_action", {})
        if isinstance(section, dict):
            return self._first_sql(section.get("sql", ""))
        return ""

    def get_description(self, check_type: str) -> str:
        """Get the human-readable description of this check."""
        policy = self._loader.load_check(check_type)
        section = policy.get("description", "")
        if isinstance(section, dict):
            return section.get("text", "")
        return str(section)

    def get_all_checks(self) -> dict[str, dict]:
        """Scan the checks/ directory and return all check definitions.

        Returns {check_type: {schedule, severity, routes_to, ...}}.
        """
        all_checks = self._loader.load_all_checks()
        result = {}
        for check_type in all_checks:
            try:
                result[check_type] = {
                    "schedule": self.get_schedule(check_type),
                    "severity": self.get_severity(check_type),
                    "routes_to": self.get_routes_to(check_type),
                    "health_query": self.get_health_query(check_type),
                }
            except Exception:
                logger.warning("Skipping check %s: failed to parse", check_type)
        return result

    @staticmethod
    def _first_sql(value) -> str:
        """Extract a single SQL string."""
        if isinstance(value, list):
            return value[0] if value else ""
        return value or ""
