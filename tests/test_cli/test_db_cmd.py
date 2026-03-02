"""Tests for sentri db commands, alias resolution, and registry sync."""

from click.testing import CliRunner

from sentri.cli.main import cli
from sentri.config.settings import DatabaseConfig, Settings
from sentri.db.connection import Database
from sentri.db.environment_repo import EnvironmentRepository


def _make_settings():
    """Create test settings with multiple databases and aliases."""
    s = Settings()
    s.databases = [
        DatabaseConfig(
            name="DEV-DB-01",
            connection_string="oracle://sentri_agent@dev-db-01:1521/DEVDB",
            environment="DEV",
            username="sentri_admin",
            aliases=["DEVDB", "dev-db-01"],
        ),
        DatabaseConfig(
            name="PROD-DB-07",
            connection_string="oracle://sentri_agent@prod-scan:1521/PRODDB",
            environment="PROD",
            username="sentri_ro",
            aliases=["PRODDB", "prod-db-07", "PROD07"],
        ),
    ]
    return s


class TestAliasResolution:
    """Test Settings.resolve_database() alias matching."""

    def test_exact_name_match(self):
        s = _make_settings()
        db = s.resolve_database("DEV-DB-01")
        assert db is not None
        assert db.name == "DEV-DB-01"

    def test_alias_match(self):
        s = _make_settings()
        db = s.resolve_database("PRODDB")
        assert db is not None
        assert db.name == "PROD-DB-07"

    def test_alias_case_insensitive(self):
        s = _make_settings()
        db = s.resolve_database("proddb")
        assert db is not None
        assert db.name == "PROD-DB-07"

    def test_alias_not_found(self):
        s = _make_settings()
        db = s.resolve_database("UNKNOWN-DB")
        assert db is None

    def test_get_database_exact(self):
        s = _make_settings()
        db = s.get_database("PROD-DB-07")
        assert db is not None
        assert db.username == "sentri_ro"

    def test_get_database_not_found(self):
        s = _make_settings()
        assert s.get_database("NONEXISTENT") is None

    def test_multiple_aliases(self):
        s = _make_settings()
        # All aliases should resolve to the same DB
        for alias in ["PRODDB", "prod-db-07", "PROD07"]:
            db = s.resolve_database(alias)
            assert db is not None, f"Alias '{alias}' not resolved"
            assert db.name == "PROD-DB-07"


class TestEnvironmentRegistrySync:
    """Test that YAML config syncs to environment_registry table."""

    def test_sync_populates_registry(self, tmp_path):
        from sentri.cli.start_cmd import _sync_environment_registry

        db_path = tmp_path / "test.db"
        db = Database(db_path)
        db.initialize_schema()
        env_repo = EnvironmentRepository(db)
        settings = _make_settings()

        _sync_environment_registry(settings, env_repo)

        # Both databases should be in the registry
        all_envs = env_repo.list_all()
        assert len(all_envs) == 2

        dev = env_repo.get("DEV-DB-01")
        assert dev is not None
        assert dev.environment == "DEV"
        assert dev.connection_string == "oracle://sentri_agent@dev-db-01:1521/DEVDB"

        prod = env_repo.get("PROD-DB-07")
        assert prod is not None
        assert prod.environment == "PROD"

        db.close()

    def test_sync_sets_default_autonomy(self, tmp_path):
        from sentri.cli.start_cmd import _sync_environment_registry

        db_path = tmp_path / "test.db"
        db = Database(db_path)
        db.initialize_schema()
        env_repo = EnvironmentRepository(db)
        settings = _make_settings()

        _sync_environment_registry(settings, env_repo)

        dev = env_repo.get("DEV-DB-01")
        assert dev.autonomy_level == "AUTONOMOUS"

        prod = env_repo.get("PROD-DB-07")
        assert prod.autonomy_level == "ADVISORY"

        db.close()

    def test_sync_upsert_updates_existing(self, tmp_path):
        from sentri.cli.start_cmd import _sync_environment_registry

        db_path = tmp_path / "test.db"
        db = Database(db_path)
        db.initialize_schema()
        env_repo = EnvironmentRepository(db)
        settings = _make_settings()

        # First sync
        _sync_environment_registry(settings, env_repo)
        assert len(env_repo.list_all()) == 2

        # Second sync (should upsert, not duplicate)
        _sync_environment_registry(settings, env_repo)
        assert len(env_repo.list_all()) == 2

        db.close()


class TestUsernameOverride:
    """Test per-DB username support in connection pool."""

    def test_parse_connection_string_extracts_user(self):
        from sentri.oracle.connection_pool import OracleConnectionPool

        user, dsn = OracleConnectionPool._parse_connection_string("oracle://myuser@host:1521/SVC")
        assert user == "myuser"
        assert dsn == "host:1521/SVC"

    def test_parse_connection_string_default_user(self):
        from sentri.oracle.connection_pool import OracleConnectionPool

        user, dsn = OracleConnectionPool._parse_connection_string("host:1521/SVC")
        assert user == "sentri_agent"
        assert dsn == "host:1521/SVC"


class TestDatabaseConfigParsing:
    """Test YAML parsing of new DatabaseConfig fields."""

    def test_from_dict_with_aliases(self):
        raw = {
            "databases": [
                {
                    "name": "TEST-DB",
                    "connection_string": "oracle://user@host:1521/SVC",
                    "environment": "DEV",
                    "username": "custom_user",
                    "aliases": ["TESTDB", "test-db"],
                    "autonomy_level": "AUTONOMOUS",
                    "oracle_version": "19c",
                    "architecture": "RAC",
                    "critical_schemas": "FINANCE,HR",
                }
            ]
        }
        s = Settings._from_dict(raw)
        assert len(s.databases) == 1
        db = s.databases[0]
        assert db.name == "TEST-DB"
        assert db.username == "custom_user"
        assert db.aliases == ["TESTDB", "test-db"]
        assert db.autonomy_level == "AUTONOMOUS"
        assert db.oracle_version == "19c"
        assert db.architecture == "RAC"
        assert db.critical_schemas == "FINANCE,HR"

    def test_from_dict_aliases_as_string(self):
        """Support comma-separated aliases as a string."""
        raw = {
            "databases": [
                {
                    "name": "TEST-DB",
                    "connection_string": "oracle://user@host:1521/SVC",
                    "environment": "DEV",
                    "aliases": "TESTDB, test-db",
                }
            ]
        }
        s = Settings._from_dict(raw)
        assert s.databases[0].aliases == ["TESTDB", "test-db"]

    def test_from_dict_defaults(self):
        """Missing optional fields should use defaults."""
        raw = {
            "databases": [
                {
                    "name": "SIMPLE-DB",
                    "connection_string": "oracle://user@host:1521/SVC",
                    "environment": "PROD",
                }
            ]
        }
        s = Settings._from_dict(raw)
        db = s.databases[0]
        assert db.username == ""
        assert db.aliases == []
        assert db.autonomy_level == ""
        assert db.architecture == "STANDALONE"


class TestDbListCommand:
    """Test sentri db list CLI command."""

    def test_db_list_no_config(self, tmp_path, monkeypatch):
        """db list with no config shows 'no databases' message."""
        import sentri.config.settings as settings_mod

        config_path = tmp_path / "sentri.yaml"
        monkeypatch.setattr(settings_mod, "CONFIG_PATH", config_path)

        runner = CliRunner()
        result = runner.invoke(cli, ["db", "list"])
        assert result.exit_code == 0
        assert "No databases configured" in result.output
