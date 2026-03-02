"""Tests for the memory system (short-term + long-term)."""

from datetime import datetime, timedelta, timezone

from sentri.core.models import AuditRecord, Workflow
from sentri.memory.manager import (
    AlertHistory,
    FailureStats,
    MemoryConfig,
    MemoryContext,
    MemoryManager,
)

# ---------------------------------------------------------------------------
# MemoryConfig tests
# ---------------------------------------------------------------------------


def test_memory_config_defaults():
    config = MemoryConfig()
    assert config.short_term_hours == 24
    assert config.max_recent_actions == 10
    assert config.max_recent_outcomes == 10
    assert config.repeat_block_hours == 6
    assert config.failed_approach_days == 30


def test_memory_config_env_lookback():
    config = MemoryConfig()
    assert config.get_lookback_hours("PROD") == 48
    assert config.get_lookback_hours("UAT") == 24
    assert config.get_lookback_hours("DEV") == 12
    assert config.get_lookback_hours("UNKNOWN") == 24  # falls back to default
    assert config.get_lookback_hours("") == 24  # empty = default


# ---------------------------------------------------------------------------
# MemoryContext tests
# ---------------------------------------------------------------------------


def test_memory_context_has_memory_empty():
    ctx = MemoryContext(database_id="DB-01", alert_type="test", config=MemoryConfig())
    assert ctx.has_memory is False


def test_memory_context_has_memory_with_actions():
    from sentri.memory.manager import ActionSummary

    ctx = MemoryContext(
        database_id="DB-01",
        alert_type="test",
        config=MemoryConfig(),
        recent_actions=[
            ActionSummary(
                timestamp="2026-01-01T00:00:00",
                hours_ago=2.0,
                action_type="ADD_DATAFILE",
                action_sql="ALTER TABLESPACE...",
                result="SUCCESS",
            )
        ],
    )
    assert ctx.has_memory is True


# ---------------------------------------------------------------------------
# MemoryManager — empty memory
# ---------------------------------------------------------------------------


def test_empty_memory(tmp_db, policy_loader):
    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")
    assert ctx.database_id == "DB-01"
    assert ctx.alert_type == "tablespace_full"
    assert ctx.recent_actions == []
    assert ctx.recent_outcomes == []
    assert ctx.failed_approaches == []
    assert ctx.has_memory is False


def test_format_empty_memory(tmp_db, policy_loader):
    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")
    text = mm.format_for_prompt(ctx)
    assert text == ""


# ---------------------------------------------------------------------------
# MemoryManager — recent actions (database-scoped)
# ---------------------------------------------------------------------------


def test_recent_actions(tmp_db, audit_repo, workflow_repo, policy_loader):
    """Insert audit records → verify they appear in memory context."""
    wf = Workflow(alert_type="tablespace_full", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="ADD_DATAFILE",
            action_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            database_id="DB-01",
            environment="DEV",
            executed_by="agent4",
            result="SUCCESS",
        )
    )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")
    assert len(ctx.recent_actions) == 1
    assert ctx.recent_actions[0].action_type == "ADD_DATAFILE"
    assert ctx.recent_actions[0].result == "SUCCESS"
    assert "ALTER TABLESPACE" in ctx.recent_actions[0].action_sql


def test_database_scoping(tmp_db, audit_repo, workflow_repo, policy_loader):
    """Actions on DB-A must NOT appear in DB-B context."""
    wf_a = Workflow(alert_type="test", database_id="DB-A", environment="DEV")
    wf_b = Workflow(alert_type="test", database_id="DB-B", environment="DEV")
    workflow_repo.create(wf_a)
    workflow_repo.create(wf_b)

    # Create action for DB-A
    audit_repo.create(
        AuditRecord(
            workflow_id=wf_a.id,
            action_type="ADD_DATAFILE",
            action_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            database_id="DB-A",
            environment="DEV",
            executed_by="agent4",
            result="SUCCESS",
        )
    )

    # Create action for DB-B
    audit_repo.create(
        AuditRecord(
            workflow_id=wf_b.id,
            action_type="KILL_SESSION",
            action_sql="ALTER SYSTEM KILL SESSION '847,12345'",
            database_id="DB-B",
            environment="DEV",
            executed_by="agent4",
            result="SUCCESS",
        )
    )

    mm = MemoryManager(tmp_db, policy_loader)

    # DB-A context should only see ADD_DATAFILE
    ctx_a = mm.get_context("DB-A", "test")
    assert len(ctx_a.recent_actions) == 1
    assert ctx_a.recent_actions[0].action_type == "ADD_DATAFILE"

    # DB-B context should only see KILL_SESSION
    ctx_b = mm.get_context("DB-B", "test")
    assert len(ctx_b.recent_actions) == 1
    assert ctx_b.recent_actions[0].action_type == "KILL_SESSION"


