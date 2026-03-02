"""Tests for InvestigationStore — persist agent analysis as .md files."""

from types import SimpleNamespace

import pytest

from sentri.memory.investigation_store import InvestigationRecord, InvestigationStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def inv_dir(tmp_path):
    """Temporary investigations directory."""
    d = tmp_path / "investigations"
    d.mkdir()
    return d


@pytest.fixture
def store(inv_dir):
    return InvestigationStore(inv_dir)


def _make_option(**kwargs):
    """Create a fake ResearchOption-like object."""
    defaults = {
        "option_id": "opt-1",
        "title": "Gather stale statistics",
        "description": "Gather optimizer stats on stale tables",
        "forward_sql": "EXEC DBMS_STATS.GATHER_TABLE_STATS('HR', 'EMPLOYEES')",
        "rollback_sql": "N/A",
        "confidence": 0.90,
        "risk_level": "LOW",
        "reasoning": "Stale stats cause poor execution plans.",
        "source": "template",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# save() tests
# ---------------------------------------------------------------------------


class TestSave:
    def test_save_creates_file(self, store, inv_dir):
        path = store.save(
            workflow_id="wf-123",
            database_id="sentri-dev",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={"alert_type": "cpu_high", "wait_events": []},
            candidates=[_make_option()],
            selected=_make_option(),
            result={"status": "success"},
        )
        assert path is not None
        assert path.exists()
        assert path.suffix == ".md"

    def test_save_filename_format(self, store, inv_dir):
        path = store.save(
            workflow_id="wf-456",
            database_id="sentri-dev",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        # Filename: YYYY-MM-DD_HHMMSS_sentri_dev_cpu_high.md
        name = path.name
        assert name.endswith(".md")
        assert "sentri" in name
        assert "cpu_high" in name

    def test_save_yaml_frontmatter(self, store, inv_dir):
        path = store.save(
            workflow_id="wf-789",
            database_id="prod-db-07",
            alert_type="tablespace_full",
            environment="PROD",
            agent_name="storage_agent",
            confidence=0.95,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "workflow_id: wf-789" in content
        assert "database_id: prod-db-07" in content
        assert "alert_type: tablespace_full" in content
        assert "environment: PROD" in content
        assert "agent: storage_agent" in content
        assert "confidence: 0.95" in content
        assert "status: success" in content

    def test_save_investigation_section(self, store):
        investigation = {
            "alert_type": "cpu_high",
            "wait_events": [
                {
                    "WAIT_CLASS": "User I/O",
                    "EVENT": "db file sequential read",
                    "TIME_WAITED": 12345,
                },
            ],
            "top_sql": [
                {"SQL_ID": "abc123", "CPU_TIME": 890123, "SQL_TEXT": "SELECT * FROM employees"},
            ],
        }
        path = store.save(
            workflow_id="wf-inv",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation=investigation,
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        content = path.read_text(encoding="utf-8")
        assert "## Investigation Findings" in content
        assert "Wait Events" in content
        assert "Top SQL" in content
        assert "abc123" in content

    def test_save_candidates_section(self, store):
        opt1 = _make_option(title="Option A", option_id="a")
        opt2 = _make_option(title="Option B", option_id="b")
        path = store.save(
            workflow_id="wf-cands",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[opt1, opt2],
            selected=opt1,
            result={"status": "success"},
        )
        content = path.read_text(encoding="utf-8")
        assert "## Candidates Considered" in content
        assert "Option A" in content
        assert "Option B" in content
        assert "(SELECTED)" in content

    def test_save_decision_section(self, store):
        selected = _make_option(title="My Fix")
        path = store.save(
            workflow_id="wf-decision",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[selected],
            selected=selected,
            result={"status": "success"},
        )
        content = path.read_text(encoding="utf-8")
        assert "## Decision" in content
        assert "**Selected**: My Fix" in content

    def test_save_outcome_section(self, store):
        path = store.save(
            workflow_id="wf-outcome",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "failed", "error": "ORA-01652"},
        )
        content = path.read_text(encoding="utf-8")
        assert "## Outcome" in content
        assert "**Status**: failed" in content
        assert "**Error**: ORA-01652" in content

    def test_save_empty_investigation(self, store):
        """Storage agent returns empty investigation — file still created."""
        path = store.save(
            workflow_id="wf-empty",
            database_id="dev-01",
            alert_type="tablespace_full",
            environment="DEV",
            agent_name="storage_agent",
            confidence=0.90,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        content = path.read_text(encoding="utf-8")
        assert (
            "delegated to existing pipeline" in content.lower()
            or "No investigation data" in content
        )

    def test_save_rca_tiered_data(self, store):
        """RCA agent produces tiered data (t1, t2)."""
        investigation = {
            "alert_type": "session_blocker",
            "focus_area": "blocking",
            "t1": {
                "wait_classes": [{"WAIT_CLASS": "Application", "TOTAL_WAIT": 99999}],
                "top_events": [{"EVENT": "enq: TX - row lock", "TIME_WAITED": 88888}],
            },
            "t2": {
                "blocking_chain": [
                    {"BLOCKER_SID": 100, "BLOCKED_SID": 200, "BLOCKED_EVENT": "enq: TX"},
                ],
            },
        }
        path = store.save(
            workflow_id="wf-rca",
            database_id="prod-07",
            alert_type="session_blocker",
            environment="PROD",
            agent_name="rca_agent",
            confidence=0.85,
            investigation=investigation,
            candidates=[],
            selected=None,
            result={"status": "escalated"},
        )
        content = path.read_text(encoding="utf-8")
        assert "Tier 1" in content
        assert "Tier 2" in content
        assert "blocking_chain" in content

    def test_save_filename_sanitization(self, store, inv_dir):
        """Special chars in database_id/alert_type are sanitized."""
        path = store.save(
            workflow_id="wf-san",
            database_id="prod/db:07",
            alert_type="check_finding:stale_stats",
            environment="PROD",
            agent_name="sql_tuning_agent",
            confidence=0.90,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        name = path.name
        assert "/" not in name
        assert ":" not in name

    def test_save_creates_directory_if_missing(self, tmp_path):
        """Save creates the investigations directory if it doesn't exist."""
        missing_dir = tmp_path / "nonexistent" / "investigations"
        store = InvestigationStore(missing_dir)
        path = store.save(
            workflow_id="wf-mkdir",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        assert path is not None
        assert missing_dir.exists()


# ---------------------------------------------------------------------------
# load_recent() tests
# ---------------------------------------------------------------------------


class TestLoadRecent:
    def test_empty_directory(self, store):
        records = store.load_recent("sentri-dev")
        assert records == []

    def test_nonexistent_directory(self, tmp_path):
        store = InvestigationStore(tmp_path / "does_not_exist")
        records = store.load_recent("sentri-dev")
        assert records == []

    def test_filters_by_database_id(self, store):
        store.save(
            workflow_id="wf-a",
            database_id="db-alpha",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        store.save(
            workflow_id="wf-b",
            database_id="db-beta",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        records = store.load_recent("db-alpha")
        assert len(records) == 1
        assert records[0].database_id == "db-alpha"

    def test_respects_max_files(self, store, inv_dir):
        # Create files with distinct timestamps in filenames
        # Note: "dev-01" sanitizes to "dev-01" (dash allowed in regex)
        for i in range(10):
            content = f"---\nworkflow_id: wf-{i}\ndatabase_id: dev-01\nalert_type: cpu_high\nenvironment: DEV\nagent: sql_tuning_agent\nconfidence: 0.80\ntimestamp: 2026-02-27T10:{i:02d}:00+00:00\nstatus: success\n---\n\n# cpu_high on dev-01\n\n## Investigation Findings\n\n_None._\n\n## Candidates Considered\n\n## Decision\n\nNo candidate selected.\n\n## Outcome\n\n**Status**: success\n"
            f = inv_dir / f"2026-02-27_10{i:02d}00_dev-01_cpu_high.md"
            f.write_text(content, encoding="utf-8")
        records = store.load_recent("dev-01", max_files=3)
        assert len(records) == 3

    def test_sorted_newest_first(self, store, inv_dir):
        for i in range(3):
            content = f"---\nworkflow_id: wf-{i}\ndatabase_id: dev-01\nalert_type: cpu_high\nenvironment: DEV\nagent: sql_tuning_agent\nconfidence: 0.80\ntimestamp: 2026-02-27T10:0{i}:00+00:00\nstatus: success\n---\n\n# cpu_high on dev-01\n\n## Investigation Findings\n\n_None._\n\n## Candidates Considered\n\n## Decision\n\nNo candidate selected.\n\n## Outcome\n\n**Status**: success\n"
            f = inv_dir / f"2026-02-27_100{i}00_dev-01_cpu_high.md"
            f.write_text(content, encoding="utf-8")
        records = store.load_recent("dev-01", max_files=3)
        # Newest first = wf-2 (filename sorted descending)
        assert records[0].workflow_id == "wf-2"

    def test_respects_max_age(self, store, inv_dir):
        # Create a file with an old date in its frontmatter
        old_content = """---
workflow_id: wf-old
database_id: dev-01
alert_type: cpu_high
environment: DEV
agent: sql_tuning_agent
confidence: 0.80
timestamp: 2020-01-01T00:00:00+00:00
status: success
---

# cpu_high on dev-01

## Investigation Findings

_None._

## Candidates Considered

## Decision

No candidate selected.

## Outcome

**Status**: success
"""
        old_file = inv_dir / "2020-01-01_000000_dev-01_cpu_high.md"
        old_file.write_text(old_content, encoding="utf-8")

        # Create a recent file
        store.save(
            workflow_id="wf-recent",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )

        records = store.load_recent("dev-01", max_age_days=30)
        assert len(records) == 1
        assert records[0].workflow_id == "wf-recent"


# ---------------------------------------------------------------------------
# load_for_workflow() tests
# ---------------------------------------------------------------------------


class TestLoadForWorkflow:
    def test_found(self, store):
        store.save(
            workflow_id="wf-find-me",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        rec = store.load_for_workflow("wf-find-me")
        assert rec is not None
        assert rec.workflow_id == "wf-find-me"

    def test_not_found(self, store):
        rec = store.load_for_workflow("wf-nonexistent")
        assert rec is None

    def test_partial_id_match(self, store):
        store.save(
            workflow_id="wf-abc123-def456",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        rec = store.load_for_workflow("wf-abc123")
        assert rec is not None
        assert rec.workflow_id == "wf-abc123-def456"


# ---------------------------------------------------------------------------
# format_for_prompt() tests
# ---------------------------------------------------------------------------


class TestFormatForPrompt:
    def test_empty_returns_empty_string(self, store):
        result = store.format_for_prompt([])
        assert result == ""

    def test_with_records(self, store):
        rec = InvestigationRecord(
            file_path="/tmp/test.md",
            timestamp="2026-02-27T14:32:18+00:00",
            workflow_id="wf-1",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            status="success",
            investigation_summary="Wait events show high User I/O",
            selected_option_title="Gather stale statistics",
            selected_option_reasoning="Stale stats cause poor plans",
            candidates_count=3,
            outcome="success",
        )
        result = store.format_for_prompt([rec])
        assert "## Past Investigations on dev-01" in result
        assert "cpu_high" in result
        assert "sql_tuning_agent" in result
        assert "Gather stale statistics" in result
        assert "Stale stats cause poor plans" in result
        assert "Avoid repeating failed approaches" in result


# ---------------------------------------------------------------------------
# cleanup() tests
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_removes_old_files(self, store, inv_dir):
        # Create a file with an old date in filename
        old_file = inv_dir / "2020-01-01_000000_dev-01_cpu_high.md"
        old_file.write_text("---\nstatus: old\n---\nOld file.", encoding="utf-8")

        # Create a recent file
        store.save(
            workflow_id="wf-recent",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )

        deleted = store.cleanup(retention_days=90)
        assert deleted == 1
        assert not old_file.exists()

    def test_keeps_recent_files(self, store, inv_dir):
        store.save(
            workflow_id="wf-keep",
            database_id="dev-01",
            alert_type="cpu_high",
            environment="DEV",
            agent_name="sql_tuning_agent",
            confidence=0.80,
            investigation={},
            candidates=[],
            selected=None,
            result={"status": "success"},
        )
        deleted = store.cleanup(retention_days=90)
        assert deleted == 0
        assert len(list(inv_dir.glob("*.md"))) == 1

    def test_returns_count(self, store, inv_dir):
        for year in range(2018, 2021):
            f = inv_dir / f"{year}-01-01_000000_dev_01_cpu_high.md"
            f.write_text("---\nstatus: old\n---", encoding="utf-8")
        deleted = store.cleanup(retention_days=90)
        assert deleted == 3

    def test_nonexistent_directory(self, tmp_path):
        store = InvestigationStore(tmp_path / "nope")
        assert store.cleanup() == 0


# ---------------------------------------------------------------------------
# _dict_list_to_table() tests
# ---------------------------------------------------------------------------


class TestDictListToTable:
    def test_basic_table(self):
        rows = [
            {"NAME": "CPU", "VALUE": 12345},
            {"NAME": "IO", "VALUE": 67890},
        ]
        result = InvestigationStore._dict_list_to_table(rows)
        assert "NAME" in result
        assert "VALUE" in result
        assert "CPU" in result
        assert "12345" in result

    def test_empty_rows(self):
        assert "_No data._" in InvestigationStore._dict_list_to_table([])

    def test_truncates_long_cells(self):
        rows = [{"DATA": "x" * 200}]
        result = InvestigationStore._dict_list_to_table(rows)
        # Cell should be truncated to 100 chars
        assert len("x" * 200) not in [len(line) for line in result.split("\n")]

    def test_max_rows(self):
        rows = [{"N": i} for i in range(50)]
        result = InvestigationStore._dict_list_to_table(rows, max_rows=5)
        assert "... and 45 more rows" in result


# ---------------------------------------------------------------------------
# _parse_frontmatter() tests
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_basic(self):
        text = "---\nkey1: value1\nkey2: value2\n---\n# Content"
        result = InvestigationStore._parse_frontmatter(text)
        assert result == {"key1": "value1", "key2": "value2"}

    def test_no_frontmatter(self):
        result = InvestigationStore._parse_frontmatter("# Just content")
        assert result == {}

    def test_values_with_colons(self):
        text = "---\ntimestamp: 2026-02-27T14:32:18+00:00\n---\n"
        result = InvestigationStore._parse_frontmatter(text)
        assert result["timestamp"] == "2026-02-27T14:32:18+00:00"


# ---------------------------------------------------------------------------
# Integration: specialist_base persistence
# ---------------------------------------------------------------------------


class TestSpecialistBasePersistence:
    def test_persist_calls_save(self, store):
        """Mock-free test: verify _persist_investigation calls store.save."""
        from unittest.mock import MagicMock

        from sentri.agents.specialist_base import SpecialistBase

        mock_store = MagicMock()
        base = MagicMock(spec=SpecialistBase)
        base._investigation_store = mock_store
        base.name = "test_agent"
        base.logger = MagicMock()

        # Call the real method
        wf = MagicMock()
        wf.id = "wf-test"
        wf.database_id = "dev-01"
        wf.alert_type = "cpu_high"
        wf.environment = "DEV"

        SpecialistBase._persist_investigation(
            base,
            wf,
            0.80,
            {"data": "test"},
            [],
            None,
            {"status": "success"},
        )
        mock_store.save.assert_called_once()

    def test_persist_without_store(self):
        """No store configured — should not error."""
        from unittest.mock import MagicMock

        from sentri.agents.specialist_base import SpecialistBase

        base = MagicMock(spec=SpecialistBase)
        base._investigation_store = None

        # Should return immediately without error
        SpecialistBase._persist_investigation(
            base,
            MagicMock(),
            0.80,
            {},
            [],
            None,
            {"status": "success"},
        )

    def test_persist_failure_doesnt_raise(self, store):
        """If store.save() throws, _persist_investigation should not raise."""
        from unittest.mock import MagicMock

        from sentri.agents.specialist_base import SpecialistBase

        mock_store = MagicMock()
        mock_store.save.side_effect = OSError("Disk full")

        base = MagicMock(spec=SpecialistBase)
        base._investigation_store = mock_store
        base.name = "test_agent"
        base.logger = MagicMock()

        wf = MagicMock()
        wf.id = "wf-err"
        wf.database_id = "dev-01"
        wf.alert_type = "cpu_high"
        wf.environment = "DEV"

        # Should NOT raise
        SpecialistBase._persist_investigation(
            base,
            wf,
            0.80,
            {},
            [],
            None,
            {"status": "success"},
        )
        base.logger.warning.assert_called_once()
