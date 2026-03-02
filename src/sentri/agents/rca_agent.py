"""RCA Agent — root cause analysis specialist.

Handles: session_blocker, correlated incidents routed by Supervisor.
Uses tiered investigation (T1 quick → T2 focused → T3 full) and
theory-ranked execution to find and fix root causes efficiently.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

from sentri.core.llm_interface import LLMProvider
from sentri.core.models import ResearchOption, Workflow

from .base import AgentContext
from .specialist_base import SpecialistBase

if TYPE_CHECKING:
    from sentri.llm.cost_tracker import CostTracker
    from sentri.orchestrator.safety_mesh import SafetyMesh

logger = logging.getLogger("sentri.agents.rca_agent")


class InvestigationTier(IntEnum):
    """Investigation depth levels."""

    T1_QUICK = 1  # 3 queries, ~5s — always runs
    T2_FOCUSED = 2  # 5 queries, ~15s — runs if T1 inconclusive
    T3_FULL = 3  # 10+ queries, ~30s — rare, PROD requires approval


@dataclass
class Theory:
    """A root cause theory with supporting evidence."""

    description: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    focus_area: str = ""  # sql_perf, blocking, storage, memory
    fix: Optional[ResearchOption] = None


class RCAAgent(SpecialistBase):
    """Root Cause Analysis specialist.

    Uses tiered investigation to efficiently diagnose issues:
    - T1: Quick triage (wait class + top events + top SQL)
    - T2: Focused on area T1 flagged (sql_perf/blocking/storage/memory)
    - T3: Full snapshot (rare, PROD requires approval)
    """

    HANDLED_ALERTS = frozenset(
        {
            "session_blocker",
        }
    )

    # Confidence threshold to stop investigation
    CONCLUSIVE_THRESHOLD = 0.85

    def __init__(
        self,
        context: AgentContext,
        safety_mesh: "SafetyMesh",
        llm_provider: Optional[LLMProvider] = None,
        cost_tracker: Optional["CostTracker"] = None,
        investigation_store=None,
        notification_router=None,
    ):
        super().__init__(
            "rca_agent",
            context,
            safety_mesh,
            llm_provider,
            cost_tracker,
            investigation_store=investigation_store,
            notification_router=notification_router,
        )

    def verify(self, workflow: Workflow) -> tuple[bool, float]:
        """Verify the issue is still active.

        - session_blocker: Check if blocking chain still exists.
        - correlated: Always true (multiple alerts confirm the issue).
        """
        if workflow.alert_type == "session_blocker":
            return self._verify_session_blocker(workflow)

        # Correlated incidents — multiple alerts confirm existence
        return True, 0.90

    def investigate(self, workflow: Workflow) -> dict:
        """Tiered investigation: T1 always → T2 if needed → T3 rarely.

        Returns investigation context with tier results and identified focus area.
        """
        investigation = {
            "alert_type": workflow.alert_type,
            "database_id": workflow.database_id,
            "tier": InvestigationTier.T1_QUICK,
            "focus_area": "unknown",
        }

        # Tier 1: Quick triage (always runs)
        t1_results = self._investigate_tier1(workflow)
        investigation["t1"] = t1_results

        focus_area = self._identify_focus_area(t1_results)
        investigation["focus_area"] = focus_area

        # Check if T1 is conclusive
        if self._is_conclusive(t1_results):
            return investigation

        # Tier 2: Focused investigation
        investigation["tier"] = InvestigationTier.T2_FOCUSED
        t2_results = self._investigate_tier2(workflow, focus_area)
        investigation["t2"] = t2_results

        if self._is_conclusive(t2_results):
            return investigation

        # Tier 3: Full investigation (only for non-PROD or with approval)
        if workflow.environment != "PROD":
            investigation["tier"] = InvestigationTier.T3_FULL
            t3_results = self._investigate_tier3(workflow, focus_area)
            investigation["t3"] = t3_results

        return investigation

    def propose(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Generate theories and convert to ResearchOptions."""
        theories = self._generate_theories(workflow, investigation)

        if not theories:
            return self._template_fallback(workflow, investigation)

        # Convert theories to ResearchOptions, sorted by confidence
        theories.sort(key=lambda t: t.confidence, reverse=True)
        options = []
        for theory in theories:
            if theory.fix:
                options.append(theory.fix)
            else:
                options.append(
                    ResearchOption(
                        title=f"RCA: {theory.description[:60]}",
                        description=theory.description,
                        forward_sql=f"-- Investigate: {theory.focus_area}",
                        rollback_sql="N/A: investigation only",
                        confidence=theory.confidence,
                        risk_level="LOW",
                        reasoning="; ".join(theory.evidence),
                        source="rca_theory",
                    )
                )

        return options

    # ------------------------------------------------------------------
    # Verify helpers
    # ------------------------------------------------------------------

    def _verify_session_blocker(self, workflow: Workflow) -> tuple[bool, float]:
        """Check if blocking chain still exists."""
        if not self.context.oracle_pool:
            return True, 0.70

        try:
            rows = self.context.oracle_pool.execute_query(
                workflow.database_id,
                """SELECT blocking_session, sid
                   FROM v$session
                   WHERE blocking_session IS NOT NULL
                   FETCH FIRST 1 ROWS ONLY""",
                timeout=10,
            )
            if rows:
                return True, 0.95
            return False, 0.20  # Blocking resolved
        except Exception:
            return True, 0.70

    # ------------------------------------------------------------------
    # Tier 1: Quick triage
    # ------------------------------------------------------------------

    def _investigate_tier1(self, workflow: Workflow) -> dict:
        """T1: 3 quick queries — wait class, top events, top SQL."""
        results: dict = {"conclusive": False}

        if not self.context.oracle_pool:
            return results

        db_id = workflow.database_id

        # Q1: Wait class summary
        try:
            wait_classes = self.context.oracle_pool.execute_query(
                db_id,
                """SELECT wait_class, SUM(time_waited) as total_wait
                   FROM v$system_event
                   WHERE wait_class != 'Idle'
                   GROUP BY wait_class
                   ORDER BY total_wait DESC
                   FETCH FIRST 5 ROWS ONLY""",
                timeout=10,
            )
            results["wait_classes"] = wait_classes
        except Exception as e:
            self.logger.warning("T1 wait class query failed: %s", e)

        # Q2: Top wait events
        try:
            top_events = self.context.oracle_pool.execute_query(
                db_id,
                """SELECT event, total_waits, time_waited, wait_class
                   FROM v$system_event
                   WHERE wait_class != 'Idle'
                   ORDER BY time_waited DESC
                   FETCH FIRST 10 ROWS ONLY""",
                timeout=10,
            )
            results["top_events"] = top_events
        except Exception as e:
            self.logger.warning("T1 top events query failed: %s", e)

        # Q3: Top 3 SQL by elapsed time
        try:
            top_sql = self.context.oracle_pool.execute_query(
                db_id,
                """SELECT sql_id, cpu_time, elapsed_time, executions,
                          buffer_gets, SUBSTR(sql_text, 1, 200) as sql_text
                   FROM v$sql
                   ORDER BY elapsed_time DESC
                   FETCH FIRST 3 ROWS ONLY""",
                timeout=10,
            )
            results["top_sql"] = top_sql
        except Exception as e:
            self.logger.warning("T1 top SQL query failed: %s", e)

        return results

    # ------------------------------------------------------------------
    # Tier 2: Focused investigation
    # ------------------------------------------------------------------

    def _investigate_tier2(
        self,
        workflow: Workflow,
        focus_area: str,
    ) -> dict:
        """T2: Focused on the area T1 flagged."""
        results: dict = {"focus_area": focus_area, "conclusive": False}

        if not self.context.oracle_pool:
            return results

        db_id = workflow.database_id

        if focus_area == "blocking":
            results.update(self._t2_blocking(db_id))
        elif focus_area == "sql_perf":
            results.update(self._t2_sql_perf(db_id))
        elif focus_area == "storage":
            results.update(self._t2_storage(db_id))
        elif focus_area == "memory":
            results.update(self._t2_memory(db_id))

        return results

    def _t2_blocking(self, db_id: str) -> dict:
        """T2 blocking: blocking chain + lock details."""
        results = {}
        try:
            blocking = self.context.oracle_pool.execute_query(
                db_id,
                """SELECT s1.sid as blocker_sid, s1.username as blocker_user,
                          s1.program as blocker_program,
                          s2.sid as blocked_sid, s2.username as blocked_user,
                          s2.event as blocked_event
                   FROM v$session s1
                   JOIN v$session s2 ON s1.sid = s2.blocking_session
                   FETCH FIRST 10 ROWS ONLY""",
                timeout=10,
            )
            results["blocking_chain"] = blocking
        except Exception as e:
            logger.warning("T2 blocking query failed: %s", e)
        return results

    def _t2_sql_perf(self, db_id: str) -> dict:
        """T2 sql_perf: detailed SQL stats + plan info."""
        results = {}
        try:
            sql_stats = self.context.oracle_pool.execute_query(
                db_id,
                """SELECT sql_id, plan_hash_value,
                          cpu_time, elapsed_time, executions,
                          buffer_gets, disk_reads, rows_processed,
                          SUBSTR(sql_text, 1, 500) as sql_text
                   FROM v$sql
                   ORDER BY cpu_time DESC
                   FETCH FIRST 5 ROWS ONLY""",
                timeout=10,
            )
            results["detailed_sql"] = sql_stats
        except Exception as e:
            logger.warning("T2 sql perf query failed: %s", e)
        return results

    def _t2_storage(self, db_id: str) -> dict:
        """T2 storage: tablespace usage detail."""
        results = {}
        try:
            ts_usage = self.context.oracle_pool.execute_query(
                db_id,
                """SELECT tablespace_name,
                          ROUND(used_percent, 1) as pct_used,
                          ROUND((tablespace_size * block_size)/(1024*1024)) as total_mb
                   FROM dba_tablespace_usage_metrics
                   ORDER BY used_percent DESC
                   FETCH FIRST 10 ROWS ONLY""",
                timeout=10,
            )
            results["tablespace_usage"] = ts_usage
        except Exception as e:
            logger.warning("T2 storage query failed: %s", e)
        return results

    def _t2_memory(self, db_id: str) -> dict:
        """T2 memory: SGA/PGA usage."""
        results = {}
        try:
            memory = self.context.oracle_pool.execute_query(
                db_id,
                """SELECT pool, name,
                          ROUND(bytes/(1024*1024)) as size_mb
                   FROM v$sgastat
                   WHERE pool IS NOT NULL
                   ORDER BY bytes DESC
                   FETCH FIRST 10 ROWS ONLY""",
                timeout=10,
            )
            results["sga_detail"] = memory
        except Exception as e:
            logger.warning("T2 memory query failed: %s", e)
        return results

    # ------------------------------------------------------------------
    # Tier 3: Full investigation
    # ------------------------------------------------------------------

    def _investigate_tier3(
        self,
        workflow: Workflow,
        focus_area: str,
    ) -> dict:
        """T3: Full snapshot — comprehensive data gathering."""
        results: dict = {"full_snapshot": True}

        if not self.context.oracle_pool:
            return results

        db_id = workflow.database_id

        # System-wide snapshot
        try:
            sys_stats = self.context.oracle_pool.execute_query(
                db_id,
                """SELECT name, value
                   FROM v$sysstat
                   WHERE name IN ('CPU used by this session',
                                  'physical reads', 'physical writes',
                                  'redo writes', 'user commits',
                                  'parse count (hard)', 'session logical reads')""",
                timeout=10,
            )
            results["system_stats"] = sys_stats
        except Exception as e:
            logger.warning("T3 system stats query failed: %s", e)

        return results

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def _identify_focus_area(self, t1_results: dict) -> str:
        """Analyze T1 results to identify the primary focus area.

        Focus areas: blocking, sql_perf, storage, memory, unknown.
        """
        wait_classes = t1_results.get("wait_classes", [])
        if not wait_classes:
            return "unknown"

        # Check dominant wait class
        for wc in wait_classes:
            wait_class = wc.get("WAIT_CLASS", wc.get("wait_class", ""))
            if wait_class == "Application":
                return "blocking"
            if wait_class in ("User I/O", "System I/O"):
                return "storage"
            if wait_class == "Concurrency":
                return "blocking"

        top_events = t1_results.get("top_events", [])
        for ev in top_events:
            event_name = ev.get("EVENT", ev.get("event", ""))
            if "enq" in event_name.lower() or "lock" in event_name.lower():
                return "blocking"
            if "buffer" in event_name.lower() or "read" in event_name.lower():
                return "sql_perf"

        return "sql_perf"  # Default to SQL performance

    def _is_conclusive(self, results: dict) -> bool:
        """Check if investigation results are conclusive enough to skip next tier."""
        return results.get("conclusive", False)

    # ------------------------------------------------------------------
    # Theory generation
    # ------------------------------------------------------------------

    def _generate_theories(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[Theory]:
        """Generate root cause theories based on investigation data."""
        if self._should_use_llm():
            return self._generate_theories_llm(workflow, investigation)
        return self._generate_theories_template(workflow, investigation)

    def _generate_theories_llm(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[Theory]:
        """Use LLM to generate theories from investigation data."""
        from sentri.llm.prompts import RCA_SYSTEM_PROMPT, build_rca_prompt

        user_prompt = build_rca_prompt(
            alert_type=workflow.alert_type,
            database_id=workflow.database_id,
            environment=workflow.environment,
            investigation_data=json.dumps(investigation, default=str),
        )

        try:
            raw = self._llm.generate(
                prompt=user_prompt,
                system_prompt=RCA_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=2048,
            )
            return self._parse_theories(raw)
        except Exception as e:
            self.logger.warning("LLM theory generation failed: %s", e)
            return self._generate_theories_template(workflow, investigation)

    def _generate_theories_template(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[Theory]:
        """Template-based theory generation from investigation data."""
        theories = []
        focus = investigation.get("focus_area", "unknown")

        if focus == "blocking":
            theories.append(
                Theory(
                    description="Session blocking chain causing lock contention",
                    confidence=0.80,
                    evidence=["Wait class analysis shows Application/Concurrency waits"],
                    focus_area="blocking",
                    fix=ResearchOption(
                        title="Identify and resolve blocking session",
                        description="Find the root blocker and determine if it can be terminated",
                        forward_sql="-- Identify blocking chain: SELECT * FROM v$session WHERE blocking_session IS NOT NULL",
                        rollback_sql="N/A: investigation only",
                        confidence=0.80,
                        risk_level="LOW",
                        reasoning="Blocking sessions cause cascading wait events.",
                        source="rca_template",
                    ),
                )
            )

        elif focus == "sql_perf":
            theories.append(
                Theory(
                    description="Poor SQL execution plan causing excessive resource usage",
                    confidence=0.75,
                    evidence=["Top SQL analysis shows high CPU/elapsed time"],
                    focus_area="sql_perf",
                    fix=ResearchOption(
                        title="Analyze and optimize top CPU-consuming SQL",
                        description="Review execution plan and consider statistics refresh or SQL profile",
                        forward_sql="-- Analyze top SQL execution plan",
                        rollback_sql="N/A: analysis only",
                        confidence=0.75,
                        risk_level="LOW",
                        reasoning="SQL performance issues are the most common root cause.",
                        source="rca_template",
                    ),
                )
            )

        elif focus == "storage":
            theories.append(
                Theory(
                    description="Storage pressure causing I/O wait events",
                    confidence=0.70,
                    evidence=["Wait class analysis shows User I/O dominance"],
                    focus_area="storage",
                    fix=ResearchOption(
                        title="Investigate storage bottleneck",
                        description="Check tablespace usage and datafile I/O patterns",
                        forward_sql="-- Check tablespace usage: SELECT * FROM dba_tablespace_usage_metrics",
                        rollback_sql="N/A: investigation only",
                        confidence=0.70,
                        risk_level="LOW",
                        reasoning="Storage pressure causes cascading performance issues.",
                        source="rca_template",
                    ),
                )
            )

        else:
            theories.append(
                Theory(
                    description="General performance degradation — needs deeper investigation",
                    confidence=0.50,
                    evidence=["T1 triage inconclusive"],
                    focus_area="unknown",
                    fix=ResearchOption(
                        title="Escalate for DBA review",
                        description="Root cause unclear — needs manual DBA investigation",
                        forward_sql="-- Escalate to DBA for manual RCA",
                        rollback_sql="N/A",
                        confidence=0.50,
                        risk_level="LOW",
                        reasoning="Automated RCA inconclusive.",
                        source="rca_template",
                    ),
                )
            )

        return theories

    def _parse_theories(self, raw: str) -> list[Theory]:
        """Parse LLM response into Theory objects."""
        if not raw or not raw.strip():
            return []

        text = raw.strip()
        if "```" in text:
            import re

            fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
            if fence_match:
                text = fence_match.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []

        if not isinstance(data, list):
            data = [data]

        theories = []
        for item in data:
            if not isinstance(item, dict):
                continue
            theory = Theory(
                description=item.get("description", ""),
                confidence=float(item.get("confidence", 0.5)),
                evidence=item.get("evidence", []),
                focus_area=item.get("focus_area", "unknown"),
            )
            # Convert fix if present
            fix_data = item.get("fix")
            if isinstance(fix_data, dict):
                theory.fix = ResearchOption(
                    title=fix_data.get("title", theory.description[:60]),
                    description=fix_data.get("description", theory.description),
                    forward_sql=fix_data.get("forward_sql", ""),
                    rollback_sql=fix_data.get("rollback_sql", "N/A"),
                    confidence=theory.confidence,
                    risk_level=fix_data.get("risk_level", "MEDIUM"),
                    reasoning="; ".join(theory.evidence),
                    source="rca_llm",
                )
            theories.append(theory)

        return theories

    def _template_fallback(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Fallback when no theories generated."""
        return [
            ResearchOption(
                title="Escalate for DBA review",
                description="RCA could not determine root cause — needs manual investigation",
                forward_sql="-- Escalate: automated RCA inconclusive",
                rollback_sql="N/A",
                confidence=0.40,
                risk_level="LOW",
                reasoning="No theories generated from investigation data.",
                source="rca_fallback",
            ),
        ]
