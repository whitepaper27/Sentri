"""Unknown Alert Agent — handles emails that don't match any alerts/*.md pattern.

v5.2: When Scout receives an unrecognized email, it creates a workflow with
alert_type="unknown". This agent uses LLM + DBA tools to:
1. Classify the alert (determine the real alert_type)
2. Investigate the database
3. Generate remediation options
4. ALWAYS require approval (unknown = untrusted)
5. After successful resolution, auto-generate an alerts/*.md file

Next time the same alert fires, it matches the generated .md and follows
the normal flow (DEV=auto, PROD=approval per policy).
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
    from sentri.memory.investigation_store import InvestigationStore
    from sentri.notifications.router import NotificationRouter
    from sentri.orchestrator.safety_mesh import SafetyMesh

logger = logging.getLogger("sentri.agents.unknown_alert_agent")

# Max tool calls for unknown alert investigation
_MAX_TOOL_CALLS = 5
_MAX_ITERATIONS = 6


class UnknownAlertAgent(SpecialistBase):
    """Specialist for unrecognized alerts. Uses LLM to classify and remediate."""

    def __init__(
        self,
        context: AgentContext,
        safety_mesh: "SafetyMesh",
        llm_provider: Optional[LLMProvider] = None,
        cost_tracker: Optional["CostTracker"] = None,
        investigation_store: Optional["InvestigationStore"] = None,
        notification_router: Optional["NotificationRouter"] = None,
    ):
        super().__init__(
            "unknown_alert_agent",
            context,
            safety_mesh,
            llm_provider,
            cost_tracker,
            investigation_store=investigation_store,
            notification_router=notification_router,
        )
        self._tool_executor = None
        self._classification: dict = {}  # Cached per-process call

    def _get_tool_executor(self):
        """Lazy-init the DBA tool executor."""
        if self._tool_executor is None:
            from sentri.llm.tools import DBAToolExecutor

            self._tool_executor = DBAToolExecutor(self.context.settings)
        return self._tool_executor

    # ------------------------------------------------------------------
    # Universal Agent Contract implementation
    # ------------------------------------------------------------------

    def verify(self, workflow: Workflow) -> tuple[bool, float]:
        """Step 1: For unknown alerts, we verify by running LLM classification.

        If the LLM classifies it as "not_a_db_alert", verification fails.
        Otherwise, assume verified with moderate confidence (LLM will investigate).
        """
        classification = self._classify_alert(workflow)
        self._classification = classification

        alert_type = classification.get("alert_type", "unknown")
        if alert_type in ("not_a_db_alert", "unknown"):
            logger.info("Classification failed or non-alert for %s (type=%s)", workflow.id, alert_type)
            return False, 0.0

        # Update workflow with classified alert_type and database_id
        classified_db = classification.get("database_id", "UNKNOWN")
        if classified_db and classified_db != "UNKNOWN":
            # Resolve database via aliases
            db_cfg = self.context.settings.resolve_database(classified_db)
            if db_cfg:
                classified_db = db_cfg.name
                # Update environment too
                self.context.workflow_repo.update_status(
                    workflow.id,
                    workflow.status,
                    metadata=json.dumps({
                        "classified_alert_type": alert_type,
                        "classified_database_id": classified_db,
                        "classification": classification,
                    }),
                )

        confidence = 0.70  # Moderate — LLM classification, not pattern match
        logger.info(
            "Unknown alert classified as '%s' on '%s' (confidence=%.2f)",
            alert_type,
            classified_db,
            confidence,
        )
        return True, confidence

    def investigate(self, workflow: Workflow) -> dict:
        """Step 2: Investigation is done during classification (agentic LLM call)."""
        return self._classification

    def propose(
        self,
        workflow: Workflow,
        investigation: dict,
    ) -> list[ResearchOption]:
        """Step 3: Extract remediation options from the LLM classification response."""
        options_data = investigation.get("options", [])
        if not options_data:
            return []

        options = []
        for item in options_data:
            if not isinstance(item, dict):
                continue
            try:
                opt = ResearchOption(
                    title=item.get("title", "Untitled"),
                    description=item.get("description", ""),
                    forward_sql=item.get("forward_sql", ""),
                    rollback_sql=item.get("rollback_sql", ""),
                    confidence=float(item.get("confidence", 0.5)),
                    risk_level=str(item.get("risk_level", "HIGH")).upper(),
                    reasoning=item.get("reasoning", ""),
                    source="llm_unknown",
                )
                if opt.forward_sql:
                    options.append(opt)
            except (ValueError, TypeError) as e:
                logger.warning("Skipping invalid option: %s", e)

        return options

    def learn(
        self,
        workflow: Workflow,
        selected: ResearchOption,
        result: dict,
    ) -> None:
        """Step 7: If successful, auto-generate an alerts/*.md file."""
        super().learn(workflow, selected, result)

        if result.get("status") in ("success", "needs_approval"):
            # Generate .md file for this alert type
            try:
                self._generate_alert_md(workflow, selected)
            except Exception as e:
                logger.warning(
                    "Failed to generate alert .md for %s: %s",
                    workflow.id,
                    e,
                )

    # ------------------------------------------------------------------
    # LLM classification (agentic — uses tools)
    # ------------------------------------------------------------------

    def _classify_alert(self, workflow: Workflow) -> dict:
        """Use LLM with DBA tools to classify an unknown alert."""
        if not self._llm.is_available():
            logger.warning("No LLM available for unknown alert classification")
            return {"alert_type": "unknown", "options": []}

        from sentri.llm.prompts import (
            UNKNOWN_ALERT_CLASSIFY_SYSTEM_PROMPT,
            build_unknown_alert_prompt,
        )
        from sentri.llm.tools import TOOL_DEFINITIONS

        # Extract raw email from suggestion
        subject = ""
        body = ""
        if workflow.suggestion:
            try:
                suggestion = json.loads(workflow.suggestion)
                extracted = suggestion.get("extracted_data", {})
                subject = extracted.get("raw_subject", suggestion.get("raw_email_subject", ""))
                body = extracted.get("raw_body", suggestion.get("raw_email_body", ""))
            except (json.JSONDecodeError, AttributeError):
                pass

        if not subject and not body:
            return {"alert_type": "unknown", "options": []}

        # Try to get profile data (we might not know the database yet)
        profile_data = "No database profile available"
        # Check if any database name appears in the email text
        full_text = f"{subject}\n{body}"
        for db_cfg in self.context.settings.databases:
            if db_cfg.name.lower() in full_text.lower():
                profile_json = self.context.environment_repo.get_profile(db_cfg.name)
                if profile_json:
                    profile_data = profile_json
                break

        user_prompt = build_unknown_alert_prompt(
            subject=subject,
            body=body,
            profile_data=profile_data,
        )

        messages = [{"role": "user", "content": user_prompt}]
        tool_executor = self._get_tool_executor()
        total_tool_calls = 0

        for iteration in range(_MAX_ITERATIONS):
            response = self._llm.generate_with_tools(
                messages=messages,
                tools=TOOL_DEFINITIONS,
                system_prompt=UNKNOWN_ALERT_CLASSIFY_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=3072,
            )

            # If no tool calls, this is the final response
            if response.is_final or not response.tool_calls:
                logger.info(
                    "Unknown alert classification complete: %d tool calls, %d iterations",
                    total_tool_calls,
                    iteration + 1,
                )
                return self._parse_classification(response.text)

            # Check tool call limit
            if total_tool_calls + len(response.tool_calls) > _MAX_TOOL_CALLS:
                calls_to_execute = response.tool_calls[: _MAX_TOOL_CALLS - total_tool_calls]
            else:
                calls_to_execute = response.tool_calls

            # Execute tool calls
            results = []
            for tc in calls_to_execute:
                logger.info("Tool call: %s(%s)", tc.name, json.dumps(tc.arguments, default=str)[:200])
                result = tool_executor.execute(tc)
                results.append(result)
                total_tool_calls += 1

            new_messages = self._llm.format_tool_results(response, results)
            messages.extend(new_messages)

            if total_tool_calls >= _MAX_TOOL_CALLS:
                messages.append({
                    "role": "user",
                    "content": "Tool call limit reached. Provide your final JSON response now.",
                })

        logger.warning("Unknown alert classification exhausted iterations")
        return {"alert_type": "unknown", "options": []}

    def _parse_classification(self, raw: str) -> dict:
        """Parse the LLM classification response."""
        from sentri.llm.json_utils import extract_json_from_text

        data = extract_json_from_text(raw)
        if data is None:
            logger.warning(
                "Failed to parse classification JSON. Raw: %s",
                (raw or "")[:500],
            )
            return {"alert_type": "unknown", "options": []}

        if isinstance(data, list):
            data = data[0] if data else {}

        if not isinstance(data, dict):
            return {"alert_type": "unknown", "options": []}

        return data

    # ------------------------------------------------------------------
    # Auto-generate alert .md file
    # ------------------------------------------------------------------

    def _generate_alert_md(self, workflow: Workflow, selected: ResearchOption) -> None:
        """Generate an alerts/*.md file from the successful classification."""
        classification = self._classification
        alert_type = classification.get("alert_type", "")

        if not alert_type or alert_type in ("unknown", "not_a_db_alert"):
            return

        # Check if .md already exists (don't overwrite)
        try:
            existing = self.context.policy_loader.load_alert(alert_type)
            if existing and existing.get("frontmatter"):
                logger.info("Alert .md for '%s' already exists — skipping generation", alert_type)
                return
        except Exception:
            pass  # File doesn't exist, good

        # Try LLM generation first, fall back to template
        md_content = self._generate_md_via_llm(classification, selected)
        if not md_content:
            md_content = self._generate_md_from_template(classification, selected)

        if not md_content:
            return

        # Write the file
        path = self.context.policy_loader.write_policy("alerts", alert_type, md_content)
        logger.info("Auto-generated alert policy: %s", path)

        # Audit the generation
        from sentri.core.models import AuditRecord

        self.context.audit_repo.create(
            AuditRecord(
                workflow_id=workflow.id,
                action_type="ALERT_MD_GENERATED",
                database_id=workflow.database_id,
                environment=workflow.environment,
                executed_by="unknown_alert_agent",
                result="GENERATED",
                evidence=f"alert_type={alert_type},path={path}",
            )
        )

    def _generate_md_via_llm(self, classification: dict, selected: ResearchOption) -> str:
        """Use LLM to generate a well-formatted alert .md file."""
        if not self._llm.is_available():
            return ""

        from sentri.llm.prompts import (
            GENERATE_ALERT_MD_SYSTEM_PROMPT,
            build_generate_alert_md_prompt,
        )

        prompt = build_generate_alert_md_prompt(
            alert_type=classification.get("alert_type", "unknown"),
            severity=classification.get("severity", "HIGH"),
            description=classification.get("description", ""),
            email_pattern_regex=classification.get("email_pattern_regex", ""),
            extracted_fields=classification.get("extracted_fields", []),
            verification_query=classification.get("verification_query", ""),
            forward_sql=selected.forward_sql,
            rollback_sql=selected.rollback_sql,
            validation_query=classification.get("validation_query", ""),
        )

        try:
            raw = self._llm.generate(
                prompt=prompt,
                system_prompt=GENERATE_ALERT_MD_SYSTEM_PROMPT,
                temperature=0.2,
                max_tokens=2048,
            )
            # Strip any markdown code fences the LLM might add
            content = raw.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                # Remove first and last fence lines
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines)
            return content
        except Exception as e:
            logger.warning("LLM .md generation failed: %s", e)
            return ""

    def _generate_md_from_template(self, classification: dict, selected: ResearchOption) -> str:
        """Generate a basic alert .md from a template (no LLM needed)."""
        alert_type = classification.get("alert_type", "unknown")
        severity = classification.get("severity", "HIGH")
        description = classification.get("description", f"Auto-detected: {alert_type}")
        regex = classification.get("email_pattern_regex", f"(?i){alert_type.replace('_', '.')}")
        fields = classification.get("extracted_fields", [])
        verification = classification.get("verification_query", "")
        validation = classification.get("validation_query", "")

        fields_md = ""
        for f in fields:
            fields_md += f"\n- `{f}`"
        if not fields_md:
            fields_md = "\n- `database_id` = group(1) -- Target database"

        action_type = alert_type.upper().replace("-", "_")

        return f"""---
type: alert_pattern
name: {alert_type}
severity: {severity}
action_type: {action_type}
version: "1.0"
generated: true
---

# {alert_type.replace('_', ' ').title()}

{description}

## Email Pattern

```regex
{regex}
```

## Extracted Fields
{fields_md}

## Verification Query

```sql
{verification or '-- verification query not available'}
```

## Forward Action

```sql
{selected.forward_sql or '-- forward action not available'}
```

## Rollback Action

```sql
{selected.rollback_sql or '-- N/A'}
```

## Validation Query

```sql
{validation or '-- validation query not available'}
```

## Risk Level

{selected.risk_level} -- Auto-generated from unknown alert classification.

## Tolerance

- `threshold`: +/- 5%
"""
