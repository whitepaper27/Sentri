"""MemoryManager — database-scoped, configurable memory (short-term + long-term).

Queries audit_log and workflows to build context for the LLM researcher.
Short-term: recent actions, outcomes, failed approaches (24h window).
Long-term: historical alert patterns, failure stats (90-day window).
Configured via brain/memory_rules.md.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from .queries import (
    ALERT_HISTORY_SQL,
    FAILED_APPROACHES_SQL,
    FAILURE_STATS_SQL,
    RECENT_ACTIONS_SQL,
    RECENT_OUTCOMES_SQL,
)

if TYPE_CHECKING:
    from sentri.db.connection import Database
    from sentri.memory.investigation_store import InvestigationStore
    from sentri.policy.loader import PolicyLoader

logger = logging.getLogger("sentri.memory")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MemoryConfig:
    """Configurable memory parameters — loaded from brain/memory_rules.md."""

    short_term_hours: int = 24
    max_recent_actions: int = 10
    max_recent_outcomes: int = 10
    repeat_block_hours: int = 6
    failed_approach_days: int = 30

    # v3.3 Long-term memory
    long_term_days: int = 90
    max_history_per_alert: int = 10

    # Investigation memory (agent analysis .md files)
    max_investigation_files: int = 5
    investigation_retention_days: int = 90

    # Per-environment overrides (environment -> hours)
    env_lookback: dict[str, int] = field(
        default_factory=lambda: {
            "PROD": 48,
            "UAT": 24,
            "DEV": 12,
        }
    )

    def get_lookback_hours(self, environment: str = "") -> int:
        """Get lookback window for an environment, falling back to default."""
        if environment:
            return self.env_lookback.get(environment.upper(), self.short_term_hours)
        return self.short_term_hours


@dataclass
class ActionSummary:
    """One executed action from audit_log."""

    timestamp: str
    hours_ago: float
    action_type: str
    action_sql: str
    result: str
    error_message: Optional[str] = None


@dataclass
class OutcomeSummary:
    """One workflow outcome from workflows table."""

    timestamp: str
    hours_ago: float
    alert_type: str
    status: str
    confidence: float = 0.0
    source: str = "unknown"


@dataclass
class FailedApproach:
    """A previously failed action — LLM should avoid repeating."""

    timestamp: str
    action_type: str
    action_sql: str
    error_message: str


# v3.3 Long-term memory dataclasses

DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


@dataclass
class AlertHistory:
    """One historical alert event (long-term, 90-day window)."""

    alert_type: str
    timestamp: str
    day_of_week: int  # 0=Sunday, 6=Saturday
    day_name: str  # "Monday", "Friday", etc.
    status: str


@dataclass
class FailureStats:
    """Failure statistics for one action_type (long-term)."""

    action_type: str
    total: int
    failures: int
    successes: int
    failure_rate: float
    common_errors: list[str] = field(default_factory=list)


@dataclass
class MemoryContext:
    """Full memory context for one database + alert type.

    This is the structured output that gets formatted into
    human-readable text for the LLM prompt.
    """

    database_id: str
    alert_type: str
    config: MemoryConfig
    recent_actions: list[ActionSummary] = field(default_factory=list)
    recent_outcomes: list[OutcomeSummary] = field(default_factory=list)
    failed_approaches: list[FailedApproach] = field(default_factory=list)
    # v3.3 Long-term memory
    alert_history: list[AlertHistory] = field(default_factory=list)
    failure_stats: list[FailureStats] = field(default_factory=list)
    # Investigation memory (past agent analysis)
    past_investigations: str = ""

    @property
    def has_memory(self) -> bool:
        """True if there's any memory to inject into the prompt."""
        return bool(
            self.recent_actions
            or self.recent_outcomes
            or self.failed_approaches
            or self.alert_history
            or self.failure_stats
            or self.past_investigations
        )


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------


