"""Universal Agent Contract — base class for all specialist agents.

v5.0: Every specialist implements the same 7-step interface:
1. verify(finding)      → Is this problem real right now?
2. investigate(finding)  → What's actually going on? (specialist tools)
3. propose(context)      → Generate N candidate fixes
4. argue(candidates)     → Score each candidate (reward function)
5. select(scored)        → Pick the best (highest score, lowest risk)
6. execute(plan)         → Run through Safety Mesh → execute or seek approval
7. learn(outcome)        → Record what happened for future decisions
"""

from __future__ import annotations

import json
import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from sentri.core.constants import WorkflowStatus
from sentri.core.llm_interface import LLMProvider, NoOpLLMProvider
from sentri.core.models import ExecutionPlan, ResearchOption, Workflow
from sentri.orchestrator.safety_mesh import MeshVerdict, SafetyMesh

from .base import AgentContext, BaseAgent

if TYPE_CHECKING:
    from sentri.llm.cost_tracker import CostTracker
    from sentri.memory.investigation_store import InvestigationStore
    from sentri.notifications.router import NotificationRouter

logger = logging.getLogger("sentri.agents.specialist_base")


@dataclass
class ScoredCandidate:
    """A ResearchOption with argue/select scoring."""

    option: ResearchOption
    scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    judge_reasoning: str = ""


