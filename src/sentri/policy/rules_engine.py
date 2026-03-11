"""Execution rules engine — reads brain/rules.md and enforces safety rules.

Provides a single entry point: evaluate() returns a RuleVerdict that tells
the orchestrator whether to ALLOW, REQUIRE_APPROVAL, or BLOCK an action.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .loader import PolicyLoader

logger = logging.getLogger("sentri.policy.rules_engine")


# Severity ordering for verdict comparison (higher = more restrictive)
_VERDICT_ORDER = {"ALLOW": 0, "REQUIRE_APPROVAL": 1, "BLOCK": 2}


class Verdict(str, Enum):
    """Possible outcomes of a rule evaluation."""

    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCK = "BLOCK"

    @property
    def severity(self) -> int:
        return _VERDICT_ORDER[self.value]


@dataclass
class RuleVerdict:
    """Result of evaluating all rules for a workflow action."""

    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    blocked_by: Optional[str] = None  # Which rule section blocked it

    @property
    def allowed(self) -> bool:
        return self.verdict == Verdict.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.verdict == Verdict.REQUIRE_APPROVAL

    @property
    def blocked(self) -> bool:
        return self.verdict == Verdict.BLOCK


class RulesEngine:
    """Parse brain/rules.md and enforce execution rules."""

    # Default confidence thresholds (overridden by brain/rules.md if present)
    _DEFAULT_CONFIDENCE_BLOCK = 0.60
    _DEFAULT_CONFIDENCE_APPROVAL = 0.80

    def __init__(self, policy_loader: PolicyLoader):
        self._loader = policy_loader
        self._rules: dict = {}
        self._action_matrix: dict[str, dict[str, str]] = {}
        self._protected_sessions: set[str] = set()
        self._protected_programs: list[str] = []
        self._protected_schemas: set[str] = set()
        self._protected_databases: dict[str, str] = {}
        self._confidence_block: float = self._DEFAULT_CONFIDENCE_BLOCK
        self._confidence_approval: float = self._DEFAULT_CONFIDENCE_APPROVAL
        self.circuit_breaker_threshold: int = 3
        self.circuit_breaker_hours: int = 24
        self.rca_alert_count: int = 3
        self.rca_window_hours: int = 24
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load and parse rules.md on first use."""
        if self._loaded:
            return
        self._rules = self._loader.load_brain("rules")
        if not self._rules:
            logger.warning("No rules.md found — all actions allowed by default")
            self._loaded = True
            return
        self._parse_confidence_thresholds()
        self._parse_action_matrix()
        self._parse_protected_sessions()
        self._parse_protected_schemas()
        self._parse_protected_databases()
        self._parse_session_kill_rules()
        self._parse_circuit_breaker()
        self._parse_rca_thresholds()
        self._loaded = True
        logger.info(
            "Rules loaded: %d action types, %d protected sessions, %d protected DBs, "
            "confidence block=%.2f approval=%.2f, circuit breaker=%d failures/%dh",
            len(self._action_matrix),
            len(self._protected_sessions),
            len(self._protected_databases),
            self._confidence_block,
            self._confidence_approval,
            self.circuit_breaker_threshold,
            self.circuit_breaker_hours,
        )

    def reload(self) -> None:
        """Force reload of rules from disk."""
        self._loaded = False
        self._loader.reload()
        self._ensure_loaded()

    # ------------------------------------------------------------------
    # Main evaluation entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        action_type: str,
        environment: str,
        database_id: str = "",
        confidence: float = 1.0,
        target_session_user: str = "",
        target_program: str = "",
        recent_same_alerts: int = 0,
        hours_since_last_same: float = 999,
    ) -> RuleVerdict:
        """Evaluate all rules and return the most restrictive verdict.

        Args:
            action_type: e.g. KILL_SESSION, ADD_DATAFILE, START_LISTENER
            environment: DEV, UAT, PROD
            database_id: Target database identifier
            confidence: Verification confidence score (0.0 - 1.0)
            target_session_user: Username of the session to be killed (if applicable)
            target_program: Program name of the target session (if applicable)
            recent_same_alerts: Number of same alert+DB in last 24 hours
            hours_since_last_same: Hours since last identical alert on same DB
        """
        self._ensure_loaded()

        reasons: list[str] = []
        verdict = Verdict.ALLOW

        # 1. Check confidence thresholds
        v = self._check_confidence(confidence)
        if v.verdict.severity > verdict.severity:
            verdict = v.verdict
            reasons.extend(v.reasons)
        if v.blocked:
            return RuleVerdict(Verdict.BLOCK, reasons, "confidence_thresholds")

        # 2. Check action + environment matrix
        v = self._check_action_environment(action_type, environment)
        if v.verdict.severity > verdict.severity:
            verdict = v.verdict
            reasons.extend(v.reasons)

        # 3. Check protected databases
        v = self._check_protected_database(database_id)
        if v.verdict.severity > verdict.severity:
            verdict = v.verdict
            reasons.extend(v.reasons)

        # 4. Check protected sessions (for KILL_SESSION actions)
        if action_type == "KILL_SESSION" and target_session_user:
            v = self._check_protected_session(target_session_user)
            if v.blocked:
                return RuleVerdict(Verdict.BLOCK, reasons + v.reasons, "protected_sessions")
            if v.verdict.severity > verdict.severity:
                verdict = v.verdict
                reasons.extend(v.reasons)

        # 5. Check protected programs (for KILL_SESSION actions)
        if action_type == "KILL_SESSION" and target_program:
            v = self._check_protected_program(target_program)
            if v.blocked:
                return RuleVerdict(Verdict.BLOCK, reasons + v.reasons, "protected_programs")
            if v.verdict.severity > verdict.severity:
                verdict = v.verdict
                reasons.extend(v.reasons)

        # 6. Repeat alert observability (logs patterns, never blocks)
        self._check_repeat_alerts(recent_same_alerts, hours_since_last_same)

        # 7. Check protected schemas (for DDL/DML)
        # This would be checked at SQL-level — handled by the executor

        return RuleVerdict(verdict, reasons)

    # ------------------------------------------------------------------
    # Individual rule checks
    # ------------------------------------------------------------------

    def _check_confidence(self, confidence: float) -> RuleVerdict:
        """Check confidence threshold rules (parsed from brain/rules.md)."""
        if confidence < self._confidence_block:
            return RuleVerdict(
                Verdict.BLOCK,
                [
                    f"Confidence {confidence:.2f} < {self._confidence_block:.2f} — escalate, do not execute"
                ],
            )
        if confidence < self._confidence_approval:
            return RuleVerdict(
                Verdict.REQUIRE_APPROVAL,
                [
                    f"Confidence {confidence:.2f} < {self._confidence_approval:.2f} — require approval"
                ],
            )
        return RuleVerdict(Verdict.ALLOW)

    def _check_action_environment(self, action_type: str, environment: str) -> RuleVerdict:
        """Check action type + environment rules from the Action Rules table."""
        action_upper = action_type.upper()
        env_upper = environment.upper()

        # Look up in parsed action matrix
        if action_upper in self._action_matrix:
            rule = self._action_matrix[action_upper].get(env_upper, "approval")
            if rule == "approval":
                return RuleVerdict(
                    Verdict.REQUIRE_APPROVAL,
                    [f"{action_upper} requires approval in {env_upper} (rules.md)"],
                )
            # "auto" = allowed
            return RuleVerdict(Verdict.ALLOW)

        # Action not in matrix — PROD always requires approval
        if env_upper == "PROD":
            return RuleVerdict(
                Verdict.REQUIRE_APPROVAL,
                ["PROD: all actions require approval (rules.md)"],
            )

        return RuleVerdict(Verdict.ALLOW)

    def _check_protected_database(self, database_id: str) -> RuleVerdict:
        """Check if the database has special restrictions."""
        db_upper = database_id.upper()
        for protected_db, rule_text in self._protected_databases.items():
            if protected_db.upper() in db_upper:
                return RuleVerdict(
                    Verdict.REQUIRE_APPROVAL,
                    [f"Protected database {database_id}: {rule_text}"],
                )
        return RuleVerdict(Verdict.ALLOW)

    def _check_protected_session(self, username: str) -> RuleVerdict:
        """Check if the target session belongs to a protected user."""
        if username.upper() in self._protected_sessions:
            return RuleVerdict(
                Verdict.BLOCK,
                [f"Cannot kill protected session: {username} (rules.md)"],
            )
        return RuleVerdict(Verdict.ALLOW)

    def _check_protected_program(self, program: str) -> RuleVerdict:
        """Check if the target program is a protected Oracle background process."""
        prog_lower = program.lower()
        for pattern in self._protected_programs:
            if pattern.lower() in prog_lower:
                return RuleVerdict(
                    Verdict.BLOCK,
                    [f"Cannot kill protected program: {program} matches '{pattern}' (rules.md)"],
                )
        return RuleVerdict(Verdict.ALLOW)

    def _check_repeat_alerts(self, recent_count: int, hours_since_last: float) -> RuleVerdict:
        """Log repeat alert patterns for observability. Never blocks.

        Blocking decisions belong to the DBA via the action+environment
        matrix in rules.md and per-database autonomy in environments/*.md.
        The circuit breaker (Safety Mesh) catches genuinely broken scenarios.
        """
        # Observability only — log patterns, never block
        if hours_since_last < 6:
            logger.info(
                "Repeat alert: same alert fired %.1fh ago (< 6h) — proceeding per policy",
                hours_since_last,
            )
        if recent_count >= 3:
            logger.info(
                "Repeat alert: same alert fired %dx in 24h — consider root cause investigation",
                recent_count,
            )
        return RuleVerdict(Verdict.ALLOW)

    # ------------------------------------------------------------------
    # Convenience query methods
    # ------------------------------------------------------------------

    def is_session_protected(self, username: str) -> bool:
        """Quick check if a username is in the protected sessions list."""
        self._ensure_loaded()
        return username.upper() in self._protected_sessions

    def is_program_protected(self, program: str) -> bool:
        """Quick check if a program matches a protected pattern."""
        self._ensure_loaded()
        prog_lower = program.lower()
        return any(p.lower() in prog_lower for p in self._protected_programs)

    def get_action_rule(self, action_type: str, environment: str) -> str:
        """Get the rule for an action+environment combo: 'auto' or 'approval'."""
        self._ensure_loaded()
        action_upper = action_type.upper()
        env_upper = environment.upper()
        if action_upper in self._action_matrix:
            return self._action_matrix[action_upper].get(env_upper, "approval")
        if env_upper == "PROD":
            return "approval"
        return "auto"

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_confidence_thresholds(self) -> None:
        """Parse the Confidence Thresholds table from rules.md.

        Expected format:
        | Confidence | Action |
        |------------|--------|
        | < 0.60 | Escalate to DBA. Do not execute. |
        | 0.60 - 0.79 | Run pre-flight checks. Require approval regardless of environment. |
        | >= 0.95 | Follow environment rules above. |

        Extracts the block threshold (< X → BLOCK) and approval threshold
        (X - Y → REQUIRE_APPROVAL). Falls back to defaults if parsing fails.
        """
        section = self._rules.get("confidence_thresholds", {})
        text = ""
        if isinstance(section, str):
            text = section
        elif isinstance(section, dict):
            text = section.get("text", "")

        if not text:
            logger.debug("No confidence thresholds table found — using defaults")
            return

        block_threshold = None
        approval_upper = None

        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 2:
                continue

            conf_cell = cells[0].lower()
            action_cell = cells[1].lower()

            # Skip header row
            if "confidence" in conf_cell and "action" in action_cell:
                continue

            try:
                # Pattern: "< 0.60" → block threshold
                m = re.match(r"<\s*([\d.]+)", conf_cell)
                if m and (
                    "escalate" in action_cell
                    or "do not execute" in action_cell
                    or "block" in action_cell
                ):
                    block_threshold = float(m.group(1))
                    continue

                # Pattern: "0.60 - 0.79" → approval range (upper bound = approval threshold)
                m = re.match(r"([\d.]+)\s*[-–]\s*([\d.]+)", conf_cell)
                if m and ("approval" in action_cell or "require" in action_cell):
                    # The upper bound + 0.01 gives us the approval threshold
                    # E.g., "0.60 - 0.79" means approval needed below 0.80
                    approval_upper = float(m.group(2))
                    continue
            except (ValueError, IndexError):
                continue

        # Apply parsed values
        if block_threshold is not None:
            self._confidence_block = block_threshold
            logger.debug("Confidence block threshold from rules.md: %.2f", block_threshold)

        if approval_upper is not None:
            # "0.60 - 0.79" means anything < 0.80 needs approval
            # Round to avoid float precision issues (0.79 + 0.01 = 0.80)
            self._confidence_approval = round(approval_upper + 0.01, 2)
            logger.debug(
                "Confidence approval threshold from rules.md: %.2f", self._confidence_approval
            )

    def _parse_action_matrix(self) -> None:
        """Parse the Action Rules table into a lookup dict.

        Expected format in rules.md:
        | Action Type | DEV | UAT | PROD | Notes |
        |-------------|-----|-----|------|-------|
        | ADD_DATAFILE | auto | auto | approval | ... |
        """
        section = self._rules.get("action_rules", {})
        text = ""
        if isinstance(section, str):
            text = section
        elif isinstance(section, dict):
            text = section.get("text", "")

        if not text:
            return

        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 4:
                continue
            action = cells[0].upper()
            # Skip header row
            if action in ("ACTION TYPE", "ACTION_TYPE"):
                continue
            dev = cells[1].lower().strip()
            uat = cells[2].lower().strip()
            prod = cells[3].lower().strip()
            self._action_matrix[action] = {
                "DEV": dev,
                "UAT": uat,
                "PROD": prod,
            }

        logger.debug("Action matrix: %s", self._action_matrix)

    def _parse_protected_sessions(self) -> None:
        """Parse the Protected Sessions list."""
        section = self._rules.get("protected_sessions__never_kill", {})
        if not section:
            # Try alternative key names
            for key in self._rules:
                if "protected_session" in key:
                    section = self._rules[key]
                    break

        items = []
        if isinstance(section, dict):
            items = section.get("items", [])
        elif isinstance(section, list):
            items = section

        for item in items:
            # Items might be just the username or "USERNAME — description"
            username = item.split("—")[0].split("-")[0].strip()
            if username:
                self._protected_sessions.add(username.upper())

        logger.debug("Protected sessions: %s", self._protected_sessions)

    def _parse_protected_schemas(self) -> None:
        """Parse the Protected Schemas list."""
        section = self._rules.get("protected_schemas__never_modify", {})
        if not section:
            for key in self._rules:
                if "protected_schema" in key:
                    section = self._rules[key]
                    break

        items = []
        if isinstance(section, dict):
            items = section.get("items", [])
        elif isinstance(section, list):
            items = section

        for item in items:
            schema = item.split("—")[0].split("-")[0].strip()
            if schema:
                self._protected_schemas.add(schema.upper())

    def _parse_protected_databases(self) -> None:
        """Parse the Protected Databases table."""
        section = self._rules.get("protected_databases", {})
        text = ""
        if isinstance(section, str):
            text = section
        elif isinstance(section, dict):
            text = section.get("text", "")

        if not text:
            return

        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 2:
                continue
            db_name = cells[0]
            if db_name.upper() in ("DATABASE", "DATABASE_ID"):
                continue
            self._protected_databases[db_name] = cells[1]

    def _parse_session_kill_rules(self) -> None:
        """Parse session kill rules for protected programs."""
        section = self._rules.get("session_kill_rules", {})
        items = []
        if isinstance(section, dict):
            items = section.get("items", [])

        for item in items:
            # Look for rule #3: programs containing oracle@, tnslsnr, etc.
            if "program contains" in item.lower():
                # Extract the backtick-delimited patterns
                patterns = re.findall(r"`([^`]+)`", item)
                self._protected_programs.extend(patterns)

        logger.debug("Protected programs: %s", self._protected_programs)

    def _parse_circuit_breaker(self) -> None:
        """Parse circuit breaker config from rules.md.

        Expected format in rules.md:
          ## Circuit Breaker
          | Setting | Value |
          |---------|-------|
          | failure_threshold | 3 |
          | window_hours | 24 |
        """
        section = self._rules.get("circuit_breaker", {})
        text = ""
        if isinstance(section, str):
            text = section
        elif isinstance(section, dict):
            text = section.get("text", "")

        if not text:
            return

        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 2:
                continue
            key = cells[0].lower().replace(" ", "_")
            try:
                val = int(cells[1])
            except ValueError:
                continue
            if "threshold" in key:
                self.circuit_breaker_threshold = val
            elif "hour" in key or "window" in key:
                self.circuit_breaker_hours = val

    def _parse_rca_thresholds(self) -> None:
        """Parse RCA recommendation thresholds from rules.md.

        Expected format:
          ### RCA Recommendation Thresholds
          | Setting | Value | Description |
          | rca_alert_count | 3 | ... |
          | rca_window_hours | 24 | ... |
        """
        section = self._rules.get("rca_recommendation_thresholds", {})
        text = ""
        if isinstance(section, str):
            text = section
        elif isinstance(section, dict):
            text = section.get("text", "")

        if not text:
            return

        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 2:
                continue
            key = cells[0].lower().replace(" ", "_")
            try:
                val = int(cells[1])
            except ValueError:
                continue
            if "count" in key:
                self.rca_alert_count = val
            elif "hour" in key or "window" in key:
                self.rca_window_hours = val