class MemoryManager:
    """Database-scoped memory for the LLM researcher (short-term + long-term).

    Queries existing audit_log and workflows tables — no new tables needed.
    Short-term: recent actions/outcomes/failures (24h).
    Long-term: historical alert patterns, failure stats (90 days).
    Configured via brain/memory_rules.md.
    """

    def __init__(
        self,
        db: "Database",
        policy_loader: "PolicyLoader",
        investigation_store: Optional["InvestigationStore"] = None,
    ):
        self._db = db
        self._policy_loader = policy_loader
        self._investigation_store = investigation_store
        self._config: Optional[MemoryConfig] = None

    def get_context(
        self,
        database_id: str,
        alert_type: str,
        environment: str = "",
    ) -> MemoryContext:
        """Build memory context for a specific database + alert type.

        Args:
            database_id: Target database (scoping key)
            alert_type: Current alert type
            environment: Optional environment for lookback override
        """
        config = self._load_config()
        lookback = config.get_lookback_hours(environment)

        actions = self._get_recent_actions(database_id, lookback, config.max_recent_actions)
        outcomes = self._get_recent_outcomes(
            database_id, alert_type, lookback, config.max_recent_outcomes
        )
        failures = self._get_failed_approaches(database_id, config.failed_approach_days)

        # v3.3: Long-term memory (90-day window)
        history = self._get_alert_history(
            database_id,
            config.long_term_days,
            config.max_history_per_alert,
        )
        failure_stats = self._get_failure_stats(database_id, config.long_term_days)

        # Load past investigation analyses (.md files)
        investigations_text = ""
        inv_count = 0
        if self._investigation_store:
            try:
                records = self._investigation_store.load_recent(
                    database_id,
                    max_files=config.max_investigation_files,
                    max_age_days=config.investigation_retention_days,
                )
                inv_count = len(records)
                if records:
                    investigations_text = self._investigation_store.format_for_prompt(records)
            except Exception as e:
                logger.warning("Failed to load past investigations: %s", e)

        ctx = MemoryContext(
            database_id=database_id,
            alert_type=alert_type,
            config=config,
            recent_actions=actions,
            recent_outcomes=outcomes,
            failed_approaches=failures,
            alert_history=history,
            failure_stats=failure_stats,
            past_investigations=investigations_text,
        )

        logger.info(
            "Memory context for %s/%s: %d actions, %d outcomes, %d failures, "
            "%d history, %d failure_stats, %d investigations (short=%dh, long=%dd)",
            database_id,
            alert_type,
            len(actions),
            len(outcomes),
            len(failures),
            len(history),
            len(failure_stats),
            inv_count,
            lookback,
            config.long_term_days,
        )
        return ctx

    def format_for_prompt(self, ctx: MemoryContext, max_chars: int = 4000) -> str:
        """Format memory context as human-readable text for LLM prompt injection.

        Returns empty string if no memory exists (skip the section entirely).
        Truncates to *max_chars* (~1 000 tokens) to prevent prompt overflow.
        """
        if not ctx.has_memory:
            return ""

        parts: list[str] = []

        # Recent actions
        if ctx.recent_actions:
            parts.append(
                f"## Recent Actions on {ctx.database_id} (Last {ctx.config.get_lookback_hours()}h)"
            )
            for a in ctx.recent_actions:
                sql_preview = (
                    a.action_sql[:150] + "..." if len(a.action_sql) > 150 else a.action_sql
                )
                line = f'- {a.hours_ago:.1f}h ago: {a.action_type} — "{sql_preview}" → {a.result}'
                if a.error_message and a.result == "FAILED":
                    line += f" ({a.error_message[:100]})"
                parts.append(line)
            parts.append("")

        # Recent outcomes for this alert type
        if ctx.recent_outcomes:
            parts.append(f"## Recent {ctx.alert_type} Outcomes on {ctx.database_id}")
            for o in ctx.recent_outcomes:
                line = f"- {o.hours_ago:.1f}h ago: {o.alert_type} → {o.status} (confidence={o.confidence:.2f}, source={o.source})"
                parts.append(line)
            parts.append("")

        # Failed approaches
        if ctx.failed_approaches:
            parts.append("## Failed Approaches (Avoid These)")
            for f in ctx.failed_approaches:
                sql_preview = (
                    f.action_sql[:150] + "..." if len(f.action_sql) > 150 else f.action_sql
                )
                parts.append(
                    f'- {f.action_type}: "{sql_preview}" → FAILED: {f.error_message[:150]}'
                )
            parts.append("")

        # v3.3: Historical alert patterns (long-term)
        if ctx.alert_history:
            parts.append(
                f"## Historical Alert Patterns for {ctx.database_id} "
                f"(Last {ctx.config.long_term_days} Days)"
            )
            # Group by alert_type, cap per alert
            by_type: dict[str, list[AlertHistory]] = {}
            for h in ctx.alert_history:
                by_type.setdefault(h.alert_type, []).append(h)
            for at, events in by_type.items():
                capped = events[: ctx.config.max_history_per_alert]
                parts.append(f"\n### {at} ({len(events)} events)")
                for e in capped:
                    parts.append(f"- {e.timestamp[:10]} ({e.day_name[:3]}) -> {e.status}")
            parts.append("")

        # v3.3: Historical failure stats
        if ctx.failure_stats:
            parts.append(f"## Historical Failure Stats for {ctx.database_id}")
            for fs in ctx.failure_stats:
                errors_preview = fs.common_errors[0][:80] if fs.common_errors else ""
                line = f"- {fs.action_type}: {fs.failures} failed / {fs.total} total"
                if errors_preview:
                    line += f" ({errors_preview})"
                parts.append(line)
            parts.append("")

        # Past investigation analyses (agent findings from .md files)
        if ctx.past_investigations:
            parts.append(ctx.past_investigations)
            parts.append("")

        # Memory rules for LLM
        parts.append("## Memory Rules")
        parts.append("- Do NOT repeat an action done less than 6 hours ago on the same target")
        parts.append(
            "- If the same alert fired again within 24 hours after a successful fix, suggest a LARGER action or escalate"
        )
        parts.append(
            "- If a previous action FAILED, do NOT suggest the same approach — try an alternative"
        )
        parts.append("- Reference specific past actions in your reasoning")
        parts.append(
            "- If 3+ actions on the same database in 24 hours, recommend root cause investigation"
        )

        result = "\n".join(parts)
        if len(result) > max_chars:
            logger.info(
                "Memory context truncated: %d -> %d chars", len(result), max_chars
            )
            result = result[:max_chars] + "\n\n[... memory context truncated]"
        return result

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _load_config(self) -> MemoryConfig:
        """Load memory config from brain/memory_rules.md (cached after first load)."""
        if self._config is not None:
            return self._config

        policy = self._policy_loader.load_brain("memory_rules")
        config = MemoryConfig()

        if not policy:
            self._config = config
            return config

        # Parse Short-Term Memory section → Lookback Windows table
        stm = policy.get("shortterm_memory", {})
        if not stm:
            # Try alternative key patterns
            for key in policy:
                if "short" in key and "memory" in key:
                    stm = policy[key]
                    break

        if isinstance(stm, dict):
            text = stm.get("text", "")
            if text:
                config = self._parse_lookback_table(text, config)

        # Parse Per-Environment Overrides table
        env_section = policy.get("perenvironment_overrides", {})
        if not env_section:
            for key in policy:
                if "environment" in key and "override" in key:
                    env_section = policy[key]
                    break

        if isinstance(env_section, dict):
            text = env_section.get("text", "")
            if text:
                config = self._parse_env_overrides(text, config)

        # v3.3: Parse Long-Term Memory section
        ltm = policy.get("longterm_memory", {})
        if not ltm:
            for key in policy:
                if "long" in key and "memory" in key:
                    ltm = policy[key]
                    break

        if isinstance(ltm, dict):
            text = ltm.get("text", "")
            if text:
                config = self._parse_ltm_table(text, config)

        self._config = config
        logger.info(
            "Memory config: short=%dh, long=%dd, max_actions=%d, failed_days=%d, env=%s",
            config.short_term_hours,
            config.long_term_days,
            config.max_recent_actions,
            config.failed_approach_days,
            config.env_lookback,
        )
        return config

    def reload(self) -> None:
        """Force reload of memory config from disk."""
        self._config = None
        self._policy_loader.reload()

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def _get_recent_actions(
        self, database_id: str, lookback_hours: int, max_items: int
    ) -> list[ActionSummary]:
        """Query audit_log for recent actions on this database."""
        rows = self._db.execute_read(
            RECENT_ACTIONS_SQL,
            (database_id, f"-{lookback_hours}", max_items),
        )

        actions = []
        now = datetime.now(timezone.utc)
        for row in rows:
            hours_ago = self._hours_since(row["timestamp"], now)
            actions.append(
                ActionSummary(
                    timestamp=row["timestamp"],
                    hours_ago=hours_ago,
                    action_type=row["action_type"],
                    action_sql=row["action_sql"] or "",
                    result=row["result"],
                    error_message=row["error_message"],
                )
            )
        return actions

    def _get_recent_outcomes(
        self, database_id: str, alert_type: str, lookback_hours: int, max_items: int
    ) -> list[OutcomeSummary]:
        """Query workflows for recent outcomes on this database + alert type."""
        rows = self._db.execute_read(
            RECENT_OUTCOMES_SQL,
            (database_id, alert_type, f"-{lookback_hours}", max_items),
        )

        outcomes = []
        now = datetime.now(timezone.utc)
        for row in rows:
            hours_ago = self._hours_since(row["created_at"], now)

            # Extract confidence from verification JSON
            confidence = 0.0
            if row["verification"]:
                try:
                    vdata = json.loads(row["verification"])
                    confidence = float(vdata.get("confidence", 0.0))
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # Extract source from metadata JSON
            source = "unknown"
            if row["metadata"]:
                try:
                    mdata = json.loads(row["metadata"])
                    source = mdata.get("source", "unknown")
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            outcomes.append(
                OutcomeSummary(
                    timestamp=row["created_at"],
                    hours_ago=hours_ago,
                    alert_type=row["alert_type"],
                    status=row["status"],
                    confidence=confidence,
                    source=source,
                )
            )
        return outcomes

    def _get_failed_approaches(self, database_id: str, lookback_days: int) -> list[FailedApproach]:
        """Query audit_log for failed actions — LLM should avoid these."""
        rows = self._db.execute_read(
            FAILED_APPROACHES_SQL,
            (database_id, f"-{lookback_days}"),
        )

        failures = []
        for row in rows:
            failures.append(
                FailedApproach(
                    timestamp=row["timestamp"],
                    action_type=row["action_type"],
                    action_sql=row["action_sql"] or "",
                    error_message=row["error_message"] or "Unknown error",
                )
            )
        return failures

    # ------------------------------------------------------------------
    # v3.3: Long-term query methods
    # ------------------------------------------------------------------

    def _get_alert_history(
        self, database_id: str, lookback_days: int, max_per_alert: int
    ) -> list[AlertHistory]:
        """Query workflows for historical alert events (90-day window)."""
        rows = self._db.execute_read(
            ALERT_HISTORY_SQL,
            (database_id, f"-{lookback_days}"),
        )

        history = []
        for row in rows:
            dow = row["day_of_week"] or 0
            history.append(
                AlertHistory(
                    alert_type=row["alert_type"],
                    timestamp=row["created_at"],
                    day_of_week=dow,
                    day_name=DAY_NAMES[dow] if 0 <= dow <= 6 else "Unknown",
                    status=row["status"],
                )
            )
        return history

    def _get_failure_stats(self, database_id: str, lookback_days: int) -> list[FailureStats]:
        """Query audit_log for historical failure statistics."""
        rows = self._db.execute_read(
            FAILURE_STATS_SQL,
            (database_id, f"-{lookback_days}"),
        )

        stats = []
        for row in rows:
            total = row["total"] or 0
            failures = row["failures"] or 0
            successes = row["successes"] or 0

            # Parse concatenated error messages
            common_errors: list[str] = []
            raw_errors = row["error_messages"] or ""
            if raw_errors:
                # Deduplicate and take top 3
                seen: set[str] = set()
                for err in raw_errors.split(" | "):
                    err = err.strip()
                    if err and err not in seen:
                        seen.add(err)
                        common_errors.append(err)
                    if len(common_errors) >= 3:
                        break

            stats.append(
                FailureStats(
                    action_type=row["action_type"],
                    total=total,
                    failures=failures,
                    successes=successes,
                    failure_rate=round(failures / max(total, 1), 2),
                    common_errors=common_errors,
                )
            )
        return stats

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_lookback_table(text: str, config: MemoryConfig) -> MemoryConfig:
        """Parse the Lookback Windows table from memory_rules.md."""
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 3:
                continue

            context_type = cells[0].lower()
            if context_type in ("context type",):
                continue

            # Parse window value (e.g. "24 hours", "30 days")
            window_str = cells[1].lower()
            max_items_str = cells[2]

            hours_match = re.search(r"(\d+)\s*hours?", window_str)
            days_match = re.search(r"(\d+)\s*days?", window_str)
            items_match = re.search(r"(\d+)", max_items_str)

            if "recent action" in context_type:
                if hours_match:
                    config.short_term_hours = int(hours_match.group(1))
                if items_match:
                    config.max_recent_actions = int(items_match.group(1))
            elif "recent outcome" in context_type:
                if hours_match:
                    pass  # Uses same short_term_hours
                if items_match:
                    config.max_recent_outcomes = int(items_match.group(1))
            elif "failed" in context_type:
                if days_match:
                    config.failed_approach_days = int(days_match.group(1))

        return config

    @staticmethod
    def _parse_env_overrides(text: str, config: MemoryConfig) -> MemoryConfig:
        """Parse the Per-Environment Overrides table."""
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 2:
                continue

            env = cells[0].upper()
            if env in ("ENVIRONMENT",):
                continue

            lookback_str = cells[1].lower()
            hours_match = re.search(r"(\d+)\s*hours?", lookback_str)
            if hours_match:
                config.env_lookback[env] = int(hours_match.group(1))

        return config

    @staticmethod
    def _parse_ltm_table(text: str, config: MemoryConfig) -> MemoryConfig:
        """Parse the Long-Term Memory History Settings table."""
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 2:
                continue

            setting = cells[0].lower()
            value_str = cells[1].lower()

            if "lookback" in setting or "detection window" in setting:
                days_match = re.search(r"(\d+)\s*days?", value_str)
                if days_match:
                    config.long_term_days = int(days_match.group(1))
            elif "max events" in setting:
                num_match = re.search(r"(\d+)", value_str)
                if num_match:
                    config.max_history_per_alert = int(num_match.group(1))

        return config

    @staticmethod
    def _hours_since(timestamp_str: str, now: datetime) -> float:
        """Calculate hours between a timestamp string and now."""
        try:
            ts = datetime.fromisoformat(timestamp_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = now - ts
            return max(0.0, delta.total_seconds() / 3600)
        except (ValueError, TypeError):
            return 999.0