# ---------------------------------------------------------------------------
# MemoryManager — recent outcomes
# ---------------------------------------------------------------------------


def test_recent_outcomes(tmp_db, workflow_repo, policy_loader):
    """Completed/failed workflows appear in outcomes."""
    wf = Workflow(
        alert_type="tablespace_full",
        database_id="DB-01",
        environment="DEV",
        status="COMPLETED",
        verification='{"confidence": 0.95}',
        metadata='{"source": "llm_agentic"}',
    )
    workflow_repo.create(wf)

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")
    assert len(ctx.recent_outcomes) == 1
    assert ctx.recent_outcomes[0].status == "COMPLETED"
    assert ctx.recent_outcomes[0].confidence == 0.95
    assert ctx.recent_outcomes[0].source == "llm_agentic"


def test_outcomes_scoped_by_alert_type(tmp_db, workflow_repo, policy_loader):
    """Outcomes for different alert types should not mix."""
    wf1 = Workflow(
        alert_type="tablespace_full",
        database_id="DB-01",
        environment="DEV",
        status="COMPLETED",
    )
    wf2 = Workflow(
        alert_type="cpu_high",
        database_id="DB-01",
        environment="DEV",
        status="COMPLETED",
    )
    workflow_repo.create(wf1)
    workflow_repo.create(wf2)

    mm = MemoryManager(tmp_db, policy_loader)

    ctx_ts = mm.get_context("DB-01", "tablespace_full")
    assert len(ctx_ts.recent_outcomes) == 1
    assert ctx_ts.recent_outcomes[0].alert_type == "tablespace_full"

    ctx_cpu = mm.get_context("DB-01", "cpu_high")
    assert len(ctx_cpu.recent_outcomes) == 1
    assert ctx_cpu.recent_outcomes[0].alert_type == "cpu_high"


# ---------------------------------------------------------------------------
# MemoryManager — failed approaches
# ---------------------------------------------------------------------------


def test_failed_approaches(tmp_db, audit_repo, workflow_repo, policy_loader):
    """FAILED audit records appear in failed_approaches."""
    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="ADD_DATAFILE",
            action_sql="ALTER TABLESPACE USERS ADD DATAFILE '/bad/path'",
            database_id="DB-01",
            environment="DEV",
            executed_by="agent4",
            result="FAILED",
            error_message="ORA-01119: error in creating database file",
        )
    )

    # Also create a SUCCESS record — should NOT be in failures
    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="ADD_DATAFILE",
            action_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            database_id="DB-01",
            environment="DEV",
            executed_by="agent4",
            result="SUCCESS",
        )
    )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "test")
    assert len(ctx.failed_approaches) == 1
    assert ctx.failed_approaches[0].action_type == "ADD_DATAFILE"
    assert "ORA-01119" in ctx.failed_approaches[0].error_message


# ---------------------------------------------------------------------------
# MemoryManager — format_for_prompt
# ---------------------------------------------------------------------------


def test_format_for_prompt_with_actions(tmp_db, audit_repo, workflow_repo, policy_loader):
    """Formatted prompt text includes action details and memory rules."""
    wf = Workflow(alert_type="tablespace_full", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="ADD_DATAFILE",
            action_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            database_id="DB-01",
            environment="DEV",
            executed_by="agent4",
            result="SUCCESS",
        )
    )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")
    text = mm.format_for_prompt(ctx)

    assert "Recent Actions on DB-01" in text
    assert "ADD_DATAFILE" in text
    assert "SUCCESS" in text
    assert "Memory Rules" in text
    assert "Do NOT repeat" in text


