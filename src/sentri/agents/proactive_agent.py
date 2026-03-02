"""Proactive Agent — scheduled health checker.

Catches problems before they trigger alerts. Runs health queries from
checks/*.md against all configured databases on a schedule. Creates
finding workflows (alert_type='check_finding:{check_type}') that enter
the Supervisor for routing to the appropriate specialist.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sentri.core.models import Workflow
from sentri.policy.check_patterns import CheckPatterns

from .base import AgentContext, BaseAgent

if TYPE_CHECKING:
    pass

logger = logging.getLogger("sentri.agents.proactive_agent")

# Schedule intervals in seconds
SCHEDULE_INTERVALS = {
    "every_6_hours": 6 * 3600,
    "daily": 24 * 3600,
    "weekly": 7 * 24 * 3600,
}


@dataclass
class CheckState:
    """Tracks when a check was last run."""

    check_type: str
    last_run: Optional[datetime] = None
    interval_seconds: int = 86400  # default: daily


class ProactiveAgent(BaseAgent):
    """Scheduled health checker — catches problems before alerts trigger.

    Not a SpecialistBase. This agent does not fix things; it detects
    potential issues and creates workflows for specialists to handle.
    """

    def __init__(
        self,
        context: AgentContext,
        alert_event: Optional[threading.Event] = None,
    ):
        super().__init__("proactive_agent", context)
        self._check_patterns = CheckPatterns(context.policy_loader)
        self._alert_event = alert_event
        self._stop = False
        self._check_states: dict[str, CheckState] = {}
        self._loaded = False

    def process(self, workflow_id: str) -> dict:
        """Not used directly — ProactiveAgent runs its own loop."""
        return {"status": "success", "agent": self.name}

    def run_loop(self, poll_interval: int = 300) -> None:
        """Main loop: check schedules and run due checks.

        Args:
            poll_interval: How often to check if any health checks are due (seconds).
        """
        logger.info("ProactiveAgent started, polling every %ds", poll_interval)

        while not self._stop:
            try:
                self._run_due_checks()
            except Exception as e:
                logger.error("ProactiveAgent cycle error: %s", e)

            # Sleep in small increments so stop() is responsive
            for _ in range(poll_interval):
                if self._stop:
                    break
                time.sleep(1)

        logger.info("ProactiveAgent stopped")

    def stop(self) -> None:
        """Signal the agent to stop."""
        self._stop = True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load check definitions on first use."""
        if self._loaded:
            return
        self._load_check_definitions()
        self._loaded = True

    def _load_check_definitions(self) -> None:
        """Scan checks/ directory and initialize check states."""
        all_checks = self._check_patterns.get_all_checks()
        for check_type, info in all_checks.items():
            schedule = info.get("schedule", "daily")
            interval = SCHEDULE_INTERVALS.get(schedule, SCHEDULE_INTERVALS["daily"])
            self._check_states[check_type] = CheckState(
                check_type=check_type,
                interval_seconds=interval,
            )
        logger.info("Loaded %d health check definitions", len(self._check_states))

    def _run_due_checks(self) -> None:
        """Check schedule and run any checks that are due."""
        self._ensure_loaded()
        now = datetime.now(timezone.utc)

        for check_type, state in self._check_states.items():
            if self._stop:
                break
            if self._is_due(state, now):
                try:
                    findings_count = self._run_single_check(check_type)
                    state.last_run = now
                    if findings_count > 0:
                        logger.info(
                            "Check '%s' found %d issues",
                            check_type,
                            findings_count,
                        )
                except Exception as e:
                    logger.error("Check '%s' failed: %s", check_type, e)
                    state.last_run = now  # Don't retry immediately on error

    def _is_due(self, state: CheckState, now: datetime) -> bool:
        """Check if a health check is due to run."""
        if state.last_run is None:
            return True
        elapsed = (now - state.last_run).total_seconds()
        return elapsed >= state.interval_seconds

    def _run_single_check(self, check_type: str) -> int:
        """Run a health check against all configured databases.

        Returns the number of finding workflows created.
        """
        health_query = self._check_patterns.get_health_query(check_type)
        if not health_query:
            logger.warning("No health query for check '%s'", check_type)
            return 0

        threshold = self._check_patterns.get_threshold(check_type)
        findings_count = 0

        # Run against each configured database
        databases = self.context.settings.databases or []
        for db_cfg in databases:
            if self._stop:
                break
            try:
                findings = self._execute_health_query(
                    db_cfg.name,
                    health_query,
                )
                if findings and self._exceeds_threshold(findings, threshold):
                    wf_id = self._create_finding_workflow(
                        check_type,
                        db_cfg,
                        findings,
                    )
                    if wf_id:
                        findings_count += 1
            except Exception as e:
                logger.warning(
                    "Health check '%s' failed on %s: %s",
                    check_type,
                    db_cfg.name,
                    e,
                )

        return findings_count

    def _execute_health_query(
        self,
        database_id: str,
        query: str,
    ) -> list[dict]:
        """Execute a health query against a database.

        Returns list of result dicts, or empty list if pool unavailable.
        """
        if not self.context.oracle_pool:
            return []

        try:
            return self.context.oracle_pool.execute_query(
                database_id,
                query,
                timeout=30,
            )
        except Exception as e:
            logger.warning(
                "Health query failed on %s: %s",
                database_id,
                e,
            )
            return []

    def _exceeds_threshold(
        self,
        findings: list[dict],
        threshold: dict,
    ) -> bool:
        """Check if findings exceed the configured thresholds.

        Returns True if any finding exceeds any numeric threshold.
        """
        if not findings or not threshold:
            return bool(findings)  # Any result = exceeds if no threshold

        for row in findings:
            for key, limit in threshold.items():
                if key in row:
                    try:
                        actual = float(row[key])
                        limit_val = float(limit)
                        if actual > limit_val:
                            return True
                    except (ValueError, TypeError):
                        continue

        return False

    def _create_finding_workflow(
        self,
        check_type: str,
        db_cfg,
        findings: list[dict],
    ) -> Optional[str]:
        """Create a workflow for a health check finding.

        The workflow alert_type is 'check_finding:{check_type}' so the
        Supervisor routes it via wildcard matching.
        """
        import json

        alert_type = f"check_finding:{check_type}"
        severity = self._check_patterns.get_severity(check_type)

        # Check for duplicate — don't create if recent finding exists
        existing = self.context.db.execute_read(
            """SELECT id FROM workflows
               WHERE alert_type = ? AND database_id = ?
               AND created_at > datetime('now', '-6 hours')
               LIMIT 1""",
            (alert_type, db_cfg.name),
        )
        if existing:
            logger.debug(
                "Skipping duplicate finding: %s on %s",
                alert_type,
                db_cfg.name,
            )
            return None

        wf = Workflow(
            alert_type=alert_type,
            database_id=db_cfg.name,
            environment=getattr(db_cfg, "environment", "DEV"),
            status="DETECTED",
            suggestion=json.dumps(
                {
                    "check_type": check_type,
                    "severity": severity,
                    "findings": findings[:10],  # Cap at 10 rows
                    "routes_to": self._check_patterns.get_routes_to(check_type),
                }
            ),
        )
        self.context.workflow_repo.create(wf)

        logger.info(
            "Created finding workflow %s: %s on %s",
            wf.id,
            alert_type,
            db_cfg.name,
        )

        # Signal the Supervisor to pick up the new workflow
        if self._alert_event:
            self._alert_event.set()

        return wf.id
