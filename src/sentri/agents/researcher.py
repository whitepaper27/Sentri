"""Agent 3: The Researcher - Multi-option remediation generator.

v2.1: Agentic tool-calling researcher. The LLM investigates the database
using DBA tools before generating remediation SQL.

Fallback chain: Agentic (tools) -> One-shot (prompt) -> Template (.md policy)
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from sentri.core.llm_interface import (
    LLMProvider,
    NoOpLLMProvider,
)
from sentri.core.models import ResearchOption, Workflow
from sentri.memory.manager import MemoryManager
from sentri.policy.alert_patterns import AlertPatterns
from sentri.rag.manager import RagManager

from .base import AgentContext, BaseAgent

logger = logging.getLogger("sentri.agents.researcher")

# Max tool calls per workflow (hard cap for cost control)
_MAX_TOOL_CALLS = 5
# Max loop iterations (tool calls + final response)
_MAX_ITERATIONS = 6


class ResearcherAgent(BaseAgent):
    """Generates remediation options: agentic, one-shot, or template-based."""

    def __init__(
        self,
        context: AgentContext,
        llm_provider: Optional[LLMProvider] = None,
        cost_tracker=None,
    ):
        super().__init__("researcher", context)
        self._llm = llm_provider or NoOpLLMProvider()
        self._cost_tracker = cost_tracker
        self._alert_patterns = AlertPatterns(context.policy_loader)
        self._memory = MemoryManager(context.db, context.policy_loader)
        self._rag = RagManager(
            context.policy_loader,
            context.environment_repo,
            context.settings,
        )
        self._tool_executor = None  # Lazy init

    def _get_tool_executor(self):
        """Lazy-init the DBA tool executor."""
        if self._tool_executor is None:
            from sentri.llm.tools import DBAToolExecutor

            self._tool_executor = DBAToolExecutor(self.context.settings)
        return self._tool_executor

    def process(self, workflow_id: str) -> dict:
        """Generate remediation options for a workflow.

        Returns:
            {
                "status": "success" | "failure",
                "agent": "researcher",
                "source": "llm_agentic" | "llm" | "template",
                "options": [ResearchOption, ...],
                "selected_option": ResearchOption,  # highest confidence
            }
        """
        workflow = self.context.workflow_repo.get(workflow_id)
        if not workflow:
            return {"status": "failure", "error": f"Workflow {workflow_id} not found"}

        # Three-level fallback: agentic -> one-shot -> template
        options = []
        source = "template"

        if self._should_use_llm():
            # Level 1: Try agentic (tool-calling) research
            try:
                options = self._run_agentic_research(workflow)
                if options:
                    source = "llm_agentic"
                    self.logger.info(
                        "Agentic research: %d options for %s", len(options), workflow_id
                    )
            except Exception as e:
                self.logger.warning("Agentic research failed for %s: %s", workflow_id, e)
                options = []

            # Level 2: Fall back to one-shot (no tools)
            if not options:
                try:
                    options = self._run_oneshot_research(workflow)
                    if options:
                        source = "llm"
                        self.logger.info(
                            "One-shot research: %d options for %s",
                            len(options),
                            workflow_id,
                        )
                except Exception as e:
                    self.logger.warning("One-shot research failed for %s: %s", workflow_id, e)
                    options = []

        # Level 3: Template fallback
        if not options:
            options = self._generate_template_options(workflow)
            source = "template"
            self.logger.info("Template: %d option(s) for %s", len(options), workflow_id)

        if not options:
            return {
                "status": "failure",
                "agent": "researcher",
                "error": "No remediation options generated",
            }

        # Sort by confidence descending
        options.sort(key=lambda o: o.confidence, reverse=True)
        selected = options[0]

        # Store research results on the workflow
        research_data = {
            "source": source,
            "option_count": len(options),
            "options": [json.loads(o.to_json()) for o in options],
            "selected_option_id": selected.option_id,
        }
        self.context.workflow_repo.update_status(
            workflow_id,
            workflow.status,
            metadata=json.dumps(research_data),
        )

        return {
            "status": "success",
            "agent": "researcher",
            "source": source,
            "options": options,
            "selected_option": selected,
        }

    # ------------------------------------------------------------------
    # Agentic research (v2.1) — multi-turn tool loop
    # ------------------------------------------------------------------

    def _run_agentic_research(self, wf: Workflow) -> list[ResearchOption]:
        """Run the agentic researcher: LLM investigates DB via tools, then generates SQL."""
        from sentri.llm.prompts import AGENTIC_RESEARCHER_SYSTEM_PROMPT, build_researcher_prompt
        from sentri.llm.tools import TOOL_DEFINITIONS

        # Build the initial user prompt
        alert_details = self._get_alert_details(wf)
        verification_data = wf.verification or "{}"
        profile_data = self._get_profile_data(wf.database_id)
        template_forward = self._alert_patterns.get_forward_action(wf.alert_type)
        template_rollback = self._alert_patterns.get_rollback_action(wf.alert_type)

        # Build memory context (database-scoped)
        memory_text = self._get_memory_context(wf)

        # Build ground truth docs (version-aware)
        ground_truth = self._get_ground_truth_docs(wf)

        user_prompt = build_researcher_prompt(
            alert_type=wf.alert_type,
            database_id=wf.database_id,
            environment=wf.environment,
            alert_details=alert_details,
            verification_data=verification_data,
            profile_data=profile_data,
            template_forward=template_forward,
            template_rollback=template_rollback,
            recent_actions=memory_text,
            ground_truth_docs=ground_truth,
        )

        messages = [{"role": "user", "content": user_prompt}]
        tool_executor = self._get_tool_executor()
        total_tool_calls = 0
        total_input_chars = len(user_prompt) + len(AGENTIC_RESEARCHER_SYSTEM_PROMPT)
        total_output_chars = 0

        for iteration in range(_MAX_ITERATIONS):
            response = self._llm.generate_with_tools(
                messages=messages,
                tools=TOOL_DEFINITIONS,
                system_prompt=AGENTIC_RESEARCHER_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=2048,
            )

            total_output_chars += len(response.text)

            # If no tool calls, this is the final response
            if response.is_final or not response.tool_calls:
                self.logger.info(
                    "Agentic research complete: %d tool calls, %d iterations",
                    total_tool_calls,
                    iteration + 1,
                )
                # Track cost
                self._track_cost(total_input_chars, total_output_chars)
                options = self._parse_llm_response(response.text, wf)
                return self._validate_options(options, wf)

            # Check tool call limit
            if total_tool_calls + len(response.tool_calls) > _MAX_TOOL_CALLS:
                # Tell LLM to wrap up
                remaining = _MAX_TOOL_CALLS - total_tool_calls
                calls_to_execute = response.tool_calls[:remaining]
                self.logger.info(
                    "Tool call limit approaching (%d/%d), executing %d more",
                    total_tool_calls,
                    _MAX_TOOL_CALLS,
                    len(calls_to_execute),
                )
            else:
                calls_to_execute = response.tool_calls

            # Execute tool calls
            results = []
            for tc in calls_to_execute:
                self.logger.info(
                    "Tool call: %s(%s)",
                    tc.name,
                    json.dumps(tc.arguments, default=str)[:200],
                )
                result = tool_executor.execute(tc)
                results.append(result)
                total_tool_calls += 1

                # Log result summary
                try:
                    _result_data = json.loads(result.content)
                    if result.is_error:
                        self.logger.warning("Tool %s error: %s", tc.name, result.content[:200])
                    else:
                        self.logger.info("Tool %s returned data", tc.name)
                except json.JSONDecodeError:
                    pass

                total_input_chars += len(result.content)

            # Format results and append to messages
            new_messages = self._llm.format_tool_results(response, results)
            messages.extend(new_messages)

            # If we've hit the cap, force final answer next iteration
            if total_tool_calls >= _MAX_TOOL_CALLS:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tool call limit reached. Based on the information gathered, "
                            "provide your final JSON response with remediation options now."
                        ),
                    }
                )

        # If we exhausted iterations, try to parse whatever we have
        self.logger.warning("Agentic research exhausted %d iterations", _MAX_ITERATIONS)
        self._track_cost(total_input_chars, total_output_chars)
        return []

    # ------------------------------------------------------------------
    # One-shot research (v2.0 fallback)
    # ------------------------------------------------------------------

    def _run_oneshot_research(self, wf: Workflow) -> list[ResearchOption]:
        """Generate options via a single LLM call (no tools)."""
        from sentri.llm.prompts import RESEARCHER_SYSTEM_PROMPT, build_researcher_prompt

        alert_details = self._get_alert_details(wf)
        verification_data = wf.verification or "{}"
        profile_data = self._get_profile_data(wf.database_id)
        template_forward = self._alert_patterns.get_forward_action(wf.alert_type)
        template_rollback = self._alert_patterns.get_rollback_action(wf.alert_type)

        # Build memory context (database-scoped)
        memory_text = self._get_memory_context(wf)

        # Build ground truth docs (version-aware)
        ground_truth = self._get_ground_truth_docs(wf)

        prompt = build_researcher_prompt(
            alert_type=wf.alert_type,
            database_id=wf.database_id,
            environment=wf.environment,
            alert_details=alert_details,
            verification_data=verification_data,
            profile_data=profile_data,
            template_forward=template_forward,
            template_rollback=template_rollback,
            recent_actions=memory_text,
            ground_truth_docs=ground_truth,
        )

        raw_response = self._llm.generate(
            prompt=prompt,
            system_prompt=RESEARCHER_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=2048,
        )

        self._track_cost(len(prompt), len(raw_response))

        options = self._parse_llm_response(raw_response, wf)

        # Substitute parameters in one-shot options (agentic uses real values)
        for opt in options:
            opt.forward_sql, opt.rollback_sql = self._substitute_params(
                wf, opt.forward_sql, opt.rollback_sql
            )
            opt.source = "llm"

        return self._validate_options(options, wf)

    # ------------------------------------------------------------------
    # Template fallback (v1.0 compat)
    # ------------------------------------------------------------------

    def _generate_template_options(self, wf: Workflow) -> list[ResearchOption]:
        """Generate a single option from the .md policy template."""
        forward_sql = self._alert_patterns.get_forward_action(wf.alert_type)
        rollback_sql = self._alert_patterns.get_rollback_action(wf.alert_type)
        risk_level = self._alert_patterns.get_risk_level(wf.alert_type)

        if not forward_sql:
            return []

        forward_sql, rollback_sql = self._substitute_params(wf, forward_sql, rollback_sql)

        return [
            ResearchOption(
                title=f"Standard remediation for {wf.alert_type}",
                description="Policy-defined action from alert template",
                forward_sql=forward_sql,
                rollback_sql=rollback_sql,
                confidence=1.0,
                risk_level=risk_level,
                reasoning="Standard DBA procedure defined in alert policy",
                source="template",
            )
        ]

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _should_use_llm(self) -> bool:
        """Check if LLM is available and within budget."""
        if not self._llm.is_available():
            return False
        if self._cost_tracker and not self._cost_tracker.is_within_budget():
            self.logger.warning("Daily LLM budget exhausted — using template fallback")
            return False
        return True

    def _track_cost(self, input_chars: int, output_chars: int) -> None:
        """Record approximate token usage."""
        if self._cost_tracker:
            self._cost_tracker.record_usage(
                provider=self._llm.name,
                input_tokens=input_chars // 4,
                output_tokens=output_chars // 4,
            )

    def _parse_llm_response(self, raw: str, wf: Workflow) -> list[ResearchOption]:
        """Parse the LLM JSON response into ResearchOption objects."""
        if not raw or not raw.strip():
            return []

        text = raw.strip()

        # Strip markdown code fences if present
        if "```" in text:
            import re as _re

            # Extract content between ```json ... ``` or ``` ... ```
            fence_match = _re.search(r"```(?:json)?\s*\n(.*?)```", text, _re.DOTALL)
            if fence_match:
                text = fence_match.group(1).strip()

        # Try direct parse first
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass

        # If direct parse failed, extract JSON array from mixed text
        if data is None:
            # Find the first [ ... ] block in the text
            bracket_start = text.find("[")
            if bracket_start >= 0:
                # Find matching closing bracket
                depth = 0
                for i in range(bracket_start, len(text)):
                    if text[i] == "[":
                        depth += 1
                    elif text[i] == "]":
                        depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(text[bracket_start : i + 1])
                        except json.JSONDecodeError:
                            pass
                        break

        if data is None:
            self.logger.warning("Failed to parse LLM response as JSON")
            self.logger.debug("Raw response: %s", raw[:500])
            return []

        if not isinstance(data, list):
            data = [data]

        options = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                opt = ResearchOption(
                    title=item.get("title", "Untitled"),
                    description=item.get("description", ""),
                    forward_sql=item.get("forward_sql", ""),
                    rollback_sql=item.get("rollback_sql", ""),
                    confidence=float(item.get("confidence", 0.5)),
                    risk_level=str(item.get("risk_level", "MEDIUM")).upper(),
                    reasoning=item.get("reasoning", ""),
                    source="llm",
                )
                if opt.forward_sql:
                    options.append(opt)
            except (ValueError, TypeError) as e:
                self.logger.warning("Skipping invalid LLM option: %s", e)

        return options

    def _substitute_params(
        self, wf: Workflow, forward_sql: str, rollback_sql: str
    ) -> tuple[str, str]:
        """Replace :placeholder tokens with extracted data from the suggestion."""
        if not wf.suggestion:
            return forward_sql, rollback_sql

        try:
            suggestion_data = json.loads(wf.suggestion)
            extracted = suggestion_data.get("extracted_data", {})
            for key, val in extracted.items():
                if val is not None:
                    placeholder = f":{key}"
                    forward_sql = forward_sql.replace(placeholder, str(val))
                    rollback_sql = rollback_sql.replace(placeholder, str(val))
        except (json.JSONDecodeError, AttributeError):
            pass

        return forward_sql, rollback_sql

    def _get_alert_details(self, wf: Workflow) -> str:
        """Extract alert details from the workflow suggestion."""
        if not wf.suggestion:
            return f"Alert type: {wf.alert_type}, Database: {wf.database_id}"

        try:
            suggestion = json.loads(wf.suggestion)
            parts = [
                f"Alert type: {wf.alert_type}",
                f"Database: {wf.database_id}",
                f"Environment: {wf.environment}",
            ]
            extracted = suggestion.get("extracted_data", {})
            for k, v in extracted.items():
                parts.append(f"{k}: {v}")
            return "\n".join(parts)
        except (json.JSONDecodeError, AttributeError):
            return f"Alert type: {wf.alert_type}, Database: {wf.database_id}"

    def _get_memory_context(self, wf: Workflow) -> str:
        """Build memory context for the LLM prompt (database-scoped)."""
        try:
            ctx = self._memory.get_context(
                database_id=wf.database_id,
                alert_type=wf.alert_type,
                environment=wf.environment,
            )
            return self._memory.format_for_prompt(ctx)
        except Exception as e:
            self.logger.warning("Memory context failed for %s: %s", wf.database_id, e)
            return ""

    def _get_ground_truth_docs(self, wf: Workflow) -> str:
        """Build ground truth doc context for the LLM prompt (version-aware)."""
        try:
            ctx = self._rag.get_context(
                alert_type=wf.alert_type,
                database_id=wf.database_id,
            )
            return self._rag.format_for_prompt(ctx)
        except Exception as e:
            self.logger.warning(
                "Ground truth docs failed for %s/%s: %s",
                wf.alert_type,
                wf.database_id,
                e,
            )
            return ""

    def _validate_options(
        self, options: list[ResearchOption], wf: Workflow
    ) -> list[ResearchOption]:
        """Validate LLM-generated options against ground truth rules.

        Drops any option whose forward_sql violates a hard rule.
        Better no action than wrong action — if all options are invalid,
        returns empty list which triggers template fallback.
        """
        if not options:
            return options

        # Check if validation is enabled via settings
        if not self._rag._config.validate_sql:
            return options

        validated = []
        for opt in options:
            try:
                result = self._rag.validate_sql(
                    opt.forward_sql,
                    wf.alert_type,
                    wf.database_id,
                )
                if result.is_valid:
                    validated.append(opt)
                else:
                    violations_str = "; ".join(
                        f"{v.rule_id}[{v.severity}]: {v.message}" for v in result.violations
                    )
                    self.logger.warning(
                        "SQL validation FAILED for option '%s': %s",
                        opt.title,
                        violations_str,
                    )
            except Exception as e:
                self.logger.warning(
                    "SQL validation error for option '%s': %s — keeping option",
                    opt.title,
                    e,
                )
                # On validation error, keep the option (fail-open for validator bugs)
                validated.append(opt)

        if validated:
            self.logger.info(
                "SQL validation: %d/%d options passed for %s",
                len(validated),
                len(options),
                wf.alert_type,
            )
        elif options:
            self.logger.warning(
                "SQL validation: ALL %d options failed for %s — falling back to template",
                len(options),
                wf.alert_type,
            )

        return validated

    def _get_profile_data(self, database_id: str) -> str:
        """Get database profile JSON for the prompt."""
        profile_json = self.context.environment_repo.get_profile(database_id)
        if profile_json:
            return profile_json
        return "No database profile available"
