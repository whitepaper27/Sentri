"""Tests for RulesEngine — policy-driven confidence thresholds and action matrix (v5.1a)."""

from __future__ import annotations

import pytest

from sentri.policy.loader import PolicyLoader
from sentri.policy.rules_engine import RulesEngine, Verdict

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rules_engine(policy_loader):
    """RulesEngine loaded from _default_policies."""
    return RulesEngine(policy_loader)


@pytest.fixture
def custom_rules_engine(tmp_path):
    """Create a RulesEngine from a custom rules.md with configurable thresholds."""

    def _make(rules_md_content: str) -> RulesEngine:
        brain_dir = tmp_path / "brain"
        brain_dir.mkdir(exist_ok=True)
        (brain_dir / "rules.md").write_text(rules_md_content, encoding="utf-8")
        loader = PolicyLoader(tmp_path)
        return RulesEngine(loader)

    return _make


# ---------------------------------------------------------------------------
# Confidence Thresholds — Parsed from rules.md
# ---------------------------------------------------------------------------


class TestConfidenceThresholds:
    """Test that confidence thresholds are parsed from brain/rules.md."""

    def test_default_thresholds_from_rules_md(self, rules_engine):
        """Default rules.md has < 0.60 = BLOCK and 0.60-0.79 = REQUIRE_APPROVAL."""
        # Below block threshold
        v = rules_engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.50)
        assert v.verdict == Verdict.BLOCK

        # In approval range
        v = rules_engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.70)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

        # Above approval threshold
        v = rules_engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.85)
        assert v.verdict == Verdict.ALLOW

    def test_custom_thresholds_parsed(self, custom_rules_engine):
        """Custom rules.md with different thresholds should be respected."""
        rules_md = """---
type: core_policy
name: execution_rules
version: 1
---

# Execution Rules

## Action Rules

| Action Type | DEV | UAT | PROD | Notes |
|-------------|-----|-----|------|-------|
| ADD_DATAFILE | auto | auto | approval | test |

## Confidence Thresholds

| Confidence | Action |
|------------|--------|
| < 0.50 | Escalate to DBA. Do not execute. |
| 0.50 - 0.89 | Run pre-flight checks. Require approval regardless of environment. |
| >= 0.90 | Follow environment rules above. |
"""
        engine = custom_rules_engine(rules_md)

        # Below custom block threshold (0.50)
        v = engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.40)
        assert v.verdict == Verdict.BLOCK
        assert "0.50" in v.reasons[0]

        # In custom approval range (0.50-0.89 → approval below 0.90)
        v = engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.60)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

        # Previously this would have been ALLOW at 0.85 — now it's approval
        v = engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.85)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

        # Above 0.90 = ALLOW
        v = engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.92)
        assert v.verdict == Verdict.ALLOW

    def test_fallback_defaults_when_no_thresholds_table(self, custom_rules_engine):
        """Missing Confidence Thresholds section falls back to 0.60/0.80 defaults."""
        rules_md = """---
type: core_policy
name: execution_rules
version: 1
---

# Execution Rules

## Action Rules

| Action Type | DEV | UAT | PROD | Notes |
|-------------|-----|-----|------|-------|
| ADD_DATAFILE | auto | auto | approval | test |
"""
        engine = custom_rules_engine(rules_md)

        # Default block at 0.60
        v = engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.55)
        assert v.verdict == Verdict.BLOCK

        # Default approval at 0.80
        v = engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.75)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

        v = engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.85)
        assert v.verdict == Verdict.ALLOW

    def test_threshold_edge_cases(self, rules_engine):
        """Test boundary values for confidence thresholds."""
        # Exactly at block threshold (0.60) — should be in approval range, not blocked
        v = rules_engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.60)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

        # Just below block threshold
        v = rules_engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.59)
        assert v.verdict == Verdict.BLOCK

        # Exactly at approval threshold (0.80) — should be ALLOW
        v = rules_engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.80)
        assert v.verdict == Verdict.ALLOW

        # Just below approval threshold
        v = rules_engine.evaluate("ADD_DATAFILE", "DEV", confidence=0.79)
        assert v.verdict == Verdict.REQUIRE_APPROVAL


# ---------------------------------------------------------------------------
# Specialist Action Types in Action Matrix
# ---------------------------------------------------------------------------


