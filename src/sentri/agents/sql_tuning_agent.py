"""SQL Tuning Agent — specialist for performance-related alerts.

Handles: long_running_sql, cpu_high, check_finding:stale_stats.
Uses v4.0 DBA tools to investigate before proposing fixes.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Optional

from sentri.core.llm_interface import LLMProvider
from sentri.core.models import ResearchOption, Workflow

from .base import AgentContext
from .specialist_base import SpecialistBase

if TYPE_CHECKING:
    from sentri.llm.cost_tracker import CostTracker
    from sentri.orchestrator.safety_mesh import SafetyMesh

logger = logging.getLogger("sentri.agents.sql_tuning_agent")


class SQLTuningAgent(SpecialistBase):
    """Specialist for SQL performance and tuning alerts."""

    HANDLED_ALERTS = frozenset(
        {
            "long_running_sql",
            "cpu_high",
            "check_finding:stale_stats",
        }
    )

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
            "sql_tuning_agent",
            context,
            safety_mesh,
            llm_provider,
            cost_tracker,
            investigation_store=investigation_store,
            notification_router=notification_router,
        )

    def verify(self, workflow: Workflow) -> tuple[bool, float]:
        """Verify the performance issue is still active.

        - cpu_high: Check if CPU wait events are still elevated.
        - long_running_sql: Check if the session is still running.
        - stale_stats: Always true (stats don't un-stale themselves).
        """
        alert_type = workflow.alert_type

        if alert_type == "check_finding:stale_stats":
            # Stale stats persist until fixed
            return True, 0.95

        if alert_type == "cpu_high":
            return self._verify_cpu_high(workflow)

        if alert_type == "long_running_sql":
            return self._verify_long_running(workflow)

        # Unknown — assume valid with moderate confidence
        return True, 0.70

    def investigate(self, workflow: Workflow) -> dict:
        """Gather investigation context using DBA tools.

        Investigation strategy varies by alert type:
        - cpu_high: wait_events → top_sql(cpu_time) → sql_plan for offender
        - long_running_sql: session_info → sql_stats → sql_plan → table_stats
        - stale_stats: Pass finding data through
        """
        alert_type = workflow.alert_type
        extracted = self._get_extracted_data(workflow)

        if alert_type == "cpu_high":
            return self._investigate_cpu_high(workflow, extracted)

        if alert_type == "long_running_sql":
            return self._investigate_long_running(workflow, extracted)

        if alert_type == "check_finding:stale_stats":
            return self._investigate_stale_stats(workflow, extracted)

        return {"alert_type": alert_type, "extracted": extracted}

    def propose(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Generate remediation candidates based on investigation.

        Uses LLM if available; falls back to template-based candidates.
        """
        if self._should_use_llm():
            return self._propose_with_llm(workflow, investigation)
        return self._propose_template(workflow, investigation)

    # ------------------------------------------------------------------
    # Verify helpers
    # ------------------------------------------------------------------

    def _verify_cpu_high(self, workflow: Workflow) -> tuple[bool, float]:
        """Check if CPU utilization is still high."""
        if not self.context.oracle_pool:
            return True, 0.70  # Can't verify → assume true

        try:
            _rows = self.context.oracle_pool.execute_query(
                workflow.database_id,
                """SELECT value FROM v$sysstat
                   WHERE name = 'CPU used by this session'""",
                timeout=10,
            )
            # If we can query, the issue might still exist
            return True, 0.80
        except Exception as e:
            self.logger.warning("CPU verify query failed: %s", e)
            return True, 0.70

    def _verify_long_running(self, workflow: Workflow) -> tuple[bool, float]:
        """Check if the long-running session is still active."""
        extracted = self._get_extracted_data(workflow)
        sid = extracted.get("sid")

        if not sid or not self.context.oracle_pool:
            return True, 0.70

        try:
            rows = self.context.oracle_pool.execute_query(
                workflow.database_id,
                "SELECT status FROM v$session WHERE sid = :1",
                params=(int(sid),),
                timeout=10,
            )
            if rows and rows[0].get("STATUS") == "ACTIVE":
                return True, 0.90
            return False, 0.30  # Session ended
        except Exception:
            return True, 0.70

    # ------------------------------------------------------------------
    # Investigate helpers
    # ------------------------------------------------------------------

    def _investigate_cpu_high(
        self,
        workflow: Workflow,
        extracted: dict,
    ) -> dict:
        """CPU high investigation: wait events → top SQL → plan."""
        investigation = {
            "alert_type": "cpu_high",
            "database_id": workflow.database_id,
            "extracted": extracted,
        }

        if not self.context.oracle_pool:
            return investigation

        # Step 1: Get wait events
        try:
            wait_events = self.context.oracle_pool.execute_query(
                workflow.database_id,
                """SELECT wait_class, event, total_waits, time_waited
                   FROM v$system_event
                   WHERE wait_class != 'Idle'
                   ORDER BY time_waited DESC
                   FETCH FIRST 10 ROWS ONLY""",
                timeout=10,
            )
            investigation["wait_events"] = wait_events
        except Exception as e:
            self.logger.warning("Wait events query failed: %s", e)

        # Step 2: Get top SQL by CPU
        try:
            top_sql = self.context.oracle_pool.execute_query(
                workflow.database_id,
                """SELECT sql_id, cpu_time, elapsed_time, executions,
                          buffer_gets, disk_reads,
                          SUBSTR(sql_text, 1, 200) as sql_text
                   FROM v$sql
                   ORDER BY cpu_time DESC
                   FETCH FIRST 5 ROWS ONLY""",
                timeout=10,
            )
            investigation["top_sql"] = top_sql
        except Exception as e:
            self.logger.warning("Top SQL query failed: %s", e)

        return investigation

    def _investigate_long_running(
        self,
        workflow: Workflow,
        extracted: dict,
    ) -> dict:
        """Long running SQL investigation: session → SQL stats → plan."""
        investigation = {
            "alert_type": "long_running_sql",
            "database_id": workflow.database_id,
            "extracted": extracted,
        }

        sid = extracted.get("sid")
        sql_id = extracted.get("sql_id")

        if not self.context.oracle_pool:
            return investigation

        # Step 1: Get session info
        if sid:
            try:
                session_info = self.context.oracle_pool.execute_query(
                    workflow.database_id,
                    """SELECT sid, serial#, username, status, sql_id,
                              event, wait_class, seconds_in_wait,
                              program, machine
                       FROM v$session WHERE sid = :1""",
                    params=(int(sid),),
                    timeout=10,
                )
                investigation["session_info"] = session_info
                if session_info and not sql_id:
                    sql_id = session_info[0].get("SQL_ID")
            except Exception as e:
                self.logger.warning("Session info query failed: %s", e)

        # Step 2: Get SQL stats
        if sql_id:
            try:
                sql_stats = self.context.oracle_pool.execute_query(
                    workflow.database_id,
                    """SELECT sql_id, cpu_time, elapsed_time, executions,
                              buffer_gets, disk_reads, rows_processed,
                              SUBSTR(sql_fulltext, 1, 500) as sql_text
                       FROM v$sql WHERE sql_id = :1""",
                    params=(sql_id,),
                    timeout=10,
                )
                investigation["sql_stats"] = sql_stats
            except Exception as e:
                self.logger.warning("SQL stats query failed: %s", e)

        return investigation

    def _investigate_stale_stats(
        self,
        workflow: Workflow,
        extracted: dict,
    ) -> dict:
        """Stale stats investigation: pass finding data through."""
        # The finding data from the proactive check contains everything
        findings = extracted.get("findings", [])
        return {
            "alert_type": "check_finding:stale_stats",
            "database_id": workflow.database_id,
            "stale_tables": findings,
            "extracted": extracted,
        }

    # ------------------------------------------------------------------
    # Propose helpers
    # ------------------------------------------------------------------

    def _propose_with_llm(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Use LLM to generate tuning candidates."""
        from sentri.llm.prompts import (
            SQL_TUNING_SYSTEM_PROMPT,
            build_sql_tuning_prompt,
        )

        user_prompt = build_sql_tuning_prompt(
            alert_type=workflow.alert_type,
            database_id=workflow.database_id,
            environment=workflow.environment,
            investigation_data=json.dumps(investigation, default=str),
        )

        try:
            raw = self._llm.generate(
                prompt=user_prompt,
                system_prompt=SQL_TUNING_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=3072,
                json_mode=True,
            )
            return self._parse_options(raw)
        except Exception as e:
            self.logger.warning("LLM propose failed: %s — using template", e)
            return self._propose_template(workflow, investigation)

    def _propose_template(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Template-based fallback: generate candidates without LLM."""
        alert_type = workflow.alert_type
        _extracted = investigation.get("extracted", {})

        if alert_type == "check_finding:stale_stats":
            return self._template_stale_stats(workflow, investigation)

        if alert_type == "cpu_high":
            return self._template_cpu_high(workflow, investigation)

        if alert_type == "long_running_sql":
            return self._template_long_running(workflow, investigation)

        return []

    def _template_stale_stats(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Template: gather stats on stale tables."""
        stale_tables = investigation.get("stale_tables", [])
        if not stale_tables:
            return []

        options = []
        # Option 1: Gather stats on all stale tables
        owners_tables = []
        for t in stale_tables[:5]:
            owner = t.get("OWNER", t.get("owner", ""))
            table = t.get("TABLE_NAME", t.get("table_name", ""))
            if owner and table:
                owners_tables.append((owner, table))

        if owners_tables:
            stats_sql = "\n".join(
                f"EXEC DBMS_STATS.GATHER_TABLE_STATS('{o}', '{t}');" for o, t in owners_tables
            )
            options.append(
                ResearchOption(
                    title="Gather stale statistics",
                    description=f"Gather optimizer statistics on {len(owners_tables)} stale tables",
                    forward_sql=stats_sql,
                    rollback_sql="N/A: gathering stats is safe and non-destructive",
                    confidence=0.90,
                    risk_level="LOW",
                    reasoning="Stale statistics cause poor execution plans. Gathering fresh stats is standard DBA practice.",
                    source="template",
                )
            )

        # Option 2: Escalate if too many tables
        if len(stale_tables) > 10:
            options.append(
                ResearchOption(
                    title="Escalate: large-scale stats gather needed",
                    description="Many tables have stale stats — needs DBA review for batch job",
                    forward_sql="-- Escalate to DBA for batch DBMS_STATS.GATHER_SCHEMA_STATS",
                    rollback_sql="N/A",
                    confidence=0.70,
                    risk_level="LOW",
                    reasoning=f"{len(stale_tables)} tables with stale stats suggests a systemic issue.",
                    source="template",
                )
            )

        return options

    def _template_cpu_high(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Template: CPU high remediation candidates."""
        top_sql = investigation.get("top_sql", [])

        options = [
            ResearchOption(
                title="Identify and tune top CPU consumer",
                description="Analyze the top SQL by CPU time and create a SQL profile or baseline",
                forward_sql="-- Investigate top SQL: "
                + (top_sql[0].get("SQL_ID", "unknown") if top_sql else "unknown"),
                rollback_sql="N/A: investigation only",
                confidence=0.75,
                risk_level="LOW",
                reasoning="Identifying the top CPU consumer is the first step in CPU troubleshooting.",
                source="template",
            ),
        ]

        return options

    def _template_long_running(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Template: long running SQL remediation."""
        extracted = investigation.get("extracted", {})
        _sid = extracted.get("sid", "unknown")
        sql_id = extracted.get("sql_id", "unknown")

        options = [
            ResearchOption(
                title="Analyze execution plan for long-running SQL",
                description=f"Get execution plan for SQL_ID={sql_id} to identify inefficiencies",
                forward_sql=f"-- Analyze plan for SQL_ID: {sql_id}",
                rollback_sql="N/A: analysis only",
                confidence=0.80,
                risk_level="LOW",
                reasoning="Execution plan analysis reveals missing indexes, bad joins, or stale stats.",
                source="template",
            ),
            ResearchOption(
                title="Gather table statistics for involved tables",
                description="Ensure optimizer statistics are current for tables in the slow query",
                forward_sql="-- DBMS_STATS.GATHER_TABLE_STATS for tables in the query",
                rollback_sql="N/A: safe operation",
                confidence=0.70,
                risk_level="LOW",
                reasoning="Stale statistics are a common cause of bad execution plans.",
                source="template",
            ),
        ]

        return options

    def _parse_options(self, raw: str) -> list[ResearchOption]:
        """Parse LLM response into ResearchOption list."""
        if not raw or not raw.strip():
            return []

        text = raw.strip()
        # Strip markdown fences
        if "```" in text:
            import re

            fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
            if fence_match:
                text = fence_match.group(1).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.logger.warning("Failed to parse LLM response as JSON")
            return []

        if not isinstance(data, list):
            data = [data]

        options = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                options.append(
                    ResearchOption(
                        title=item.get("title", "Untitled"),
                        description=item.get("description", ""),
                        forward_sql=item.get("forward_sql", ""),
                        rollback_sql=item.get("rollback_sql", "N/A"),
                        confidence=float(item.get("confidence", 0.5)),
                        risk_level=item.get("risk_level", "MEDIUM"),
                        reasoning=item.get("reasoning", ""),
                        source="llm",
                    )
                )
            except (ValueError, TypeError):
                continue

        return options
