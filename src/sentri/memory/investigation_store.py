"""InvestigationStore — persist specialist agent analysis as .md files.

Each investigation is saved as a human-readable markdown file with YAML
frontmatter.  Files are named ``YYYY-MM-DD_HHMMSS_{database_id}_{alert_type}.md``
and stored in the ``investigations/`` directory.

These files serve two purposes:
1. **DBA visibility** — open any file to see what the agent found and decided.
2. **LLM memory** — MemoryManager loads recent investigations and injects them
   into prompts so specialists learn from past incidents.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sentri.memory.investigation_store")

# Maximum rows rendered in a markdown table (keeps files readable).
_MAX_TABLE_ROWS = 20
# Maximum characters per table cell.
_MAX_CELL_LEN = 100


@dataclass
class InvestigationRecord:
    """A parsed investigation .md file — used for memory injection."""

    file_path: str = ""
    timestamp: str = ""
    workflow_id: str = ""
    database_id: str = ""
    alert_type: str = ""
    environment: str = ""
    agent_name: str = ""
    confidence: float = 0.0
    focus_area: str = ""
    status: str = ""
    investigation_summary: str = ""
    selected_option_title: str = ""
    selected_option_reasoning: str = ""
    candidates_count: int = 0
    outcome: str = ""


class InvestigationStore:
    """Write, read and query investigation .md files."""

    def __init__(self, investigations_dir: Path):
        self._dir = investigations_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(
        self,
        workflow_id: str,
        database_id: str,
        alert_type: str,
        environment: str,
        agent_name: str,
        confidence: float,
        investigation: dict,
        candidates: list,
        selected,
        result: dict,
    ) -> Optional[Path]:
        """Persist a full investigation as a .md file.

        Returns the file path on success, ``None`` on failure.
        *candidates* is a list of ``ResearchOption``; *selected* is one
        ``ResearchOption``; *result* is the dict returned by
        ``SpecialistBase.process()``.
        """
        try:
            self._dir.mkdir(parents=True, exist_ok=True)

            now = datetime.now(timezone.utc)
            safe_db = re.sub(r"[^\w\-]", "_", database_id)
            safe_alert = re.sub(r"[^\w\-]", "_", alert_type)
            filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{safe_db}_{safe_alert}.md"
            path = self._dir / filename

            content = self._build_markdown(
                workflow_id=workflow_id,
                database_id=database_id,
                alert_type=alert_type,
                environment=environment,
                agent_name=agent_name,
                confidence=confidence,
                investigation=investigation,
                candidates=candidates,
                selected=selected,
                result=result,
                timestamp=now,
            )

            path.write_text(content, encoding="utf-8")
            logger.info("Investigation saved: %s", path.name)
            return path
        except Exception as exc:
            logger.warning("Failed to save investigation: %s", exc)
            return None

    def load_recent(
        self,
        database_id: str,
        max_files: int = 5,
        max_age_days: int = 90,
    ) -> list[InvestigationRecord]:
        """Load recent investigations for a database (newest first)."""
        if not self._dir.exists():
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        safe_db = re.sub(r"[^\w\-]", "_", database_id)

        records: list[InvestigationRecord] = []
        # Sort newest-first by filename (date prefix guarantees correct order).
        for path in sorted(self._dir.glob(f"*_{safe_db}_*.md"), reverse=True):
            if len(records) >= max_files:
                break
            rec = self._parse_file(path)
            if rec is None:
                continue
            # Age filter: compare file timestamp against cutoff.
            try:
                ts = datetime.fromisoformat(rec.timestamp)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass  # Keep the record if timestamp parsing fails.
            records.append(rec)

        return records

    def load_for_workflow(self, workflow_id: str) -> Optional[InvestigationRecord]:
        """Find the investigation file for a specific workflow ID (or prefix)."""
        if not self._dir.exists():
            return None

        for path in sorted(self._dir.glob("*.md"), reverse=True):
            try:
                text = path.read_text(encoding="utf-8")
                fm = self._parse_frontmatter(text)
                wf_id = fm.get("workflow_id", "")
                if wf_id == workflow_id or wf_id.startswith(workflow_id):
                    return self._parse_file(path)
            except Exception:
                continue
        return None

    def format_for_prompt(self, records: list[InvestigationRecord]) -> str:
        """Format investigation records as markdown for LLM prompt injection."""
        if not records:
            return ""

        parts = [f"## Past Investigations on {records[0].database_id}"]
        for rec in records:
            parts.append(f"\n### {rec.alert_type} ({rec.timestamp[:10]}) — {rec.outcome}")
            parts.append(f"- Agent: {rec.agent_name}, Confidence: {rec.confidence:.2f}")
            if rec.selected_option_title:
                parts.append(f"- Selected: {rec.selected_option_title}")
            if rec.selected_option_reasoning:
                parts.append(f"- Reasoning: {rec.selected_option_reasoning[:300]}")
            if rec.investigation_summary:
                parts.append(f"- Key findings: {rec.investigation_summary[:500]}")

        parts.append("")
        parts.append("Use these past investigations to:")
        parts.append("- Avoid repeating failed approaches")
        parts.append("- Build on successful investigation patterns")
        parts.append("- Identify recurring root causes across investigations")
        return "\n".join(parts)

    def cleanup(self, retention_days: int = 90) -> int:
        """Delete investigation files older than *retention_days*. Returns count."""
        if not self._dir.exists():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted = 0
        for path in self._dir.glob("*.md"):
            try:
                # Extract date from filename prefix (YYYY-MM-DD_HHMMSS).
                date_str = path.stem[:15]  # e.g. "2026-02-27_143218"
                file_dt = datetime.strptime(date_str, "%Y-%m-%d_%H%M%S")
                file_dt = file_dt.replace(tzinfo=timezone.utc)
                if file_dt < cutoff:
                    path.unlink()
                    deleted += 1
            except (ValueError, OSError):
                continue
        return deleted

    # ------------------------------------------------------------------
    # Markdown builder
    # ------------------------------------------------------------------

    def _build_markdown(
        self,
        workflow_id: str,
        database_id: str,
        alert_type: str,
        environment: str,
        agent_name: str,
        confidence: float,
        investigation: dict,
        candidates: list,
        selected,
        result: dict,
        timestamp: datetime,
    ) -> str:
        status = result.get("status", "unknown")
        ts_iso = timestamp.isoformat()
        ts_display = timestamp.strftime("%Y-%m-%d %H:%M UTC")

        lines: list[str] = []

        # --- YAML frontmatter ---
        lines.append("---")
        lines.append(f"workflow_id: {workflow_id}")
        lines.append(f"database_id: {database_id}")
        lines.append(f"alert_type: {alert_type}")
        lines.append(f"environment: {environment}")
        lines.append(f"agent: {agent_name}")
        lines.append(f"confidence: {confidence:.2f}")
        lines.append(f"timestamp: {ts_iso}")
        lines.append(f"status: {status}")
        lines.append("---")
        lines.append("")

        # --- Title ---
        lines.append(f"# {alert_type} on {database_id}")
        lines.append("")
        lines.append(
            f"**Agent**: {agent_name} | **Time**: {ts_display} "
            f"| **Environment**: {environment} | **Confidence**: {confidence:.2f}"
        )
        lines.append("")

        # --- Investigation findings ---
        lines.append("## Investigation Findings")
        lines.append("")
        inv_text = self._format_investigation_section(investigation, agent_name)
        lines.append(inv_text)
        lines.append("")

        # --- Candidates ---
        lines.append("## Candidates Considered")
        lines.append("")
        selected_id = getattr(selected, "option_id", "") if selected else ""
        for idx, cand in enumerate(candidates, 1):
            title = getattr(cand, "title", "Untitled")
            is_sel = getattr(cand, "option_id", "") == selected_id
            marker = " (SELECTED)" if is_sel else ""
            lines.append(f"### {idx}. {title}{marker}")
            conf = getattr(cand, "confidence", 0.0)
            risk = getattr(cand, "risk_level", "MEDIUM")
            source = getattr(cand, "source", "unknown")
            lines.append(f"- **Confidence**: {conf:.2f} | **Risk**: {risk} | **Source**: {source}")
            fwd = getattr(cand, "forward_sql", "")
            if fwd:
                lines.append(f"- **SQL**: `{fwd[:200]}`")
            rb = getattr(cand, "rollback_sql", "")
            if rb and rb != "N/A":
                lines.append(f"- **Rollback**: `{rb[:200]}`")
            reasoning = getattr(cand, "reasoning", "")
            if reasoning:
                lines.append(f"- **Reasoning**: {reasoning}")
            lines.append("")

        # --- Decision ---
        lines.append("## Decision")
        lines.append("")
        if selected:
            lines.append(f"**Selected**: {getattr(selected, 'title', 'N/A')}")
            sel_reasoning = getattr(selected, "reasoning", "")
            if sel_reasoning:
                lines.append(f"**Reasoning**: {sel_reasoning}")
        else:
            lines.append("No candidate selected.")
        lines.append("")

        # --- Outcome ---
        lines.append("## Outcome")
        lines.append("")
        lines.append(f"**Status**: {status}")
        error = result.get("error", "")
        if error:
            lines.append(f"**Error**: {error}")
        reasons = result.get("reasons", [])
        if reasons:
            lines.append(f"**Safety Mesh**: {'; '.join(reasons)}")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Investigation formatting (agent-specific)
    # ------------------------------------------------------------------

    def _format_investigation_section(self, investigation: dict, agent_name: str) -> str:
        """Render investigation data as readable markdown."""
        if not investigation:
            return "_No investigation data (delegated to existing pipeline)._"

        parts: list[str] = []

        # Show metadata keys first.
        for meta_key in ("alert_type", "database_id", "tier", "focus_area"):
            if meta_key in investigation and investigation[meta_key]:
                parts.append(f"- **{meta_key}**: {investigation[meta_key]}")

        if parts:
            parts.append("")

        # Agent-specific rendering.
        if agent_name == "sql_tuning_agent":
            parts.extend(self._format_sql_tuning(investigation))
        elif agent_name == "rca_agent":
            parts.extend(self._format_rca(investigation))
        else:
            parts.extend(self._format_generic(investigation))

        return "\n".join(parts)

    def _format_sql_tuning(self, inv: dict) -> list[str]:
        parts: list[str] = []
        for key, heading in [
            ("wait_events", "Wait Events"),
            ("top_sql", "Top SQL by CPU"),
            ("session_info", "Session Info"),
            ("sql_stats", "SQL Stats"),
        ]:
            rows = inv.get(key)
            if rows and isinstance(rows, list):
                parts.append(f"### {heading}")
                parts.append("")
                parts.append(self._dict_list_to_table(rows))
                parts.append("")

        # Extracted data.
        extracted = inv.get("extracted", {})
        if extracted:
            parts.append("### Extracted Alert Data")
            for k, v in extracted.items():
                parts.append(f"- {k}: {v}")
            parts.append("")

        return parts

    def _format_rca(self, inv: dict) -> list[str]:
        parts: list[str] = []

        for tier_key, tier_label in [
            ("t1", "Tier 1: Quick Triage"),
            ("t2", "Tier 2: Focused Investigation"),
            ("t3", "Tier 3: Full Snapshot"),
        ]:
            tier_data = inv.get(tier_key)
            if not tier_data or not isinstance(tier_data, dict):
                continue
            parts.append(f"### {tier_label}")
            parts.append("")
            for sub_key, rows in tier_data.items():
                if isinstance(rows, list) and rows:
                    parts.append(f"#### {sub_key}")
                    parts.append("")
                    parts.append(self._dict_list_to_table(rows))
                    parts.append("")

        # Extracted data.
        extracted = inv.get("extracted", {})
        if extracted:
            parts.append("### Extracted Alert Data")
            for k, v in extracted.items():
                parts.append(f"- {k}: {v}")
            parts.append("")

        return parts

    def _format_generic(self, inv: dict) -> list[str]:
        """Fallback: render unknown investigation keys as JSON."""
        parts: list[str] = []
        skip = {"alert_type", "database_id", "tier", "focus_area", "extracted"}
        for key, value in inv.items():
            if key in skip:
                continue
            if isinstance(value, list) and value and isinstance(value[0], dict):
                parts.append(f"### {key}")
                parts.append("")
                parts.append(self._dict_list_to_table(value))
                parts.append("")
            elif isinstance(value, (dict, list)):
                parts.append(f"### {key}")
                parts.append("")
                parts.append("```json")
                parts.append(json.dumps(value, indent=2, default=str)[:500])
                parts.append("```")
                parts.append("")
            else:
                parts.append(f"- **{key}**: {value}")

        # Extracted data.
        extracted = inv.get("extracted", {})
        if extracted:
            parts.append("### Extracted Alert Data")
            for k, v in extracted.items():
                parts.append(f"- {k}: {v}")
            parts.append("")

        return parts

    # ------------------------------------------------------------------
    # Table rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _dict_list_to_table(rows: list[dict], max_rows: int = _MAX_TABLE_ROWS) -> str:
        """Convert a list of dicts to a markdown table."""
        if not rows:
            return "_No data._"

        # Collect column headers from all rows.
        headers: list[str] = []
        for row in rows[:max_rows]:
            for key in row:
                if key not in headers:
                    headers.append(key)

        if not headers:
            return "_No data._"

        def _cell(val) -> str:
            s = str(val) if val is not None else ""
            return s[:_MAX_CELL_LEN] if len(s) > _MAX_CELL_LEN else s

        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        for row in rows[:max_rows]:
            cells = [_cell(row.get(h, "")) for h in headers]
            lines.append("| " + " | ".join(cells) + " |")

        if len(rows) > max_rows:
            lines.append(f"_... and {len(rows) - max_rows} more rows._")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_file(self, path: Path) -> Optional[InvestigationRecord]:
        """Parse a .md investigation file into an InvestigationRecord."""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None

        fm = self._parse_frontmatter(text)
        if not fm:
            return None

        # Build a short investigation summary from the first few lines of the
        # "## Investigation Findings" section.
        inv_section = self._extract_section(text, "## Investigation Findings")
        summary = inv_section[:500] if inv_section else ""

        # Extract selected option info from "## Decision" section.
        decision_section = self._extract_section(text, "## Decision")
        selected_title = ""
        selected_reasoning = ""
        for line in decision_section.split("\n"):
            if line.startswith("**Selected**:"):
                selected_title = line.split(":", 1)[1].strip()
            elif line.startswith("**Reasoning**:"):
                selected_reasoning = line.split(":", 1)[1].strip()

        # Extract outcome.
        outcome_section = self._extract_section(text, "## Outcome")
        outcome = ""
        for line in outcome_section.split("\n"):
            if line.startswith("**Status**:"):
                outcome = line.split(":", 1)[1].strip()

        return InvestigationRecord(
            file_path=str(path),
            timestamp=fm.get("timestamp", ""),
            workflow_id=fm.get("workflow_id", ""),
            database_id=fm.get("database_id", ""),
            alert_type=fm.get("alert_type", ""),
            environment=fm.get("environment", ""),
            agent_name=fm.get("agent", ""),
            confidence=float(fm.get("confidence", 0)),
            focus_area=fm.get("focus_area", ""),
            status=fm.get("status", ""),
            investigation_summary=summary,
            selected_option_title=selected_title,
            selected_option_reasoning=selected_reasoning,
            candidates_count=0,
            outcome=outcome,
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> dict:
        """Parse simple YAML frontmatter (key: value pairs only)."""
        if not text.startswith("---"):
            return {}
        end = text.find("---", 3)
        if end < 0:
            return {}
        fm_text = text[3:end].strip()
        result: dict[str, str] = {}
        for line in fm_text.split("\n"):
            line = line.strip()
            if ":" in line:
                key, val = line.split(":", 1)
                result[key.strip()] = val.strip()
        return result

    @staticmethod
    def _extract_section(text: str, heading: str) -> str:
        """Extract content between *heading* and the next same-level heading."""
        idx = text.find(heading)
        if idx < 0:
            return ""
        start = idx + len(heading)
        # Find the next heading at the same level (## ...).
        level = heading.split()[0]  # "##"
        next_heading = text.find(f"\n{level} ", start)
        if next_heading < 0:
            return text[start:].strip()
        return text[start:next_heading].strip()