class TestSpecialistActionTypes:
    """Test that specialist agent action types are in the Action Rules matrix."""

    def test_cpu_high_requires_approval_on_dev(self, rules_engine):
        """CPU_HIGH should require approval even on DEV — the original bug."""
        v = rules_engine.evaluate("CPU_HIGH", "DEV", confidence=0.90)
        assert v.verdict == Verdict.REQUIRE_APPROVAL
        assert "CPU_HIGH" in v.reasons[0]

    def test_long_running_sql_requires_approval_on_dev(self, rules_engine):
        """LONG_RUNNING_SQL should require approval on DEV."""
        v = rules_engine.evaluate("LONG_RUNNING_SQL", "DEV", confidence=0.90)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

    def test_session_blocker_requires_approval_on_dev(self, rules_engine):
        """SESSION_BLOCKER should require approval on DEV."""
        v = rules_engine.evaluate("SESSION_BLOCKER", "DEV", confidence=0.90)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

    def test_tablespace_full_auto_on_dev(self, rules_engine):
        """TABLESPACE_FULL should auto-execute on DEV (no regression)."""
        v = rules_engine.evaluate("TABLESPACE_FULL", "DEV", confidence=0.90)
        assert v.verdict == Verdict.ALLOW

    def test_temp_full_auto_on_dev(self, rules_engine):
        """TEMP_FULL should auto-execute on DEV."""
        v = rules_engine.evaluate("TEMP_FULL", "DEV", confidence=0.90)
        assert v.verdict == Verdict.ALLOW

    def test_archive_dest_full_auto_on_dev(self, rules_engine):
        """ARCHIVE_DEST_FULL should auto-execute on DEV."""
        v = rules_engine.evaluate("ARCHIVE_DEST_FULL", "DEV", confidence=0.90)
        assert v.verdict == Verdict.ALLOW

    def test_archive_dest_full_approval_on_uat(self, rules_engine):
        """ARCHIVE_DEST_FULL should require approval on UAT."""
        v = rules_engine.evaluate("ARCHIVE_DEST_FULL", "UAT", confidence=0.90)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

    def test_high_undo_usage_approval_on_uat(self, rules_engine):
        """HIGH_UNDO_USAGE should require approval on UAT."""
        v = rules_engine.evaluate("HIGH_UNDO_USAGE", "UAT", confidence=0.90)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

    def test_check_finding_auto_on_dev(self, rules_engine):
        """CHECK_FINDING should auto-execute on DEV (low-risk proactive)."""
        v = rules_engine.evaluate("CHECK_FINDING", "DEV", confidence=0.90)
        assert v.verdict == Verdict.ALLOW

    def test_check_finding_approval_on_prod(self, rules_engine):
        """CHECK_FINDING should require approval on PROD."""
        v = rules_engine.evaluate("CHECK_FINDING", "PROD", confidence=0.90)
        assert v.verdict == Verdict.REQUIRE_APPROVAL

    def test_all_specialist_actions_require_approval_on_prod(self, rules_engine):
        """ALL action types should require approval on PROD."""
        actions = [
            "CPU_HIGH",
            "LONG_RUNNING_SQL",
            "SESSION_BLOCKER",
            "TABLESPACE_FULL",
            "TEMP_FULL",
            "ARCHIVE_DEST_FULL",
            "HIGH_UNDO_USAGE",
            "CHECK_FINDING",
            "ADD_DATAFILE",
            "KILL_SESSION",
            "START_LISTENER",
        ]
        for action in actions:
            v = rules_engine.evaluate(action, "PROD", confidence=0.90)
            assert (
                v.verdict == Verdict.REQUIRE_APPROVAL
            ), f"{action} on PROD should require approval, got {v.verdict}"


# ---------------------------------------------------------------------------
# Reload picks up new thresholds
# ---------------------------------------------------------------------------


class TestReload:
    """Test that reload() picks up changed thresholds."""

    def test_reload_updates_confidence_thresholds(self, tmp_path):
        """Changing rules.md and reloading should update confidence thresholds."""
        brain_dir = tmp_path / "brain"
        brain_dir.mkdir()

        # Start with standard thresholds
        rules_v1 = """---
type: core_policy
name: execution_rules
version: 1
---

# Execution Rules

## Confidence Thresholds

| Confidence | Action |
|------------|--------|
| < 0.60 | Escalate to DBA. Do not execute. |
| 0.60 - 0.79 | Require approval. |
| >= 0.80 | Follow environment rules. |
"""
        (brain_dir / "rules.md").write_text(rules_v1, encoding="utf-8")
        loader = PolicyLoader(tmp_path)
        engine = RulesEngine(loader)

        v = engine.evaluate("SOMETHING", "DEV", confidence=0.55)
        assert v.verdict == Verdict.BLOCK

        # Update thresholds to be more lenient
        rules_v2 = """---
type: core_policy
name: execution_rules
version: 2
---

# Execution Rules

## Confidence Thresholds

| Confidence | Action |
|------------|--------|
| < 0.40 | Escalate to DBA. Do not execute. |
| 0.40 - 0.69 | Require approval. |
| >= 0.70 | Follow environment rules. |
"""
        (brain_dir / "rules.md").write_text(rules_v2, encoding="utf-8")
        engine.reload()

        # 0.55 should now be in approval range, not blocked
        v = engine.evaluate("SOMETHING", "DEV", confidence=0.55)
        assert v.verdict == Verdict.REQUIRE_APPROVAL