def test_format_for_prompt_with_failures(tmp_db, audit_repo, workflow_repo, policy_loader):
    """Formatted prompt includes failed approaches section."""
    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="ADD_DATAFILE",
            action_sql="ALTER TABLESPACE BAD ADD DATAFILE",
            database_id="DB-01",
            environment="DEV",
            executed_by="agent4",
            result="FAILED",
            error_message="ORA-01119: error in creating database file",
        )
    )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "test")
    text = mm.format_for_prompt(ctx)

    assert "Failed Approaches" in text
    assert "ORA-01119" in text


# ---------------------------------------------------------------------------
# MemoryManager — config loading from .md
# ---------------------------------------------------------------------------


def test_config_from_md(tmp_db, policy_loader):
    """MemoryConfig should be parsed from brain/memory_rules.md."""
    mm = MemoryManager(tmp_db, policy_loader)
    config = mm._load_config()
    # Values from memory_rules.md Short-Term Memory section
    assert config.short_term_hours == 24
    assert config.max_recent_actions == 10
    assert config.failed_approach_days == 30
    # Per-environment overrides
    assert config.env_lookback.get("PROD") == 48
    assert config.env_lookback.get("DEV") == 12


def test_config_reload(tmp_db, policy_loader):
    """Reload should clear cached config."""
    mm = MemoryManager(tmp_db, policy_loader)
    config1 = mm._load_config()
    assert mm._config is not None

    mm.reload()
    assert mm._config is None

    config2 = mm._load_config()
    assert config2.short_term_hours == config1.short_term_hours


# ---------------------------------------------------------------------------
# MemoryManager — environment-specific lookback
# ---------------------------------------------------------------------------


def test_env_lookback_prod(tmp_db, audit_repo, workflow_repo, policy_loader):
    """PROD environment should use 48h lookback."""
    wf = Workflow(alert_type="tablespace_full", database_id="DB-01", environment="PROD")
    workflow_repo.create(wf)

    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="ADD_DATAFILE",
            action_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            database_id="DB-01",
            environment="PROD",
            executed_by="agent4",
            result="SUCCESS",
        )
    )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full", environment="PROD")
    # Should still find the action (within 48h window)
    assert len(ctx.recent_actions) == 1


# ---------------------------------------------------------------------------
# MemoryManager — multiple records, ordering
# ---------------------------------------------------------------------------


def test_multiple_actions_all_returned(tmp_db, audit_repo, workflow_repo, policy_loader):
    """Multiple actions for the same database should all be returned."""
    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    for action in ["ADD_DATAFILE", "KILL_SESSION", "DELETE_ARCHIVES"]:
        audit_repo.create(
            AuditRecord(
                workflow_id=wf.id,
                action_type=action,
                action_sql=f"SQL for {action}",
                database_id="DB-01",
                environment="DEV",
                executed_by="agent4",
                result="SUCCESS",
            )
        )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "test")
    assert len(ctx.recent_actions) == 3
    action_types = {a.action_type for a in ctx.recent_actions}
    assert action_types == {"ADD_DATAFILE", "KILL_SESSION", "DELETE_ARCHIVES"}


# ---------------------------------------------------------------------------
# v3.3: Long-Term Memory — alert history
# ---------------------------------------------------------------------------


def test_alert_history_query(tmp_db, workflow_repo, policy_loader):
    """Completed workflows appear in long-term alert_history with day names."""
    # Create 5 workflows over the past 60 days
    base = datetime.now(timezone.utc)
    for i in range(5):
        wf = Workflow(
            alert_type="tablespace_full",
            database_id="DB-01",
            environment="DEV",
            status="COMPLETED",
        )
        wf_id = workflow_repo.create(wf)
        created = (base - timedelta(days=i * 14)).isoformat()
        tmp_db.execute_write(
            "UPDATE workflows SET created_at = ? WHERE id = ?",
            (created, wf_id),
        )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")

    assert len(ctx.alert_history) == 5
    # Each should have a valid day name
    for h in ctx.alert_history:
        assert h.alert_type == "tablespace_full"
        assert h.status == "COMPLETED"
        assert h.day_name in [
            "Sunday",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
        ]


