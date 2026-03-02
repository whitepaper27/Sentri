"""Orchestrator engine: main loop that routes workflows through agents."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from sentri.agents.auditor import AuditorAgent
from sentri.agents.base import AgentContext
from sentri.core.constants import (
    ORCHESTRATOR_POLL_INTERVAL,
    WorkflowStatus,
)
from sentri.core.models import ExecutionPlan, Workflow
from sentri.orchestrator.approval import ApprovalRouter
from sentri.orchestrator.state_machine import StateMachine, is_terminal
from sentri.policy.alert_patterns import AlertPatterns
from sentri.policy.brain_policies import BrainPolicies
from sentri.policy.rules_engine import RulesEngine

logger = logging.getLogger("sentri.orchestrator.engine")


class Orchestrator:
    """Main orchestration loop: polls for actionable workflows and routes them."""

    def __init__(
        self,
        context: AgentContext,
        alert_event: threading.Event,
    ):
        self.context = context
        self._alert_event = alert_event
        self._stop = False

        self._state_machine = StateMachine(context.workflow_repo)
        self._brain = BrainPolicies(context.policy_loader)
        self._approval = ApprovalRouter(self._brain)
        self._alert_patterns = AlertPatterns(context.policy_loader)
        self._rules = RulesEngine(context.policy_loader)

        # Agents
        self._auditor = AuditorAgent(context)
        self._executor = None  # Set via set_executor()
        self._researcher = None  # Set via set_researcher()
        self._analyst = None  # Set via set_analyst()

    def set_executor(self, executor) -> None:
        """Inject the executor agent (avoids circular imports)."""
        self._executor = executor

    def set_researcher(self, researcher) -> None:
        """Inject the researcher agent (avoids circular imports)."""
        self._researcher = researcher

    def set_analyst(self, analyst) -> None:
        """Inject the analyst agent (avoids circular imports)."""
        self._analyst = analyst

    def run(self, poll_interval: int = ORCHESTRATOR_POLL_INTERVAL) -> None:
        """Main orchestrator loop. Runs until stop() is called."""
        logger.info("Orchestrator started, polling every %ds", poll_interval)

        while not self._stop:
            self._alert_event.wait(timeout=poll_interval)
            self._alert_event.clear()

            try:
                self._process_cycle()
            except Exception as e:
                logger.error("Orchestrator cycle error: %s", e)

        logger.info("Orchestrator stopped")

    def stop(self) -> None:
        """Signal the orchestrator to stop."""
        self._stop = True
        self._alert_event.set()  # Wake up from wait

    def _process_cycle(self) -> None:
        """Process all actionable workflows."""
        workflows = self.context.workflow_repo.find_actionable()

        for wf in workflows:
            if self._stop:
                break
            try:
                self._process_workflow(wf)
            except Exception as e:
                logger.error("Workflow %s error: %s", wf.id, e)

    def _process_workflow(self, wf: Workflow) -> None:
        """Route a single workflow based on its current status."""
        if is_terminal(wf.status):
            return

        if wf.status == WorkflowStatus.DETECTED.value:
            self._handle_detected(wf)
        elif wf.status == WorkflowStatus.VERIFIED.value:
            self._handle_verified(wf)
        elif wf.status == WorkflowStatus.APPROVED.value:
            self._handle_approved(wf)
        elif wf.status == WorkflowStatus.AWAITING_APPROVAL.value:
            self._check_approval_timeout(wf)

    def _handle_detected(self, wf: Workflow) -> None:
        """DETECTED -> VERIFYING -> VERIFIED or VERIFICATION_FAILED."""
        self._state_machine.transition(wf.id, WorkflowStatus.VERIFYING.value)

        result = self._auditor.process(wf.id)

        if result["status"] == "verified":
            # Auditor already updated status to VERIFIED
            # Now handle the verified workflow immediately
            updated_wf = self.context.workflow_repo.get(wf.id)
            if updated_wf and updated_wf.status == WorkflowStatus.VERIFIED.value:
                self._handle_verified(updated_wf)
        # If failed, auditor already set VERIFICATION_FAILED

    def _handle_verified(self, wf: Workflow) -> None:
        """VERIFIED -> research options -> evaluate rules -> route action."""
        # Run researcher to generate remediation options
        selected_option = None
        if self._researcher:
            try:
                research_result = self._researcher.process(wf.id)
                if research_result.get("status") == "success":
                    selected_option = research_result.get("selected_option")
                    logger.info(
                        "Researcher: %s source, %d options for %s",
                        research_result.get("source", "?"),
                        len(research_result.get("options", [])),
                        wf.id,
                    )
            except Exception as e:
                logger.warning("Researcher failed for %s: %s", wf.id, e)

        # Build execution plan (uses researcher's selected option if available)
        plan = self._build_execution_plan(wf, selected_option=selected_option)
        self.context.workflow_repo.update_status(wf.id, wf.status, execution_plan=plan.to_json())

        # Extract confidence and action type
        confidence = self._get_confidence(wf)
        action_type = self._alert_patterns.get_action_type(wf.alert_type)

        # Check repeat alerts
        recent_count, hours_since = self.context.workflow_repo.count_recent_same(
            wf.database_id, wf.alert_type, hours=24
        )

        # === RULES ENGINE EVALUATION ===
        verdict = self._rules.evaluate(
            action_type=action_type,
            environment=wf.environment,
            database_id=wf.database_id,
            confidence=confidence,
            recent_same_alerts=recent_count,
            hours_since_last_same=hours_since,
        )

        # Log the rule evaluation
        for reason in verdict.reasons:
            logger.info("Rule [%s]: %s — %s", wf.id, verdict.verdict.value, reason)

        # Act on the verdict
        if verdict.blocked:
            logger.warning(
                "BLOCKED by rules for %s (blocked_by=%s): %s",
                wf.id,
                verdict.blocked_by,
                "; ".join(verdict.reasons),
            )
            self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)
            return

        if verdict.needs_approval:
            logger.info(
                "Approval required by rules for %s: %s",
                wf.id,
                "; ".join(verdict.reasons),
            )
            if self._run_preflight(wf):
                self._request_approval(wf)
            return

        # ALLOW — run pre-flight, then execute
        if self._run_preflight(wf):
            self._execute(wf)

    def _get_confidence(self, wf: Workflow) -> float:
        """Extract confidence score from the verification report."""
        if not wf.verification:
            return 0.5  # No verification = conservative default, requires approval
        try:
            report = json.loads(wf.verification)
            return float(report.get("confidence", 0.5))
        except (json.JSONDecodeError, ValueError, TypeError):
            return 0.5

    def _run_preflight(self, wf: Workflow) -> bool:
        """Run pre-flight checks. Returns True if all passed, False if failed.

        On failure, transitions to PRE_FLIGHT_FAILED.
        """
        self._state_machine.transition(wf.id, WorkflowStatus.PRE_FLIGHT.value)

        from sentri.agents.preflight import PreFlightRunner, all_passed, format_results

        db_cfg = self.context.settings.get_database(wf.database_id)
        env_record = self.context.environment_repo.get(wf.database_id)

        conn_string = ""
        if env_record:
            conn_string = env_record.connection_string
        elif db_cfg:
            conn_string = db_cfg.connection_string

        if not conn_string or not db_cfg:
            logger.warning("No DB config for pre-flight on %s, skipping checks", wf.database_id)
            return True  # Can't run checks, proceed anyway

        # Extract params from suggestion for SQL bind vars
        params = {}
        if wf.suggestion:
            try:
                params = json.loads(wf.suggestion).get("extracted_data", {})
            except (json.JSONDecodeError, AttributeError):
                pass

        runner = PreFlightRunner(self._alert_patterns)
        checks = runner.run_checks(
            alert_type=wf.alert_type,
            database_id=wf.database_id,
            connection_string=conn_string,
            password=db_cfg.password if db_cfg else "",
            username=db_cfg.username if db_cfg and db_cfg.username else None,
            params=params,
        )

        if not checks:
            # No pre-flight checks defined — pass by default
            logger.info("No pre-flight checks for %s, proceeding", wf.alert_type)
            return True

        if all_passed(checks):
            logger.info("Pre-flight PASSED for %s:\n%s", wf.id, format_results(checks))
            return True

        # Some checks failed
        logger.warning("Pre-flight FAILED for %s:\n%s", wf.id, format_results(checks))
        check_json = json.dumps([c.to_json() for c in checks])
        self._state_machine.transition(
            wf.id,
            WorkflowStatus.PRE_FLIGHT_FAILED.value,
            metadata=check_json,
        )
        return False

    def _handle_approved(self, wf: Workflow) -> None:
        """APPROVED -> EXECUTING."""
        self._execute(wf)

    def _execute(self, wf: Workflow) -> None:
        """Transition to EXECUTING and run the executor agent."""
        # Safety: check for unresolved :placeholders in forward SQL
        if wf.execution_plan:
            try:
                plan_data = json.loads(wf.execution_plan)
                forward = plan_data.get("forward_sql", "")
                import re

                unresolved = re.findall(r":([a-z_]+)", forward, re.IGNORECASE)
                # Filter out known Oracle bind-var patterns (these are fine)
                real_unresolved = [p for p in unresolved if p.lower() not in ("name", "tbs", "sid")]
                if real_unresolved:
                    logger.warning(
                        "BLOCKED: forward SQL has unresolved placeholders %s — escalating %s",
                        real_unresolved,
                        wf.id,
                    )
                    self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)
                    return
            except (json.JSONDecodeError, AttributeError):
                pass

        self._state_machine.transition(wf.id, WorkflowStatus.EXECUTING.value)

        if self._executor:
            result = self._executor.process(wf.id)
            logger.info("Execution result for %s: %s", wf.id, result.get("status"))
            # After execution completes, run analyst for learning
            self._run_analyst(wf.id)
        else:
            logger.warning("No executor configured, marking %s as FAILED", wf.id)
            self._state_machine.transition(wf.id, WorkflowStatus.FAILED.value)

    def _run_analyst(self, workflow_id: str) -> None:
        """Run the analyst agent for learning observation (non-blocking)."""
        if not self._analyst:
            return
        try:
            result = self._analyst.process(workflow_id)
            obs = result.get("observation")
            if obs:
                logger.info(
                    "Analyst: %s observation for %s",
                    obs.get("type", "?"),
                    obs.get("alert_type", "?"),
                )
        except Exception as e:
            logger.warning("Analyst failed for %s: %s", workflow_id, e)

    def _request_approval(self, wf: Workflow) -> None:
        """Transition to AWAITING_APPROVAL and send approval request."""
        timeout_at = self._approval.calculate_timeout(wf)

        self._state_machine.transition(
            wf.id,
            WorkflowStatus.AWAITING_APPROVAL.value,
            approval_timeout=timeout_at,
        )

        # Build and send approval package
        package = self._approval.build_approval_package(self.context.workflow_repo.get(wf.id))
        message = self._approval.format_approval_message(package)
        logger.info("Approval requested for workflow %s:\n%s", wf.id, message[:200])

        # Send notifications (if configured)
        self._send_approval_notifications(package, message)

    def _send_approval_notifications(self, package: dict, message: str) -> None:
        """Send approval request via configured channels (email + Slack)."""
        settings = self.context.settings

        # Email notification
        if settings.approvals.email_enabled and settings.email.smtp_server:
            try:
                from sentri.notifications.email_sender import (
                    send_approval_request_email,
                )

                recipients_str = settings.approvals.approval_recipients or settings.email.username
                recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
                verification = package.get("verification", {})
                if recipients:
                    send_approval_request_email(
                        smtp_server=settings.email.smtp_server,
                        smtp_port=settings.email.smtp_port,
                        from_addr=settings.email.username,
                        to_addrs=recipients,
                        workflow_id=package.get("workflow_id", ""),
                        database_id=package.get("database", ""),
                        alert_type=package.get("alert_type", ""),
                        environment=package.get("environment", ""),
                        forward_sql=package.get("proposed_action", ""),
                        rollback_sql=package.get("rollback_plan", "N/A"),
                        risk_level=package.get("risk_level", "MEDIUM"),
                        confidence=float(verification.get("confidence", 0.0)),
                        reasons=[],
                        username=settings.email.username,
                        password=settings.email.password,
                        use_tls=settings.email.use_tls,
                    )
            except Exception as e:
                logger.warning("Failed to send approval email: %s", e)

        # Slack notification
        try:
            from sentri.notifications.slack import send_slack_message

            webhook = settings.approvals.slack_webhook_url
            if webhook:
                send_slack_message(webhook, message)
        except Exception as e:
            logger.warning("Failed to send Slack notification: %s", e)

    def _check_approval_timeout(self, wf: Workflow) -> None:
        """Check if an approval request has timed out."""
        if not wf.approval_timeout:
            return

        now = datetime.now(timezone.utc)
        try:
            timeout_at = datetime.fromisoformat(wf.approval_timeout)
            if timeout_at.tzinfo is None:
                timeout_at = timeout_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return

        if now > timeout_at:
            logger.warning("Approval timeout for workflow %s", wf.id)
            self._state_machine.transition(wf.id, WorkflowStatus.TIMEOUT.value)
            self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)

    def _build_execution_plan(self, wf: Workflow, selected_option=None) -> ExecutionPlan:
        """Build an execution plan from researcher output or alert policy."""
        # Use researcher's selected option if available
        if selected_option is not None:
            forward_sql = selected_option.forward_sql
            rollback_sql = selected_option.rollback_sql
            risk_level = selected_option.risk_level
        else:
            forward_sql = self._alert_patterns.get_forward_action(wf.alert_type)
            rollback_sql = self._alert_patterns.get_rollback_action(wf.alert_type)
            risk_level = self._alert_patterns.get_risk_level(wf.alert_type)

        validation_sql = self._alert_patterns.get_validation_query(wf.alert_type)

        # Substitute extracted data into SQL templates
        # DDL (forward/rollback) uses string substitution (bind vars not supported)
        # DML (validation) keeps :placeholders and passes params to query runner
        params = {}
        if wf.suggestion:
            try:
                suggestion_data = json.loads(wf.suggestion)
                extracted = suggestion_data.get("extracted_data", {})
                params = {k: v for k, v in extracted.items() if v is not None}
                for key, val in params.items():
                    placeholder = f":{key}"
                    if selected_option is None:
                        forward_sql = forward_sql.replace(placeholder, str(val))
                        rollback_sql = rollback_sql.replace(placeholder, str(val))
                    # validation_sql keeps :placeholders — params passed via bind vars
            except json.JSONDecodeError:
                pass

        # Apply profile-aware adjustments (OMF, CDB)
        # Skip if researcher already generated profile-aware SQL
        if selected_option is None:
            forward_sql, rollback_sql = self._apply_profile_awareness(
                wf.database_id, wf.alert_type, forward_sql, rollback_sql
            )

        # Resolve action type dynamically from the alert .md file
        action_type = self._alert_patterns.get_action_type(wf.alert_type)

        return ExecutionPlan(
            action_type=action_type,
            forward_sql=forward_sql,
            rollback_sql=rollback_sql,
            validation_sql=validation_sql,
            expected_outcome={"status": "resolved"},
            risk_level=risk_level,
            estimated_duration_seconds=30,
            params=params,
        )

    def _apply_profile_awareness(
        self,
        database_id: str,
        alert_type: str,
        forward_sql: str,
        rollback_sql: str,
    ) -> tuple[str, str]:
        """Adjust SQL based on database profile (OMF, CDB awareness).

        Returns (forward_sql, rollback_sql) — possibly modified.
        """
        profile_json = self.context.environment_repo.get_profile(database_id)
        if not profile_json:
            return forward_sql, rollback_sql

        try:
            from sentri.core.models import DatabaseProfile

            profile = DatabaseProfile.from_json(profile_json)
        except Exception:
            return forward_sql, rollback_sql

        # OMF-aware tablespace operations: remove explicit DATAFILE path
        # When OMF is enabled, Oracle auto-manages file locations
        if profile.omf_enabled and alert_type in ("tablespace_full", "temp_full"):
            # Remove SIZE clause path — let OMF handle file placement
            # The ADD DATAFILE without a path uses db_create_file_dest
            import re

            forward_sql = re.sub(
                r"ADD\s+DATAFILE\s+'[^']*'\s+SIZE",
                "ADD DATAFILE SIZE",
                forward_sql,
                flags=re.IGNORECASE,
            )
            # Rollback: keep the DROP DATAFILE as-is (path captured at runtime)
            logger.info(
                "OMF enabled for %s — using auto-managed file placement",
                database_id,
            )

        return forward_sql, rollback_sql
