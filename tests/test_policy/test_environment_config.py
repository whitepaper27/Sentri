"""Tests for EnvironmentConfig — per-database autonomy overrides (v5.1a)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sentri.core.constants import AutonomyLevel
from sentri.policy.environment_config import EnvironmentConfig
from sentri.policy.loader import PolicyLoader

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def env_dir(tmp_path):
    """Create a temporary environments directory."""
    d = tmp_path / "environments"
    d.mkdir()
    return d


@pytest.fixture
def make_env_config(tmp_path, env_dir):
    """Factory to create EnvironmentConfig from temp files."""

    def _make(env_md_content: str, filename: str = "dev_db_01") -> EnvironmentConfig:
        (env_dir / f"{filename}.md").write_text(env_md_content, encoding="utf-8")
        loader = PolicyLoader(tmp_path)
        return EnvironmentConfig(loader)

    return _make


# ---------------------------------------------------------------------------
# get_autonomy_override() Tests
# ---------------------------------------------------------------------------


class TestGetAutonomyOverride:
    def test_returns_none_when_no_override(self, make_env_config):
        """No autonomy_override in frontmatter → returns None."""
        config = make_env_config(
            """---
database_id: dev-db-01
environment: DEV
autonomy_level: AUTONOMOUS
---

# DEV-DB-01
"""
        )
        result = config.get_autonomy_override("dev-db-01")
        assert result is None

    def test_returns_advisory_override(self, make_env_config):
        """autonomy_override: ADVISORY in frontmatter → returns AutonomyOverride."""
        config = make_env_config(
            """---
database_id: dev-db-01
environment: DEV
autonomy_level: AUTONOMOUS
autonomy_override: ADVISORY
override_reason: Contains copy of production data
override_approved_by: john.smith
---

# DEV-DB-01
"""
        )
        result = config.get_autonomy_override("dev-db-01")
        assert result is not None
        assert result.level == AutonomyLevel.ADVISORY
        assert "production data" in result.reason
        assert result.approved_by == "john.smith"

    def test_returns_supervised_override(self, make_env_config):
        """autonomy_override: SUPERVISED in frontmatter."""
        config = make_env_config(
            """---
database_id: dev-db-01
environment: DEV
autonomy_level: AUTONOMOUS
autonomy_override: SUPERVISED
override_reason: Shared with QA
---

# DEV-DB-01
"""
        )
        result = config.get_autonomy_override("dev-db-01")
        assert result is not None
        assert result.level == AutonomyLevel.SUPERVISED

    def test_expired_override_returns_none(self, make_env_config):
        """Override with past expiration date returns None."""
        config = make_env_config(
            """---
database_id: dev-db-01
environment: DEV
autonomy_override: ADVISORY
override_reason: Temporary restriction
override_expires: 2020-01-01
---

# DEV-DB-01
"""
        )
        result = config.get_autonomy_override("dev-db-01")
        assert result is None

    def test_future_override_returns_override(self, make_env_config):
        """Override with future expiration date is active."""
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        config = make_env_config(
            f"""---
database_id: dev-db-01
environment: DEV
autonomy_override: ADVISORY
override_reason: Migration window
override_expires: {future}
---

# DEV-DB-01
"""
        )
        result = config.get_autonomy_override("dev-db-01")
        assert result is not None
        assert result.level == AutonomyLevel.ADVISORY
        assert result.expires is not None

    def test_invalid_override_level_returns_none(self, make_env_config):
        """Invalid autonomy_override value returns None (logged as warning)."""
        config = make_env_config(
            """---
database_id: dev-db-01
environment: DEV
autonomy_override: INVALID_LEVEL
---

# DEV-DB-01
"""
        )
        result = config.get_autonomy_override("dev-db-01")
        assert result is None

    def test_no_env_file_returns_none(self, tmp_path):
        """No environment file for the database → returns None."""
        (tmp_path / "environments").mkdir(exist_ok=True)
        loader = PolicyLoader(tmp_path)
        config = EnvironmentConfig(loader)

        result = config.get_autonomy_override("nonexistent-db")
        assert result is None

    def test_override_without_expiry_has_no_expires_field(self, make_env_config):
        """Override without override_expires has expires=None."""
        config = make_env_config(
            """---
database_id: dev-db-01
environment: DEV
autonomy_override: ADVISORY
override_reason: Permanent restriction
---

# DEV-DB-01
"""
        )
        result = config.get_autonomy_override("dev-db-01")
        assert result is not None
        assert result.expires is None