def test_alert_history_scoped_by_database(tmp_db, workflow_repo, policy_loader):
    """DB-A history must NOT appear in DB-B context."""
    for db_id in ["DB-A", "DB-B"]:
        wf = Workflow(
            alert_type="tablespace_full",
            database_id=db_id,
            environment="DEV",
            status="COMPLETED",
        )
        workflow_repo.create(wf)

    mm = MemoryManager(tmp_db, policy_loader)

    ctx_a = mm.get_context("DB-A", "tablespace_full")
    ctx_b = mm.get_context("DB-B", "tablespace_full")

    # Each should only see their own history
    for h in ctx_a.alert_history:
        assert h.alert_type == "tablespace_full"
    for h in ctx_b.alert_history:
        assert h.alert_type == "tablespace_full"

    # Both should have exactly 1
    assert len(ctx_a.alert_history) == 1
    assert len(ctx_b.alert_history) == 1


def test_alert_history_respects_lookback(tmp_db, workflow_repo, policy_loader):
    """Workflows older than long_term_days should be excluded."""
    base = datetime.now(timezone.utc)
    wf = Workflow(
        alert_type="tablespace_full",
        database_id="DB-01",
        environment="DEV",
        status="COMPLETED",
    )
    wf_id = workflow_repo.create(wf)
    # Backdate to 120 days ago (beyond default 90-day lookback)
    old_date = (base - timedelta(days=120)).isoformat()
    tmp_db.execute_write(
        "UPDATE workflows SET created_at = ? WHERE id = ?",
        (old_date, wf_id),
    )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")
    assert len(ctx.alert_history) == 0


def test_alert_history_includes_all_alert_types(tmp_db, workflow_repo, policy_loader):
    """Alert history should include ALL alert types for the database."""
    for alert in ["tablespace_full", "archive_dest_full", "cpu_high"]:
        wf = Workflow(
            alert_type=alert,
            database_id="DB-01",
            environment="DEV",
            status="COMPLETED",
        )
        workflow_repo.create(wf)

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")

    alert_types = {h.alert_type for h in ctx.alert_history}
    assert alert_types == {"tablespace_full", "archive_dest_full", "cpu_high"}


# ---------------------------------------------------------------------------
# v3.3: Long-Term Memory — failure stats
# ---------------------------------------------------------------------------


def test_failure_stats_query(tmp_db, audit_repo, workflow_repo, policy_loader):
    """Mixed SUCCESS/FAILED audit records produce correct failure stats."""
    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    # 3 SUCCESS, 2 FAILED for RESIZE_DATAFILE
    for i in range(3):
        audit_repo.create(
            AuditRecord(
                workflow_id=wf.id,
                action_type="RESIZE_DATAFILE",
                action_sql="ALTER DATABASE DATAFILE ... RESIZE 50G",
                database_id="DB-01",
                environment="DEV",
                executed_by="agent4",
                result="SUCCESS",
            )
        )
    for i in range(2):
        audit_repo.create(
            AuditRecord(
                workflow_id=wf.id,
                action_type="RESIZE_DATAFILE",
                action_sql="ALTER DATABASE DATAFILE ... RESIZE 50G",
                database_id="DB-01",
                environment="DEV",
                executed_by="agent4",
                result="FAILED",
                error_message="ORA-01119: bad file path",
            )
        )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "test")

    assert len(ctx.failure_stats) >= 1
    stat = ctx.failure_stats[0]
    assert stat.action_type == "RESIZE_DATAFILE"
    assert stat.total == 5
    assert stat.failures == 2
    assert stat.successes == 3
    assert stat.failure_rate == 0.4
    assert any("ORA-01119" in e for e in stat.common_errors)


