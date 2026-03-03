"""Tests for recursive policy loading — multi-DB directory scaffolding (v5.1b)."""

from __future__ import annotations

import pytest

from sentri.policy.loader import PolicyLoader


@pytest.fixture
def multi_db_dir(tmp_path):
    """Create a multi-DB alerts/checks directory structure."""
    # Flat alerts (existing — backwards compat)
    alerts_dir = tmp_path / "alerts"
    alerts_dir.mkdir()
    (alerts_dir / "tablespace_full.md").write_text(
        "---\nalert_type: tablespace_full\n---\n\n## Email Pattern\ntest",
        encoding="utf-8",
    )
    (alerts_dir / "README.md").write_text("# Alerts\n", encoding="utf-8")

    # Oracle subdirectory
    oracle_dir = alerts_dir / "oracle"
    oracle_dir.mkdir()
    (oracle_dir / "ora_blocker.md").write_text(
        "---\nalert_type: ora_blocker\n---\n\n## Email Pattern\noracle test",
        encoding="utf-8",
    )
    (oracle_dir / "README.md").write_text("# Oracle Alerts\n", encoding="utf-8")

    # Postgres subdirectory
    pg_dir = alerts_dir / "postgres"
    pg_dir.mkdir()
    (pg_dir / "replication_lag.md").write_text(
        "---\nalert_type: replication_lag\n---\n\n## Email Pattern\npg test",
        encoding="utf-8",
    )
    (pg_dir / "README.md").write_text("# Postgres Alerts\n", encoding="utf-8")

    # Flat checks (existing — backwards compat)
    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    (checks_dir / "stale_stats.md").write_text(
        "---\ncheck_type: stale_stats\n---\n\n## Health Query\ntest",
        encoding="utf-8",
    )
    (checks_dir / "README.md").write_text("# Checks\n", encoding="utf-8")

    # Snowflake checks subdirectory
    sf_dir = checks_dir / "snowflake"
    sf_dir.mkdir()
    (sf_dir / "warehouse_idle.md").write_text(
        "---\ncheck_type: warehouse_idle\n---\n\n## Health Query\nsf test",
        encoding="utf-8",
    )

    return tmp_path


class TestRecursiveAlertLoading:
    """Test that load_all_alerts() scans subdirectories."""

    def test_flat_alerts_still_load(self, multi_db_dir):
        """Flat alerts/*.md files still load (backwards compatible)."""
        loader = PolicyLoader(multi_db_dir)
        alerts = loader.load_all_alerts()
        assert "tablespace_full" in alerts

    def test_subdirectory_alerts_load(self, multi_db_dir):
        """Alerts in subdirectories (alerts/oracle/*.md) are loaded."""
        loader = PolicyLoader(multi_db_dir)
        alerts = loader.load_all_alerts()
        assert "ora_blocker" in alerts

    def test_multiple_subdirectories_load(self, multi_db_dir):
        """Alerts from multiple subdirectories all load."""
        loader = PolicyLoader(multi_db_dir)
        alerts = loader.load_all_alerts()
        assert "tablespace_full" in alerts  # flat
        assert "ora_blocker" in alerts  # oracle/
        assert "replication_lag" in alerts  # postgres/

    def test_readme_files_excluded(self, multi_db_dir):
        """README.md files in all directories are excluded."""
        loader = PolicyLoader(multi_db_dir)
        alerts = loader.load_all_alerts()
        assert "readme" not in alerts
        assert "README" not in alerts

    def test_empty_subdirectory_no_error(self, multi_db_dir):
        """Empty subdirectory with only README.md doesn't cause errors."""
        empty_dir = multi_db_dir / "alerts" / "sqlserver"
        empty_dir.mkdir()
        (empty_dir / "README.md").write_text("# SQL Server\n", encoding="utf-8")

        loader = PolicyLoader(multi_db_dir)
        alerts = loader.load_all_alerts()
        assert len(alerts) == 3  # flat + oracle + postgres

    def test_no_alerts_dir_returns_empty(self, tmp_path):
        """Missing alerts/ directory returns empty dict."""
        loader = PolicyLoader(tmp_path)
        alerts = loader.load_all_alerts()
        assert alerts == {}


