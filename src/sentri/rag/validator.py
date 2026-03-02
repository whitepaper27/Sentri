"""SQL Validator — checks LLM-generated SQL against ground truth hard rules.

Post-generation safety gate: if a rule is violated, the option is dropped.
Better no action than wrong action.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sentri.rag.manager import RuleDoc, RuleViolation, ValidationResult

logger = logging.getLogger("sentri.rag.validator")


class SQLValidator:
    """Validate LLM-generated SQL against ground truth hard rules."""

    def __init__(self, environment_repo=None):
        self._env_repo = environment_repo

    def validate(
        self,
        sql: str,
        rules: list[RuleDoc],
        database_id: str = "",
    ) -> ValidationResult:
        """Check SQL against all applicable rules.

        For each rule:
        1. Check if the detection_pattern regex matches the SQL
        2. If matched, check if the condition applies given DB context
        3. If condition applies, record a violation

        Returns ValidationResult with all violations found.
        """
        if not sql or not rules:
            return ValidationResult(
                is_valid=True,
                violations=[],
                checked_rules=len(rules),
            )

        # Load database context once (for condition checks)
        db_context = self._get_database_context(database_id)

        violations = []
        for rule in rules:
            violation = self._check_rule(sql, rule, db_context)
            if violation:
                violations.append(violation)
                logger.warning(
                    "SQL validation: rule '%s' [%s] violated — %s",
                    rule.rule_id,
                    rule.severity,
                    violation.message,
                )

        is_valid = len(violations) == 0
        if is_valid:
            logger.info(
                "SQL validation passed: %d rules checked, no violations",
                len(rules),
            )

        return ValidationResult(
            is_valid=is_valid,
            violations=violations,
            checked_rules=len(rules),
        )

    # ------------------------------------------------------------------
    # Rule checking
    # ------------------------------------------------------------------

    def _check_rule(self, sql: str, rule: RuleDoc, db_context: dict) -> Optional[RuleViolation]:
        """Check a single rule against SQL. Returns violation or None."""
        # Step 1: Does the detection pattern match?
        if not rule.detection_pattern:
            return None

        try:
            pattern = re.compile(rule.detection_pattern, re.IGNORECASE)
        except re.error as e:
            logger.warning("Invalid regex in rule '%s': %s", rule.rule_id, e)
            return None

        match = pattern.search(sql)
        if not match:
            return None  # Pattern doesn't match — rule not triggered

        # Step 2: Check if condition applies
        if not self._condition_applies(rule, db_context):
            return None  # Condition not met — not a violation

        # Step 3: It's a violation
        return RuleViolation(
            rule_id=rule.rule_id,
            severity=rule.severity,
            message=f"Rule '{rule.rule_id}' violated: {rule.condition}",
            sql_fragment=match.group(0),
            suggested_fix=rule.required_action,
        )

    def _condition_applies(self, rule: RuleDoc, db_context: dict) -> bool:
        """Check if the rule's condition applies given database context.

        Conditions are extracted from the rule's ## Condition section.
        We parse known condition patterns and check against db_context.
        """
        condition = rule.condition.lower() if rule.condition else ""

        if not condition:
            # No condition specified — rule always applies when pattern matches
            return True

        # Check for known condition patterns
        if "bigfile" in condition:
            return self._check_bigfile_condition(db_context)

        if "omf" in condition or "db_create_file_dest" in condition:
            return self._check_omf_condition(db_context)

        if "cdb" in condition:
            return self._check_cdb_condition(db_context)

        if "read" in condition and "only" in condition:
            return self._check_read_only_condition(db_context)

        # Unknown condition — be conservative, assume it applies
        logger.debug(
            "Unknown condition for rule '%s': %s — assuming applies",
            rule.rule_id,
            rule.condition,
        )
        return True

    # ------------------------------------------------------------------
    # Condition checkers
    # ------------------------------------------------------------------

    def _check_bigfile_condition(self, db_context: dict) -> bool:
        """Check if the target tablespace is BIGFILE."""
        tablespace_type = db_context.get("tablespace_type", "").upper()
        return tablespace_type == "BIGFILE"

    def _check_omf_condition(self, db_context: dict) -> bool:
        """Check if OMF is enabled (db_create_file_dest is set)."""
        return db_context.get("omf_enabled", False)

    def _check_cdb_condition(self, db_context: dict) -> bool:
        """Check if the database is a CDB (container database)."""
        return db_context.get("is_cdb", False)

    def _check_read_only_condition(self, db_context: dict) -> bool:
        """Check if the database is in read-only mode."""
        open_mode = db_context.get("open_mode", "").upper()
        return "READ ONLY" in open_mode

    # ------------------------------------------------------------------
    # Database context loading
    # ------------------------------------------------------------------

    def _get_database_context(self, database_id: str) -> dict:
        """Build database context dict from profile for condition checking.

        Extracts key properties from the DatabaseProfile stored in
        environment_registry: tablespace_type, omf_enabled, is_cdb, etc.
        """
        if not database_id or not self._env_repo:
            return {}

        try:
            profile_json = self._env_repo.get_profile(database_id)
            if not profile_json:
                return {}

            data = json.loads(profile_json)
            db_config = data.get("db_config", data)

            context = {}

            # OMF enabled?
            context["omf_enabled"] = data.get("omf_enabled", False)

            # Is CDB?
            context["is_cdb"] = data.get("is_cdb", False)

            # Is RAC?
            context["is_rac"] = data.get("is_rac", False)

            # Open mode (from db_identity)
            db_identity = db_config.get("db_identity", [])
            if db_identity and isinstance(db_identity, list):
                context["open_mode"] = db_identity[0].get("open_mode", "")
                context["database_role"] = db_identity[0].get("database_role", "")

            # Tablespace type — we can't know from the profile alone which
            # tablespace the alert is about. The validator checks this at
            # the rule level; the caller can enrich db_context with
            # tablespace-specific info from the DBA tools.
            # Default to empty (not BIGFILE) — bigfile check will be false.

            return context

        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            logger.warning("Failed to load DB context for %s: %s", database_id, e)
            return {}