def test_failure_stats_min_threshold(tmp_db, audit_repo, workflow_repo, policy_loader):
    """Action with only 1 attempt should NOT appear in failure stats."""
    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="SINGLE_ACTION",
            action_sql="ALTER TABLESPACE USERS RESIZE 10G",
            database_id="DB-01",
            environment="DEV",
            executed_by="agent4",
            result="FAILED",
            error_message="Some error",
        )
    )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "test")

    # HAVING total >= 2 should filter this out
    action_types = [s.action_type for s in ctx.failure_stats]
    assert "SINGLE_ACTION" not in action_types


# ---------------------------------------------------------------------------
# v3.3: Long-Term Memory — format_for_prompt
# ---------------------------------------------------------------------------


def test_format_includes_history_section(tmp_db, workflow_repo, policy_loader):
    """format_for_prompt includes Historical Alert Patterns when history exists."""
    wf = Workflow(
        alert_type="tablespace_full",
        database_id="DB-01",
        environment="DEV",
        status="COMPLETED",
    )
    workflow_repo.create(wf)

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")
    text = mm.format_for_prompt(ctx)

    assert "Historical Alert Patterns for DB-01" in text
    assert "tablespace_full" in text


def test_format_includes_day_names(tmp_db, workflow_repo, policy_loader):
    """Formatted history output includes day-of-week abbreviations."""
    wf = Workflow(
        alert_type="tablespace_full",
        database_id="DB-01",
        environment="DEV",
        status="COMPLETED",
    )
    workflow_repo.create(wf)

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "tablespace_full")
    text = mm.format_for_prompt(ctx)

    # Should contain a 3-letter day abbreviation
    day_abbrs = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    assert any(abbr in text for abbr in day_abbrs)


def test_format_includes_failure_stats(tmp_db, audit_repo, workflow_repo, policy_loader):
    """Formatted prompt includes Historical Failure Stats section."""
    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    for _ in range(2):
        audit_repo.create(
            AuditRecord(
                workflow_id=wf.id,
                action_type="ADD_DATAFILE",
                action_sql="ALTER TABLESPACE...",
                database_id="DB-01",
                environment="DEV",
                executed_by="agent4",
                result="FAILED",
                error_message="ORA-01237: cannot extend",
            )
        )

    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("DB-01", "test")
    text = mm.format_for_prompt(ctx)

    assert "Historical Failure Stats for DB-01" in text
    assert "ADD_DATAFILE" in text
    assert "2 failed / 2 total" in text


def test_format_empty_no_history(tmp_db, policy_loader):
    """No data -> no historical section in output."""
    mm = MemoryManager(tmp_db, policy_loader)
    ctx = mm.get_context("EMPTY-DB", "tablespace_full")
    text = mm.format_for_prompt(ctx)
    assert text == ""
    assert "Historical" not in text


# ---------------------------------------------------------------------------
# v3.3: Long-Term Memory — config
# ---------------------------------------------------------------------------


def test_config_long_term_days(tmp_db, policy_loader):
    """Config should load long_term_days from memory_rules.md."""
    mm = MemoryManager(tmp_db, policy_loader)
    config = mm._load_config()
    assert config.long_term_days == 90
    assert config.max_history_per_alert == 10


def test_has_memory_includes_history():
    """has_memory returns True when only alert_history exists."""
    ctx = MemoryContext(
        database_id="DB-01",
        alert_type="test",
        config=MemoryConfig(),
        alert_history=[
            AlertHistory(
                alert_type="tablespace_full",
                timestamp="2026-01-01T00:00:00",
                day_of_week=0,
                day_name="Sunday",
                status="COMPLETED",
            )
        ],
    )
    assert ctx.has_memory is True


def test_has_memory_includes_failure_stats():
    """has_memory returns True when only failure_stats exists."""
    ctx = MemoryContext(
        database_id="DB-01",
        alert_type="test",
        config=MemoryConfig(),
        failure_stats=[
            FailureStats(
                action_type="RESIZE",
                total=5,
                failures=2,
                successes=3,
                failure_rate=0.4,
            )
        ],
    )
    assert ctx.has_memory is True