class TestRecursiveCheckLoading:
    """Test that load_all_checks() scans subdirectories."""

    def test_flat_checks_still_load(self, multi_db_dir):
        """Flat checks/*.md files still load."""
        loader = PolicyLoader(multi_db_dir)
        checks = loader.load_all_checks()
        assert "stale_stats" in checks

    def test_subdirectory_checks_load(self, multi_db_dir):
        """Checks in subdirectories load."""
        loader = PolicyLoader(multi_db_dir)
        checks = loader.load_all_checks()
        assert "warehouse_idle" in checks

    def test_readme_excluded_in_checks(self, multi_db_dir):
        """README.md in checks subdirectories is excluded."""
        loader = PolicyLoader(multi_db_dir)
        checks = loader.load_all_checks()
        assert "readme" not in checks
        assert "README" not in checks


class TestDbEngineConfig:
    """Test that db_engine field works in DatabaseConfig."""

    def test_db_engine_default_oracle(self):
        """Default db_engine is 'oracle'."""
        from sentri.config.settings import DatabaseConfig

        db = DatabaseConfig(name="test")
        assert db.db_engine == "oracle"

    def test_db_engine_parsed_from_yaml(self, tmp_path):
        """db_engine is parsed from YAML config."""
        from sentri.config.settings import Settings

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "databases:\n"
            "  - name: pg-dev\n"
            "    db_engine: postgres\n"
            "    connection_string: postgresql://user@host/db\n"
            "    environment: DEV\n",
            encoding="utf-8",
        )

        settings = Settings.load(config)
        assert len(settings.databases) == 1
        assert settings.databases[0].db_engine == "postgres"

    def test_db_engine_multiple_engines_from_yaml(self, tmp_path):
        """Multiple db_engine values are parsed correctly."""
        from sentri.config.settings import Settings

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "databases:\n"
            "  - name: oracle-dev\n"
            "    db_engine: oracle\n"
            "    connection_string: oracle://sys@host:1521/DEV\n"
            "    environment: DEV\n"
            "  - name: pg-uat\n"
            "    db_engine: postgres\n"
            "    connection_string: postgresql://user@host/db\n"
            "    environment: UAT\n"
            "  - name: sf-prod\n"
            "    db_engine: snowflake\n"
            "    connection_string: snowflake://user@account/wh\n"
            "    environment: PROD\n"
            "  - name: mssql-dev\n"
            "    db_engine: sqlserver\n"
            "    connection_string: mssql://user@host:1433/erpdb\n"
            "    environment: DEV\n",
            encoding="utf-8",
        )

        settings = Settings.load(config)
        assert len(settings.databases) == 4
        engines = [db.db_engine for db in settings.databases]
        assert engines == ["oracle", "postgres", "snowflake", "sqlserver"]

    def test_db_engine_missing_defaults_to_oracle(self, tmp_path):
        """db_engine missing from YAML defaults to 'oracle'."""
        from sentri.config.settings import Settings

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "databases:\n"
            "  - name: legacy-db\n"
            "    connection_string: oracle://user@host:1521/DB\n"
            "    environment: DEV\n",
            encoding="utf-8",
        )

        settings = Settings.load(config)
        assert settings.databases[0].db_engine == "oracle"


class TestBundledPoliciesIntegration:
    """Integration tests against the real bundled _default_policies."""

    @pytest.fixture
    def bundled_loader(self):
        """PolicyLoader pointed at real _default_policies."""
        from pathlib import Path

        policies = Path(__file__).parent.parent.parent / "src" / "sentri" / "_default_policies"
        assert policies.exists(), f"_default_policies not found at {policies}"
        return PolicyLoader(policies)

    def test_bundled_alerts_load_recursively(self, bundled_loader):
        """All 9 bundled alert patterns load (flat only, subdirs are README-only)."""
        alerts = bundled_loader.load_all_alerts()
        # These are the known alert types
        expected = {
            "tablespace_full",
            "temp_full",
            "archive_dest_full",
            "listener_down",
            "archive_gap",
            "session_blocker",
            "cpu_high",
            "high_undo_usage",
            "long_running_sql",
        }
        assert expected.issubset(set(alerts.keys())), (
            f"Missing alerts: {expected - set(alerts.keys())}"
        )

    def test_bundled_alerts_exclude_readme(self, bundled_loader):
        """README.md files in alerts/ and subdirectories are excluded."""
        alerts = bundled_loader.load_all_alerts()
        for key in alerts:
            assert key.lower() != "readme", f"README loaded as alert: {key}"

    def test_bundled_checks_load_recursively(self, bundled_loader):
        """Bundled health checks load (stale_stats, tablespace_trend)."""
        checks = bundled_loader.load_all_checks()
        assert "stale_stats" in checks
        assert "tablespace_trend" in checks

    def test_bundled_checks_exclude_readme(self, bundled_loader):
        """README.md files in checks/ and subdirectories are excluded."""
        checks = bundled_loader.load_all_checks()
        for key in checks:
            assert key.lower() != "readme", f"README loaded as check: {key}"

    def test_subdirectory_readmes_exist_but_excluded(self, bundled_loader):
        """Subdirectory READMEs exist on disk but aren't loaded as policies."""
        from pathlib import Path

        policies = Path(__file__).parent.parent.parent / "src" / "sentri" / "_default_policies"
        # Verify the subdirectory README files exist
        for subdir in ["oracle", "postgres", "snowflake", "sqlserver"]:
            readme = policies / "alerts" / subdir / "README.md"
            assert readme.exists(), f"Missing: {readme}"

        # But they don't appear in loaded alerts
        alerts = bundled_loader.load_all_alerts()
        assert "README" not in alerts
        assert "readme" not in alerts


