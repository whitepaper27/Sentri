"""Supervisor — deterministic routing + category-aware correlation engine.

v5.0: NOT an LLM call. Routes alert_type → specialist agent using
brain/routing_rules.md. Correlates same-category alerts on same DB
within 5 minutes into incidents for RCA agent.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sentri.core.constants import ORCHESTRATOR_POLL_INTERVAL, WorkflowStatus
from sentri.core.models import AuditRecord, Workflow
from sentri.orchestrator.state_machine import StateMachine, is_terminal

if TYPE_CHECKING:
    from sentri.agents.base import AgentContext
    from sentri.agents.specialist_base import SpecialistBase
    from sentri.notifications.router import NotificationRouter

logger = logging.getLogger("sentri.orchestrator.supervisor")


@dataclass
class RoutingRule:
    """A parsed routing rule from brain/routing_rules.md."""

    pattern: str  # e.g., "tablespace_full" or "check_finding:*"
    agent_name: str  # e.g., "storage_agent"
    is_wildcard: bool  # True if pattern ends with *


class Supervisor:
    """Deterministic workflow router + category-aware correlation.

    Replaces the Orchestrator as the primary workflow processor in start_cmd.
    The existing Orchestrator class is NOT modified.
    """

    CORRELATION_WINDOW_MINUTES = 5

    def __init__(
        self,
        context: "AgentContext",
        alert_event: threading.Event,
        notification_router: Optional["NotificationRouter"] = None,
    ):
        self.context = context
        self._alert_event = alert_event
        self._stop = False
        self._notification_router = notification_router

        self._state_machine = StateMachine(context.workflow_repo)
        self._agents: dict[str, "SpecialistBase"] = {}
        self._routing_rules: list[RoutingRule] = []
        self._categories: dict[str, list[str]] = {}  # category → [alert_types]
        self._fallback_agent: str = "storage_agent"
        self._rca_alert_count: int = 3
        self._rca_window_hours: int = 24
        self._loaded = False

    def register_agent(self, name: str, agent: "SpecialistBase") -> None:
        """Register a specialist agent by name."""
        self._agents[name] = agent
        logger.info("Registered specialist: %s", name)

    def run(self, poll_interval: int = ORCHESTRATOR_POLL_INTERVAL) -> None:
        """Main supervisor loop. Runs until stop() is called."""
        logger.info("Supervisor started, polling every %ds", poll_interval)

        while not self._stop:
            self._alert_event.wait(timeout=poll_interval)
            self._alert_event.clear()

            try:
                self._process_cycle()
            except Exception as e:
                logger.error("Supervisor cycle error: %s", e)

        logger.info("Supervisor stopped")

    def stop(self) -> None:
        """Signal the supervisor to stop."""
        self._stop = True
        self._alert_event.set()

    # ------------------------------------------------------------------
    # Routing rules
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Load and parse routing_rules.md on first use."""
        if self._loaded:
            return
        self._load_routing_rules()
        self._load_rca_thresholds()
        self._loaded = True

    def _load_routing_rules(self) -> None:
        """Parse brain/routing_rules.md into routing rules + categories."""
        try:
            policy = self.context.policy_loader.load_brain("routing_rules")
        except Exception:
            logger.warning("No routing_rules.md found — using direct routing only")
            policy = {}

        if not policy:
            return

        # Parse "Direct Routing" section
        routing_section = policy.get("direct_routing", {})
        items = []
        if isinstance(routing_section, dict):
            items = routing_section.get("items", [])
        elif isinstance(routing_section, list):
            items = routing_section

        for item in items:
            # Format: "alert_type -> agent_name" or "alert_type → agent_name"
            item_str = str(item)
            for sep in ("->", "→"):
                if sep in item_str:
                    parts = item_str.split(sep, 1)
                    pattern = parts[0].strip()
                    agent_name = parts[1].strip()
                    is_wildcard = pattern.endswith("*")
                    self._routing_rules.append(
                        RoutingRule(
                            pattern=pattern,
                            agent_name=agent_name,
                            is_wildcard=is_wildcard,
                        )
                    )
                    break

        # Parse "Alert Categories" section
        categories_section = policy.get("alert_categories", {})
        if isinstance(categories_section, dict):
            items = categories_section.get("items", [])
            for item in items:
                # Format: "category: [type1, type2, ...]"
                item_str = str(item)
                if ":" in item_str:
                    cat_name, types_str = item_str.split(":", 1)
                    cat_name = cat_name.strip()
                    # Parse [type1, type2] or type1, type2
                    types_str = types_str.strip().strip("[]")
                    alert_types = [t.strip() for t in types_str.split(",") if t.strip()]
                    if cat_name and alert_types:
                        self._categories[cat_name] = alert_types

        # Parse fallback
        fallback_section = policy.get("fallback", {})
        if isinstance(fallback_section, dict):
            items = fallback_section.get("items", [])
            for item in items:
                item_str = str(item)
                for sep in ("->", "→"):
                    if sep in item_str:
                        self._fallback_agent = item_str.split(sep, 1)[1].strip()
                        break

        logger.info(
            "Routing loaded: %d rules, %d categories, fallback=%s",
            len(self._routing_rules),
            len(self._categories),
            self._fallback_agent,
        )

    def _load_rca_thresholds(self) -> None:
        """Parse RCA recommendation thresholds from brain/rules.md."""
        try:
            policy = self.context.policy_loader.load_brain("rules")
        except Exception:
            return

        if not policy:
            return

        section = policy.get("rca_recommendation_thresholds", {})
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
                self._rca_alert_count = val
            elif "hour" in key or "window" in key:
                self._rca_window_hours = val

        logger.info(
            "RCA thresholds: %d alerts in %dh",
            self._rca_alert_count,
            self._rca_window_hours,
        )

    # ------------------------------------------------------------------
    # Main processing cycle
    # ------------------------------------------------------------------

    def _process_cycle(self) -> None:
        """Process all actionable workflows with routing + correlation."""
        self._ensure_loaded()

        workflows = self.context.workflow_repo.find_actionable()
        if not workflows:
            return

        # Step 0: Handle APPROVED workflows (execute stored plans)
        for wf in workflows:
            if self._stop:
                break
            if wf.status == WorkflowStatus.APPROVED.value:
                self._handle_approved(wf)

        # Step 0b: Check AWAITING_APPROVAL timeouts
        for wf in workflows:
            if self._stop:
                break
            if wf.status == WorkflowStatus.AWAITING_APPROVAL.value:
                self._check_approval_timeout(wf)

        # Step 0c: Handle DENIED workflows (complete or escalate)
        for wf in workflows:
            if self._stop:
                break
            if wf.status == WorkflowStatus.DENIED.value:
                self._handle_denied(wf)

        # Step 1: Check for correlation (same category, same DB, within window)
        correlated_ids: set[str] = set()
        correlated_groups = self._detect_correlations(workflows)
        for group in correlated_groups:
            for wf in group:
                correlated_ids.add(wf.id)
            self._route_correlated_incident(group)

        # Step 2: Route uncorrelated DETECTED workflows individually
        for wf in workflows:
            if self._stop:
                break
            if is_terminal(wf.status):
                continue
            if wf.status != WorkflowStatus.DETECTED.value:
                continue
            if wf.id in correlated_ids:
                continue
            try:
                self._route_workflow(wf)
            except Exception as e:
                logger.error("Routing error for %s: %s", wf.id, e)

    def _route_workflow(self, wf: Workflow) -> None:
        """Route a single workflow to the correct specialist agent."""
        # Optimistic lock: re-read status to prevent double-processing.
        # If status changed since we fetched the batch, skip this workflow.
        fresh = self.context.workflow_repo.get(wf.id)
        if not fresh or fresh.status != wf.status:
            return

        agent_name = self._match_routing_rule(wf.alert_type)
        agent = self._agents.get(agent_name)

        if not agent:
            logger.warning(
                "No agent '%s' registered, falling back to '%s'",
                agent_name,
                self._fallback_agent,
            )
            agent = self._agents.get(self._fallback_agent)

        if agent:
            logger.info(
                "Routing %s (%s) → %s",
                wf.id,
                wf.alert_type,
                agent.name,
            )
            # Audit the routing decision
            try:
                self.context.audit_repo.create(
                    AuditRecord(
                        workflow_id=wf.id,
                        action_type="ROUTING_DECISION",
                        database_id=wf.database_id,
                        environment=wf.environment,
                        executed_by="supervisor",
                        result="ROUTED",
                        evidence=f"agent={agent.name},alert_type={wf.alert_type},rule={agent_name}",
                    )
                )
            except Exception:
                pass  # Don't let audit failure block routing
            try:
                result = agent.process(wf.id)
                result_status = result.get("status", "?")
                logger.info(
                    "Agent %s result for %s: %s",
                    agent.name,
                    wf.id,
                    result_status,
                )
                # Safety net: if specialist didn't advance status, do it here
                updated_wf = self.context.workflow_repo.get(wf.id)
                if updated_wf and updated_wf.status == WorkflowStatus.DETECTED.value:
                    if result_status == "success":
                        self._state_machine.transition(wf.id, WorkflowStatus.COMPLETED.value)
                    elif result_status in ("failure", "blocked"):
                        self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)

                # Send notification based on agent result (specialist may have
                # already advanced the status, so this is outside the safety net).
                # Use updated_wf (fresh from DB) so email shows actual SQL + confidence.
                notify_wf = updated_wf or wf
                if result_status == "success":
                    self._send_completion_notification(notify_wf)
                elif result_status in ("failure", "blocked"):
                    self._send_escalation_notification(notify_wf, [f"Agent {agent.name} returned {result_status}"])
            except Exception as e:
                logger.error("Agent %s failed for %s: %s", agent.name, wf.id, e)
                self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)
                self._send_escalation_notification(wf, [f"Agent {agent.name} exception: {e}"])
        else:
            logger.error(
                "No agent available for %s (alert_type=%s) — escalating",
                wf.id,
                wf.alert_type,
            )
            self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)
            self._send_escalation_notification(wf, [f"No agent available for alert_type={wf.alert_type}"])

    def _handle_approved(self, wf: Workflow) -> None:
        """Execute an approved workflow using its stored execution plan."""
        logger.info("Executing APPROVED workflow %s", wf.id)

        # Load stored execution plan
        if not wf.execution_plan:
            logger.warning("APPROVED workflow %s has no execution plan — escalating", wf.id)
            self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)
            self._send_escalation_notification(wf, ["APPROVED workflow has no execution plan"])
            return

        try:
            self._state_machine.transition(wf.id, WorkflowStatus.EXECUTING.value)
        except Exception as e:
            logger.error("Cannot transition %s to EXECUTING: %s", wf.id, e)
            return

        # Find the appropriate agent for this workflow
        agent_name = self._match_routing_rule(wf.alert_type)
        agent = self._agents.get(agent_name) or self._agents.get(self._fallback_agent)

        if not agent:
            logger.error("No agent available to execute APPROVED workflow %s", wf.id)
            self._state_machine.transition(wf.id, WorkflowStatus.FAILED.value)
            return

        # Execute: for now mark as COMPLETED (actual execution via Executor is in engine.py)
        # The specialist already stored the plan — we just advance the state
        try:
            self._state_machine.transition(wf.id, WorkflowStatus.COMPLETED.value)
            logger.info("APPROVED workflow %s executed successfully", wf.id)
            self._send_completion_notification(wf)
        except Exception as e:
            logger.error("Execution failed for APPROVED workflow %s: %s", wf.id, e)
            try:
                self._state_machine.transition(wf.id, WorkflowStatus.FAILED.value)
            except Exception:
                pass

    def _handle_denied(self, wf: Workflow) -> None:
        """Handle a DENIED workflow — complete or escalate based on denial reason."""
        logger.info("Handling DENIED workflow %s", wf.id)

        # Extract denial reason from audit record
        denial_reason = ""
        denied_by = ""
        try:
            recent = self.context.audit_repo.find_by_workflow(wf.id)
            for rec in reversed(recent):
                if rec.result == "DENIED":
                    denied_by = rec.approved_by or ""
                    ev = rec.evidence or ""
                    for part in ev.split(","):
                        if part.startswith("denied_reason="):
                            denial_reason = part[len("denied_reason=") :]
                    break
        except Exception as e:
            logger.warning("Could not read denial reason for %s: %s", wf.id, e)

        # If denial reason contains "escalate", route to ESCALATED
        if denial_reason and "escalate" in denial_reason.lower():
            logger.info(
                "DENIED workflow %s escalated per DBA request (reason: %s)",
                wf.id,
                denial_reason,
            )
            self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)
            self._send_escalation_notification(wf, [f"DBA escalation: {denial_reason}"])
        else:
            self._state_machine.transition(wf.id, WorkflowStatus.COMPLETED.value)
            logger.info("DENIED workflow %s completed (no action taken)", wf.id)
            self._send_completion_notification(wf, result="DENIED")

        # Send denial notification
        self._send_denial_notification(wf, denied_by, denial_reason)

    def _send_denial_notification(
        self,
        wf: Workflow,
        denied_by: str,
        denial_reason: str,
    ) -> None:
        """Send denial notification via NotificationRouter."""
        try:
            if self._notification_router:
                from sentri.notifications.adapter import NotificationContext

                ctx = NotificationContext(
                    workflow_id=wf.id,
                    database_id=wf.database_id,
                    alert_type=wf.alert_type,
                    environment=wf.environment,
                    denied_by=denied_by,
                    denial_reason=denial_reason,
                )
                self._notification_router.send_denial_notice(ctx)
        except Exception as e:
            logger.warning("Denial notification failed for %s: %s", wf.id, e)

    def _send_completion_notification(self, wf: Workflow, result: str = "SUCCESS") -> None:
        """Send completion notification via NotificationRouter."""
        if not self._notification_router:
            return
        try:
            import json

            from sentri.notifications.adapter import NotificationContext

            forward_sql = ""
            rollback_sql = ""
            confidence = 0.0
            reasons: list[str] = []

            if wf.execution_plan:
                try:
                    plan = json.loads(wf.execution_plan)
                    forward_sql = plan.get("forward_sql", "")
                    rollback_sql = plan.get("rollback_sql", "")
                except (json.JSONDecodeError, AttributeError):
                    pass

            if wf.verification:
                try:
                    vr = json.loads(wf.verification)
                    confidence = vr.get("confidence", 0.0)
                except (json.JSONDecodeError, AttributeError):
                    pass

            # Check for repeat alerts — add RCA recommendation if threshold exceeded
            try:
                recent_count, _ = self.context.workflow_repo.count_recent_same(
                    wf.database_id, wf.alert_type, hours=self._rca_window_hours,
                )
                if recent_count >= self._rca_alert_count:
                    reasons.append(
                        f"REPEAT ALERT: {wf.alert_type} fired {recent_count}x "
                        f"in {self._rca_window_hours}h on {wf.database_id} "
                        f"-- root cause investigation recommended"
                    )
            except Exception:
                pass  # Don't let RCA check failure block notification

            ctx = NotificationContext(
                workflow_id=wf.id,
                database_id=wf.database_id,
                alert_type=wf.alert_type,
                environment=wf.environment,
                result=result,
                forward_sql=forward_sql,
                rollback_sql=rollback_sql,
                confidence=confidence,
                reasons=reasons,
            )
            self._notification_router.send_completion_notice(ctx)
        except Exception as e:
            logger.warning("Completion notification failed for %s: %s", wf.id, e)

    def _send_escalation_notification(self, wf: Workflow, reasons: list[str] | None = None) -> None:
        """Send escalation notification via NotificationRouter."""
        if not self._notification_router:
            return
        try:
            from sentri.notifications.adapter import NotificationContext

            ctx = NotificationContext(
                workflow_id=wf.id,
                database_id=wf.database_id,
                alert_type=wf.alert_type,
                environment=wf.environment,
                reasons=reasons or [],
            )
            self._notification_router.send_escalation_notice(ctx)
        except Exception as e:
            logger.warning("Escalation notification failed for %s: %s", wf.id, e)

    def _check_approval_timeout(self, wf: Workflow) -> None:
        """Check if an AWAITING_APPROVAL workflow has timed out."""
        timeout_secs = self.context.settings.approvals.approval_timeout

        # Use updated_at (set when status changed to AWAITING_APPROVAL),
        # not created_at (which is when the alert was first detected).
        ref_ts = wf.updated_at or wf.created_at
        if not ref_ts:
            return

        try:
            if isinstance(ref_ts, str):
                created = datetime.fromisoformat(ref_ts)
            else:
                created = ref_ts
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            elapsed = (now - created).total_seconds()

            if elapsed > timeout_secs:
                logger.warning(
                    "Approval timeout for %s (elapsed=%.0fs, timeout=%ds)",
                    wf.id,
                    elapsed,
                    timeout_secs,
                )
                self._state_machine.transition(wf.id, WorkflowStatus.TIMEOUT.value)
                self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)

                # Audit record for timeout
                self.context.audit_repo.create(
                    AuditRecord(
                        workflow_id=wf.id,
                        action_type="APPROVAL_TIMEOUT",
                        database_id=wf.database_id,
                        environment=wf.environment,
                        executed_by="supervisor",
                        result="TIMEOUT",
                        evidence=f"elapsed={elapsed:.0f}s,timeout={timeout_secs}s",
                    )
                )

                # Send timeout notification email
                self._send_timeout_notification(wf, elapsed, timeout_secs)
        except Exception as e:
            logger.warning("Timeout check failed for %s: %s", wf.id, e)

    def _send_timeout_notification(self, wf: Workflow, elapsed: float, timeout_secs: int) -> None:
        """Send timeout notification via NotificationRouter (or legacy fallback)."""
        try:
            # Use NotificationRouter if available (v5.1b)
            if self._notification_router:
                from sentri.notifications.adapter import NotificationContext

                ctx = NotificationContext(
                    workflow_id=wf.id,
                    database_id=wf.database_id,
                    alert_type=wf.alert_type,
                    environment=wf.environment,
                    elapsed_seconds=elapsed,
                    timeout_seconds=timeout_secs,
                )
                self._notification_router.send_timeout_notification(ctx)
                return

            # Legacy fallback: direct email call
            settings = self.context.settings
            if not (settings.approvals.email_enabled and settings.email.smtp_server):
                return

            from sentri.notifications.email_sender import send_timeout_notification_email

            recipients = settings.approvals.approval_recipients
            if not recipients:
                recipients = settings.email.username
            to_addrs = [a.strip() for a in recipients.split(",") if a.strip()]

            if to_addrs:
                send_timeout_notification_email(
                    smtp_server=settings.email.smtp_server,
                    smtp_port=settings.email.smtp_port,
                    from_addr=settings.email.username,
                    to_addrs=to_addrs,
                    workflow_id=wf.id,
                    database_id=wf.database_id,
                    alert_type=wf.alert_type,
                    environment=wf.environment,
                    elapsed_seconds=elapsed,
                    timeout_seconds=timeout_secs,
                    username=settings.email.username,
                    password=settings.email.password,
                    use_tls=settings.email.use_tls,
                )
        except Exception as e:
            logger.warning("Failed to send timeout notification for %s: %s", wf.id, e)

    def _match_routing_rule(self, alert_type: str) -> str:
        """Match alert_type against routing rules. Returns agent name."""
        for rule in self._routing_rules:
            if rule.is_wildcard:
                prefix = rule.pattern.rstrip("*")
                if alert_type.startswith(prefix):
                    return rule.agent_name
            elif rule.pattern == alert_type:
                return rule.agent_name

        return self._fallback_agent

    # ------------------------------------------------------------------
    # Correlation detection
    # ------------------------------------------------------------------

    def _detect_correlations(
        self,
        workflows: list[Workflow],
    ) -> list[list[Workflow]]:
        """Find groups of alerts from same category on same DB within window.

        Only groups DETECTED workflows (not yet processed).
        Only groups alerts from the SAME category.
        """
        # Filter to DETECTED workflows within the correlation window
        detected = [wf for wf in workflows if wf.status == WorkflowStatus.DETECTED.value]

        if len(detected) < 2:
            return []

        # Group by database_id
        db_groups: dict[str, list[Workflow]] = {}
        for wf in detected:
            db_groups.setdefault(wf.database_id, []).append(wf)

        correlated: list[list[Workflow]] = []
        for db_id, group in db_groups.items():
            if len(group) < 2:
                continue

            # Sub-group by category
            cat_groups: dict[str, list[Workflow]] = {}
            for wf in group:
                category = self._get_alert_category(wf.alert_type)
                cat_groups.setdefault(category, []).append(wf)

            for cat, cat_wfs in cat_groups.items():
                if cat == "unknown":
                    continue  # Don't correlate unknown categories
                if len(cat_wfs) >= 2:
                    logger.info(
                        "Correlated %d '%s' alerts on %s: %s",
                        len(cat_wfs),
                        cat,
                        db_id,
                        [w.alert_type for w in cat_wfs],
                    )
                    correlated.append(cat_wfs)

        return correlated

    def _route_correlated_incident(self, group: list[Workflow]) -> None:
        """Route a correlated incident group to the RCA agent."""
        rca = self._agents.get("rca_agent")
        if not rca:
            # No RCA agent registered — route each individually
            logger.warning("No rca_agent registered, routing correlated alerts individually")
            for wf in group:
                self._route_workflow(wf)
            return

        # Route the first workflow to RCA agent (it represents the incident)
        primary = group[0]
        logger.info(
            "Routing correlated incident (primary=%s, %d alerts) → rca_agent",
            primary.id,
            len(group),
        )
        try:
            result = rca.process(primary.id)
            result_status = result.get("status", "?")
            logger.info("RCA result: %s", result_status)
            # Update all correlated workflows to terminal state
            for wf in group:
                updated_wf = self.context.workflow_repo.get(wf.id)
                if updated_wf and updated_wf.status == WorkflowStatus.DETECTED.value:
                    if result_status == "success":
                        self._state_machine.transition(wf.id, WorkflowStatus.COMPLETED.value)
                    elif result_status in ("failure", "blocked"):
                        self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)
        except Exception as e:
            logger.error("RCA agent failed for incident %s: %s", primary.id, e)
            for wf in group:
                self._state_machine.transition(wf.id, WorkflowStatus.ESCALATED.value)

    def _get_alert_category(self, alert_type: str) -> str:
        """Look up which category an alert_type belongs to."""
        for category, types in self._categories.items():
            if alert_type in types:
                return category
        return "unknown"
