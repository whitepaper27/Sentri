"""Convenience wrapper for loading alert-specific patterns and queries."""

from __future__ import annotations

import logging
import re

from sentri.core.exceptions import PolicyLoadError

from .loader import PolicyLoader

logger = logging.getLogger("sentri.policy.alerts")


class AlertPatterns:
    """Provides easy access to alert regex patterns, SQL queries, and actions."""

    def __init__(self, policy_loader: PolicyLoader):
        self._loader = policy_loader
        self._compiled_patterns: dict[str, re.Pattern] = {}

    def get_email_pattern(self, alert_type: str) -> re.Pattern:
        """Get the compiled regex pattern for matching alert emails."""
        if alert_type in self._compiled_patterns:
            return self._compiled_patterns[alert_type]

        policy = self._loader.load_alert(alert_type)
        section = policy.get("email_pattern", {})

        regex_str = None
        if isinstance(section, dict):
            regex_str = section.get("regex")
        if not regex_str:
            raise PolicyLoadError(
                f"No regex pattern found in alerts/{alert_type}.md [Email Pattern]"
            )

        pattern = re.compile(regex_str, re.IGNORECASE | re.DOTALL)
        self._compiled_patterns[alert_type] = pattern
        return pattern

    def get_extracted_fields(self, alert_type: str) -> list[str]:
        """Get the list of field extraction rules."""
        policy = self._loader.load_alert(alert_type)
        section = policy.get("extracted_fields", {})
        if isinstance(section, dict):
            return section.get("items", [])
        return []

    def get_verification_query(self, alert_type: str) -> str:
        """Get the SQL query used to verify the alert."""
        policy = self._loader.load_alert(alert_type)
        section = policy.get("verification_query", {})
        if isinstance(section, dict):
            return self._first_sql(section.get("sql", ""))
        return ""

    def get_forward_action(self, alert_type: str) -> str:
        """Get the SQL/command to fix the issue."""
        policy = self._loader.load_alert(alert_type)
        section = policy.get("forward_action", {})
        if isinstance(section, dict):
            return self._first_sql(section.get("sql", ""))
        return ""

    def get_rollback_action(self, alert_type: str) -> str:
        """Get the SQL/command to undo the fix."""
        policy = self._loader.load_alert(alert_type)
        section = policy.get("rollback_action", {})
        if isinstance(section, dict):
            return self._first_sql(section.get("sql", ""))
        return ""

    def get_validation_query(self, alert_type: str) -> str:
        """Get the SQL query to verify the fix worked."""
        policy = self._loader.load_alert(alert_type)
        section = policy.get("validation_query", {})
        if isinstance(section, dict):
            return self._first_sql(section.get("sql", ""))
        return ""

    @staticmethod
    def _first_sql(value) -> str:
        """Extract a single SQL string (policy parser may return a list for multiple code blocks)."""
        if isinstance(value, list):
            return value[0] if value else ""
        return value or ""

    def get_risk_level(self, alert_type: str) -> str:
        """Get the risk level for this alert type."""
        policy = self._loader.load_alert(alert_type)
        risk = policy.get("risk_level", "HIGH")
        if isinstance(risk, dict):
            return risk.get("text", "HIGH")
        return str(risk).strip().upper()

    def get_tolerance(self, alert_type: str) -> dict:
        """Get tolerance values for verification comparison."""
        policy = self._loader.load_alert(alert_type)
        section = policy.get("tolerance", {})
        if isinstance(section, dict):
            items = section.get("items", [])
            result = {}
            for item in items:
                if ":" in item:
                    key, val = item.split(":", 1)
                    result[key.strip().strip("`")] = val.strip()
            return result
        return {}

    def get_preflight_checks(self, alert_type: str) -> list[dict]:
        """Get pre-flight check definitions from the alert policy.

        Returns a list of dicts: [{"name": ..., "sql": ..., "expected": ...}]
        """
        policy = self._loader.load_alert(alert_type)
        section = policy.get("preflight_checks", policy.get("pre_flight_checks", {}))
        if not section:
            return []

        checks: list[dict] = []
        # Parse bullet items: "Check name: expected value"
        items = []
        if isinstance(section, dict):
            items = section.get("items", [])
            # Also grab any SQL code blocks
            sql_code = section.get("sql", "")
            if isinstance(sql_code, list):
                sql_list = sql_code
            elif sql_code:
                sql_list = [sql_code]
            else:
                sql_list = []
        elif isinstance(section, str):
            return []
        else:
            return []

        # Match items to SQL blocks: each item defines a check name + expected
        for i, item in enumerate(items):
            name = item
            expected = ""
            if " -- " in item:
                name, expected = item.split(" -- ", 1)
            elif ": " in item:
                name, expected = item.split(": ", 1)

            sql = sql_list[i] if i < len(sql_list) else ""
            checks.append(
                {
                    "name": name.strip().strip("`"),
                    "sql": sql.strip(),
                    "expected": expected.strip(),
                }
            )

        return checks

    def get_action_type(self, alert_type: str) -> str:
        """Get the action_type from the alert policy frontmatter.

        Falls back to alert_type.upper() if not specified in the .md file.
        This enables dynamic alert types without hardcoded enum mapping.
        """
        policy = self._loader.load_alert(alert_type)
        fm = policy.get("frontmatter", {})
        action = fm.get("action_type", "")
        action = str(action).strip().upper()
        return action if action else alert_type.upper()

    def get_severity(self, alert_type: str) -> str:
        """Get the severity from the alert policy frontmatter."""
        policy = self._loader.load_alert(alert_type)
        fm = policy.get("frontmatter", {})
        severity = fm.get("severity", "HIGH")
        return str(severity).strip().upper()

    def get_all_patterns(self) -> dict[str, re.Pattern]:
        """Load and compile all alert email patterns."""
        all_alerts = self._loader.load_all_alerts()
        for alert_type in all_alerts:
            try:
                self.get_email_pattern(alert_type)
            except PolicyLoadError:
                logger.warning("Skipping alert %s: no valid regex pattern", alert_type)
        return dict(self._compiled_patterns)