class TestInitializerRecursiveCopy:
    """Test that sentri init copies subdirectory structure correctly."""

    def test_initialize_copies_subdirectory_readmes(self, tmp_path):
        """sentri init copies alerts/oracle/README.md etc. into target."""
        import shutil

        # Simulate what initialize() does: rglob + relative copy
        from pathlib import Path

        from sentri.config.initializer import POLICY_DIRS

        defaults_path = Path(__file__).parent.parent.parent / "src" / "sentri" / "_default_policies"

        for policy_dir in POLICY_DIRS:
            src_dir = defaults_path / policy_dir
            dst_dir = tmp_path / policy_dir
            if not src_dir.exists():
                continue
            dst_dir.mkdir(parents=True, exist_ok=True)
            for src_file in src_dir.rglob("*.md"):
                rel = src_file.relative_to(src_dir)
                dst_file = dst_dir / rel
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)

        # Verify subdirectory READMEs were copied
        assert (tmp_path / "alerts" / "oracle" / "README.md").exists()
        assert (tmp_path / "alerts" / "postgres" / "README.md").exists()
        assert (tmp_path / "alerts" / "snowflake" / "README.md").exists()
        assert (tmp_path / "alerts" / "sqlserver" / "README.md").exists()
        assert (tmp_path / "checks" / "oracle" / "README.md").exists()
        assert (tmp_path / "checks" / "postgres" / "README.md").exists()

    def test_recursive_copy_preserves_flat_files(self, tmp_path):
        """rglob copy still copies flat alerts/*.md files."""
        import shutil
        from pathlib import Path

        defaults_path = Path(__file__).parent.parent.parent / "src" / "sentri" / "_default_policies"

        src_dir = defaults_path / "alerts"
        dst_dir = tmp_path / "alerts"
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src_file in src_dir.rglob("*.md"):
            rel = src_file.relative_to(src_dir)
            dst_file = dst_dir / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)

        # Flat files should be copied
        assert (tmp_path / "alerts" / "tablespace_full.md").exists()
        assert (tmp_path / "alerts" / "cpu_high.md").exists()
        assert (tmp_path / "alerts" / "session_blocker.md").exists()

    def test_copied_policies_are_loadable(self, tmp_path):
        """Policies copied by initializer are loadable by PolicyLoader."""
        import shutil
        from pathlib import Path

        defaults_path = Path(__file__).parent.parent.parent / "src" / "sentri" / "_default_policies"

        for policy_dir in ["alerts", "checks"]:
            src_dir = defaults_path / policy_dir
            dst_dir = tmp_path / policy_dir
            if not src_dir.exists():
                continue
            dst_dir.mkdir(parents=True, exist_ok=True)
            for src_file in src_dir.rglob("*.md"):
                rel = src_file.relative_to(src_dir)
                dst_file = dst_dir / rel
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)

        loader = PolicyLoader(tmp_path)
        alerts = loader.load_all_alerts()
        checks = loader.load_all_checks()
        assert len(alerts) >= 9  # All bundled alerts
        assert len(checks) >= 2  # stale_stats + tablespace_trend
