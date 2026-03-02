"""Tests for CheckPatterns — health check policy loader (v5.0c)."""

import pytest

from sentri.policy.check_patterns import CheckPatterns

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def check_patterns(policy_loader):
    return CheckPatterns(policy_loader)


# ---------------------------------------------------------------------------
# Health query
# ---------------------------------------------------------------------------


class TestHealthQuery:
    """Test get_health_query loading."""

    def test_stale_stats_query(self, check_patterns):
        """stale_stats.md has a health query."""
        query = check_patterns.get_health_query("stale_stats")
        assert "dba_tables" in query.lower()
        assert "last_analyzed" in query.lower()

    def test_tablespace_trend_query(self, check_patterns):
        """tablespace_trend.md has a health query."""
        query = check_patterns.get_health_query("tablespace_trend")
        assert "dba_tablespace_usage_metrics" in query.lower()

    def test_missing_check_returns_empty(self, check_patterns):
        """Non-existent check type returns empty string."""
        query = check_patterns.get_health_query("nonexistent_check")
        assert query == ""


# ---------------------------------------------------------------------------
# Threshold
# ---------------------------------------------------------------------------


class TestThreshold:
    """Test get_threshold parsing."""

    def test_stale_stats_threshold(self, check_patterns):
        """stale_stats.md thresholds are parsed as numbers."""
        threshold = check_patterns.get_threshold("stale_stats")
        assert threshold["min_rows"] == 1000
        assert threshold["max_days_stale"] == 30
        assert threshold["max_tables"] == 10

    def test_tablespace_trend_threshold(self, check_patterns):
        """tablespace_trend.md thresholds are parsed."""
        threshold = check_patterns.get_threshold("tablespace_trend")
        assert threshold["pct_used"] == 85

    def test_missing_returns_empty(self, check_patterns):
        threshold = check_patterns.get_threshold("nonexistent")
        assert threshold == {}


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


class TestSchedule:
    """Test get_schedule from frontmatter."""

    def test_stale_stats_daily(self, check_patterns):
        assert check_patterns.get_schedule("stale_stats") == "daily"

    def test_tablespace_trend_6h(self, check_patterns):
        assert check_patterns.get_schedule("tablespace_trend") == "every_6_hours"


# ---------------------------------------------------------------------------
# Routes to
# ---------------------------------------------------------------------------


class TestRoutesTo:
    """Test get_routes_to from frontmatter."""

    def test_stale_stats_routes_to_sql_tuning(self, check_patterns):
        assert check_patterns.get_routes_to("stale_stats") == "sql_tuning_agent"

    def test_tablespace_trend_routes_to_storage(self, check_patterns):
        assert check_patterns.get_routes_to("tablespace_trend") == "storage_agent"


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


class TestSeverity:
    """Test get_severity from frontmatter."""

    def test_stale_stats_medium(self, check_patterns):
        assert check_patterns.get_severity("stale_stats") == "MEDIUM"

    def test_tablespace_trend_high(self, check_patterns):
        assert check_patterns.get_severity("tablespace_trend") == "HIGH"


# ---------------------------------------------------------------------------
# Recommended action
# ---------------------------------------------------------------------------


class TestRecommendedAction:
    """Test get_recommended_action."""

    def test_stale_stats_has_action(self, check_patterns):
        action = check_patterns.get_recommended_action("stale_stats")
        assert "DBMS_STATS" in action

    def test_tablespace_trend_has_action(self, check_patterns):
        action = check_patterns.get_recommended_action("tablespace_trend")
        assert "ADD DATAFILE" in action


# ---------------------------------------------------------------------------
# Get all checks
# ---------------------------------------------------------------------------


class TestGetAllChecks:
    """Test get_all_checks discovery."""

    def test_discovers_both_checks(self, check_patterns):
        """Should find stale_stats and tablespace_trend."""
        all_checks = check_patterns.get_all_checks()
        assert "stale_stats" in all_checks
        assert "tablespace_trend" in all_checks
        assert len(all_checks) >= 2

    def test_check_info_structure(self, check_patterns):
        """Each check has expected keys."""
        all_checks = check_patterns.get_all_checks()
        for check_type, info in all_checks.items():
            assert "schedule" in info
            assert "severity" in info
            assert "routes_to" in info
            assert "health_query" in info