class SpecialistBase(BaseAgent):
    """Base class for all specialist agents implementing the Universal Agent Contract.

    Subclasses MUST implement: verify(), investigate(), propose().
    Subclasses MAY override: argue(), select(), learn().
    """

    def __init__(
        self,
        name: str,
        context: AgentContext,
        safety_mesh: SafetyMesh,
        llm_provider: Optional[LLMProvider] = None,
        cost_tracker: Optional["CostTracker"] = None,
        investigation_store: Optional["InvestigationStore"] = None,
        notification_router: Optional["NotificationRouter"] = None,
    ):
        super().__init__(name, context)
        self._safety_mesh = safety_mesh
        self._llm = llm_provider or NoOpLLMProvider()
        self._cost_tracker = cost_tracker
        self._investigation_store = investigation_store
        self._notification_router = notification_router

    # ------------------------------------------------------------------
    # Main orchestration (the 7-step contract)
    # ------------------------------------------------------------------

    def process(self, workflow_id: str) -> dict:
        """Orchestrate the 7-step Universal Agent Contract.

        Returns:
            {"status": "success"|"failure"|"needs_approval"|"queued",
             "agent": <name>, ...}
        """
        workflow = self.context.workflow_repo.get(workflow_id)
        if not workflow:
            return {
                "status": "failure",
                "agent": self.name,
                "error": f"Workflow {workflow_id} not found",
            }

        # Step 1: Verify
        try:
            verified, confidence = self.verify(workflow)
        except Exception as e:
            self.logger.error("Verify failed for %s: %s", workflow_id, e)
            return {"status": "failure", "agent": self.name, "error": f"Verification error: {e}"}

        if not verified:
            self.logger.info(
                "Verification failed for %s (confidence=%.2f)", workflow_id, confidence
            )
            self.context.workflow_repo.update_status(
                workflow_id,
                WorkflowStatus.VERIFICATION_FAILED.value,
            )
            return {
                "status": "failure",
                "agent": self.name,
                "error": "Verification failed",
                "confidence": confidence,
            }

        # Step 2: Investigate
        try:
            investigation = self.investigate(workflow)
        except Exception as e:
            self.logger.error("Investigate failed for %s: %s", workflow_id, e)
            investigation = {}

        # Step 3: Propose
        try:
            candidates = self.propose(workflow, investigation)
        except Exception as e:
            self.logger.error("Propose failed for %s: %s", workflow_id, e)
            candidates = []

        if not candidates:
            self.context.workflow_repo.update_status(
                workflow_id,
                WorkflowStatus.FAILED.value,
            )
            return {"status": "failure", "agent": self.name, "error": "No candidates generated"}

        # Steps 4 & 5: Argue + Select (cost gate decides depth)
        selected = self._cost_gated_selection(workflow, candidates)

        # Step 6: Execute (through Safety Mesh)
        plan = self._build_plan(workflow, selected)
        mesh_verdict = self._safety_mesh.check(workflow, plan, confidence)
        result = self._handle_mesh_verdict(workflow, plan, mesh_verdict)

        # Step 7: Learn
        try:
            self.learn(workflow, selected, result)
        except Exception as e:
            self.logger.warning("Learn failed for %s: %s", workflow_id, e)

        # Persist investigation analysis as .md file
        self._persist_investigation(
            workflow,
            confidence,
            investigation,
            candidates,
            selected,
            result,
        )

        return result

    # ------------------------------------------------------------------
    # Abstract methods (specialist-specific)
    # ------------------------------------------------------------------

    @abstractmethod
    def verify(self, workflow: Workflow) -> tuple[bool, float]:
        """Step 1: Is this problem real right now?

        Returns (is_verified, confidence_score).
        """
        ...

    @abstractmethod
    def investigate(self, workflow: Workflow) -> dict:
        """Step 2: Gather specialist-specific investigation context.

        Returns a dict of investigation results (tool outputs, etc.).
        """
        ...

    @abstractmethod
    def propose(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Step 3: Generate N candidate fixes.

        Returns a list of ResearchOption with forward_sql, rollback_sql, etc.
        """
        ...

    # ------------------------------------------------------------------
    # Default implementations (override-able)
    # ------------------------------------------------------------------

    def argue(
        self,
        candidates: list[ResearchOption],
        workflow: Workflow,
    ) -> list[ScoredCandidate]:
        """Step 4: Score each candidate using LLM judge + scoring weights.

        Default: use LLM to score candidates against weights from agent .md.
        Override for specialist-specific scoring logic.
        """
        weights = self._get_scoring_weights()
        if not weights or not self._llm.is_available():
            # No weights or no LLM — score by confidence only
            return [
                ScoredCandidate(
                    option=c,
                    scores={"confidence": c.confidence},
                    total_score=c.confidence,
                )
                for c in candidates
            ]

        # Build judge prompt
        from sentri.llm.prompts import ARGUE_JUDGE_SYSTEM_PROMPT

        criteria_desc = "\n".join(f"- {k} (weight {v:.2f})" for k, v in weights.items())
        system_prompt = ARGUE_JUDGE_SYSTEM_PROMPT.format(
            criteria_descriptions=criteria_desc,
        )

        candidates_json = json.dumps(
            [
                {
                    "option_id": c.option_id,
                    "title": c.title,
                    "forward_sql": c.forward_sql,
                    "rollback_sql": c.rollback_sql,
                    "confidence": c.confidence,
                    "risk_level": c.risk_level,
                    "reasoning": c.reasoning,
                }
                for c in candidates
            ],
            default=str,
        )

        user_prompt = (
            f"Alert: {workflow.alert_type} on {workflow.database_id}\n"
            f"Environment: {workflow.environment}\n\n"
            f"Candidates:\n{candidates_json}"
        )

        try:
            raw = self._llm.generate(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.2,
                max_tokens=1024,
            )
            return self._parse_judge_response(raw, candidates, weights)
        except Exception as e:
            self.logger.warning("Judge LLM failed: %s — using confidence only", e)
            return [ScoredCandidate(option=c, total_score=c.confidence) for c in candidates]

    def select(self, scored: list[ScoredCandidate]) -> ResearchOption:
        """Step 5: Pick the best candidate. Default: highest total_score."""
        if not scored:
            raise ValueError("No scored candidates to select from")
        best = max(scored, key=lambda s: s.total_score)
        self.logger.info("Selected: '%s' (score=%.2f)", best.option.title, best.total_score)
        return best.option

    def learn(
        self,
        workflow: Workflow,
        selected: ResearchOption,
        result: dict,
    ) -> None:
        """Step 7: Record outcome for future decisions.

        Default: log the outcome. Override for specialist-specific learning.
        """
        status = result.get("status", "unknown")
        self.logger.info(
            "Outcome for %s on %s: %s (option='%s')",
            workflow.alert_type,
            workflow.database_id,
            status,
            selected.title,
        )

    # ------------------------------------------------------------------
    # Investigation persistence
    # ------------------------------------------------------------------

    def _persist_investigation(
        self,
        workflow: Workflow,
        confidence: float,
        investigation: dict,
        candidates: list[ResearchOption],
        selected: ResearchOption,
        result: dict,
    ) -> None:
        """Persist investigation analysis as a .md file (fire-and-forget)."""
        if not self._investigation_store:
            return
        try:
            self._investigation_store.save(
                workflow_id=workflow.id,
                database_id=workflow.database_id,
                alert_type=workflow.alert_type,
                environment=workflow.environment,
                agent_name=self.name,
                confidence=confidence,
                investigation=investigation,
                candidates=candidates,
                selected=selected,
                result=result,
            )
        except Exception as e:
            self.logger.warning(
                "Failed to persist investigation for %s: %s",
                workflow.id,
                e,
            )

    # ------------------------------------------------------------------
    # Cost gate
    # ------------------------------------------------------------------

    def _cost_gated_selection(
        self,
        workflow: Workflow,
        candidates: list[ResearchOption],
    ) -> ResearchOption:
        """Apply cost gate: template / one-shot / full argue-select based on history.

        | Success Rate | Confidence | Action          | LLM Calls |
        |-------------|------------|-----------------|-----------|
        | ≥95%        | ≥0.95      | Template (skip) | 0         |
        | 80-95%      | any        | One-shot        | 0 (just pick) |
        | <80% or new | any        | Full argue/select | 1+      |
        """
        success_rate, total = self._get_historical_success_rate(
            workflow.alert_type,
            workflow.database_id,
        )
        best_confidence = max(c.confidence for c in candidates) if candidates else 0.0

        # Template path: high success rate + high confidence + enough history
        if success_rate >= 0.95 and best_confidence >= 0.95 and total >= 5:
            self.logger.info(
                "Cost gate: template path (%.0f%% success, %d history)",
                success_rate * 100,
                total,
            )
            return max(candidates, key=lambda c: c.confidence)

        # One-shot path: moderate success rate — just pick best, no LLM judge
        if 0.80 <= success_rate < 0.95 and total >= 3:
            self.logger.info(
                "Cost gate: one-shot path (%.0f%% success, %d history)",
                success_rate * 100,
                total,
            )
            return max(candidates, key=lambda c: c.confidence)

        # Full argue/select path: novel or low success rate
        self.logger.info(
            "Cost gate: full argue/select (%.0f%% success, %d history)",
            success_rate * 100,
            total,
        )
        scored = self.argue(candidates, workflow)
        if scored:
            return self.select(scored)
        # Fallback: highest confidence
        return max(candidates, key=lambda c: c.confidence)

    def _get_historical_success_rate(
        self,
        alert_type: str,
        database_id: str,
    ) -> tuple[float, int]:
        """Query workflows table for 90-day success rate.

        Returns (success_rate, total_count). (0.0, 0) if no history.
        """
        try:
            rows = self.context.db.execute_read(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as success
                   FROM workflows
                   WHERE alert_type = ?
                   AND database_id = ?
                   AND created_at > datetime('now', '-90 days')""",
                (alert_type, database_id),
            )
            if not rows or rows[0]["total"] == 0:
                return 0.0, 0
            total = rows[0]["total"]
            success = rows[0]["success"] or 0
            return success / total, total
        except Exception as e:
            self.logger.warning("Historical success rate query failed: %s", e)
            return 0.0, 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_plan(
        self,
        workflow: Workflow,
        option: ResearchOption,
    ) -> ExecutionPlan:
        """Build an ExecutionPlan from the selected ResearchOption."""
        # Extract params from workflow suggestion
        params = {}
        if workflow.suggestion:
            try:
                suggestion_data = json.loads(workflow.suggestion)
                params = {
                    k: v
                    for k, v in suggestion_data.get("extracted_data", {}).items()
                    if v is not None
                }
            except (json.JSONDecodeError, AttributeError):
                pass

        return ExecutionPlan(
            action_type=workflow.alert_type.upper(),
            forward_sql=option.forward_sql,
            rollback_sql=option.rollback_sql,
            validation_sql="",  # Specialist agents may not have standard validation
            expected_outcome={"status": "resolved"},
            risk_level=option.risk_level,
            estimated_duration_seconds=30,
            params=params,
        )

    def _handle_mesh_verdict(
        self,
        workflow: Workflow,
        plan: ExecutionPlan,
        verdict: MeshVerdict,
    ) -> dict:
        """Route based on Safety Mesh verdict: allow/approval/block/queue."""
        if verdict.blocked:
            self.logger.warning(
                "Safety Mesh BLOCKED %s: %s",
                workflow.id,
                "; ".join(verdict.reasons),
            )
            self.context.workflow_repo.update_status(
                workflow.id,
                WorkflowStatus.ESCALATED.value,
            )
            return {
                "status": "blocked",
                "agent": self.name,
                "reasons": verdict.reasons,
                "blocked_by": verdict.blocked_by,
            }

        if verdict.queued:
            self.logger.info(
                "Safety Mesh QUEUED %s: %s",
                workflow.id,
                "; ".join(verdict.reasons),
            )
            return {
                "status": "queued",
                "agent": self.name,
                "reasons": verdict.reasons,
            }

        if verdict.needs_approval:
            # Store plan and request approval
            self.context.workflow_repo.update_status(
                workflow.id,
                WorkflowStatus.AWAITING_APPROVAL.value,
                execution_plan=plan.to_json(),
            )
            # Send approval notifications (email + Slack)
            self._send_approval_notifications(workflow, plan, verdict)
            return {
                "status": "needs_approval",
                "agent": self.name,
                "reasons": verdict.reasons,
                "plan": plan,
            }

        # ALLOW — store plan and mark as completed (DEV auto-execute)
        self.context.workflow_repo.update_status(
            workflow.id,
            WorkflowStatus.COMPLETED.value,
            execution_plan=plan.to_json(),
        )
        return {
            "status": "success",
            "agent": self.name,
            "plan": plan,
        }

    def _send_approval_notifications(
        self,
        workflow: Workflow,
        plan: ExecutionPlan,
        verdict: MeshVerdict,
    ) -> None:
        """Send approval notifications via NotificationRouter (or legacy fallback)."""
        confidence = getattr(plan, "confidence", 0.0)

        # Use NotificationRouter if available (v5.1b)
        if self._notification_router:
            from sentri.notifications.adapter import NotificationContext

            ctx = NotificationContext(
                workflow_id=workflow.id,
                database_id=workflow.database_id,
                alert_type=workflow.alert_type,
                environment=workflow.environment,
                risk_level=plan.risk_level or "MEDIUM",
                confidence=confidence,
                forward_sql=plan.forward_sql,
                rollback_sql=plan.rollback_sql or "N/A",
                reasons=verdict.reasons,
            )
            sent = self._notification_router.send_approval_request(ctx)
            self.logger.info(
                "Approval notifications sent via router: %d adapter(s)",
                sent,
            )
            return

        # Legacy fallback: direct email + Slack calls
        settings = self.context.settings

        if settings.approvals.email_enabled and settings.email.smtp_server:
            try:
                from sentri.notifications.email_sender import (
                    send_approval_request_email,
                )

                recipients_str = settings.approvals.approval_recipients or settings.email.username
                recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]
                if recipients:
                    send_approval_request_email(
                        smtp_server=settings.email.smtp_server,
                        smtp_port=settings.email.smtp_port,
                        from_addr=settings.email.username,
                        to_addrs=recipients,
                        workflow_id=workflow.id,
                        database_id=workflow.database_id,
                        alert_type=workflow.alert_type,
                        environment=workflow.environment,
                        forward_sql=plan.forward_sql,
                        rollback_sql=plan.rollback_sql or "N/A",
                        risk_level=plan.risk_level or "MEDIUM",
                        confidence=confidence,
                        reasons=verdict.reasons,
                        username=settings.email.username,
                        password=settings.email.password,
                        use_tls=settings.email.use_tls,
                    )
            except Exception as e:
                self.logger.warning("Failed to send approval email: %s", e)

        if settings.approvals.slack_webhook_url:
            try:
                from sentri.notifications.slack import send_slack_message

                msg = (
                    f":warning: *Approval needed* for `{workflow.alert_type}` "
                    f"on `{workflow.database_id}` ({workflow.environment})\n"
                    f"Workflow: `{workflow.id}`\n"
                    f"Reasons: {'; '.join(verdict.reasons)}\n"
                    f"Use `sentri approve {workflow.id[:8]}` to approve"
                )
                send_slack_message(settings.approvals.slack_webhook_url, msg)
            except Exception as e:
                self.logger.warning("Failed to send Slack notification: %s", e)

    def _get_scoring_weights(self) -> dict[str, float]:
        """Load scoring weights from the agent's .md policy file.

        Expected format in agents/{name}.md:
        ## Scoring Weights
        - fixes_root_cause: 0.30
        - reversibility: 0.25
        """
        try:
            policy = self.context.policy_loader.load_agent(self.name)
            section = policy.get("scoring_weights", {})
            items = []
            if isinstance(section, dict):
                items = section.get("items", [])
            elif isinstance(section, list):
                items = section

            weights = {}
            for item in items:
                if ":" in str(item):
                    key, val = str(item).split(":", 1)
                    key = key.strip().strip("-").strip()
                    try:
                        weights[key] = float(val.strip())
                    except ValueError:
                        pass
            return weights
        except Exception:
            return {}

    def _parse_judge_response(
        self,
        raw: str,
        candidates: list[ResearchOption],
        weights: dict[str, float],
    ) -> list[ScoredCandidate]:
        """Parse the LLM judge response into ScoredCandidate objects."""
        if not raw or not raw.strip():
            return []

        text = raw.strip()
        # Strip markdown fences
        if "```" in text:
            import re as _re

            fence_match = _re.search(r"```(?:json)?\s*\n(.*?)```", text, _re.DOTALL)
            if fence_match:
                text = fence_match.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.logger.warning("Failed to parse judge response as JSON")
            return []

        if not isinstance(data, list):
            data = [data]

        # Build lookup by option_id
        option_map = {c.option_id: c for c in candidates}

        scored = []
        for item in data:
            if not isinstance(item, dict):
                continue
            option_id = item.get("option_id", "")
            option = option_map.get(option_id)
            if not option:
                continue

            scores = item.get("scores", {})
            # Compute weighted total
            total = sum(scores.get(k, 0.0) * w for k, w in weights.items())
            scored.append(
                ScoredCandidate(
                    option=option,
                    scores=scores,
                    total_score=total,
                    judge_reasoning=item.get("reasoning", ""),
                )
            )

        return scored

    def _should_use_llm(self) -> bool:
        """Check if LLM is available and within budget."""
        if not self._llm.is_available():
            return False
        if self._cost_tracker and not self._cost_tracker.is_within_budget():
            self.logger.warning("Daily LLM budget exhausted")
            return False
        return True

    def _get_extracted_data(self, workflow: Workflow) -> dict:
        """Extract data from the workflow suggestion."""
        if not workflow.suggestion:
            return {}
        try:
            suggestion = json.loads(workflow.suggestion)
            return suggestion.get("extracted_data", {})
        except (json.JSONDecodeError, AttributeError):
            return {}
