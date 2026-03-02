"""Tests for the RagManager — ground truth doc loading and prompt formatting."""

import json

from sentri.rag.manager import (
    DEFAULT_VERSION,
    DocContext,
    RagManager,
    RuleDoc,
    SyntaxDoc,
    _extract_version_from_profile,
    normalize_version,
)

# ---------------------------------------------------------------------------
# Version normalization
# ---------------------------------------------------------------------------


class TestNormalizeVersion:
    def test_full_version_19c(self):
        assert normalize_version("19.12.0.0.0") == "19c"

    def test_full_version_21c(self):
        assert normalize_version("21.3.0.0.0") == "21c"

    def test_full_version_23ai(self):
        assert normalize_version("23.4.0.0.0") == "23ai"

    def test_full_version_12c(self):
        assert normalize_version("12.2.0.1.0") == "12c"

    def test_already_normalized_19c(self):
        assert normalize_version("19c") == "19c"

    def test_already_normalized_23ai(self):
        assert normalize_version("23ai") == "23ai"

    def test_major_only(self):
        assert normalize_version("19") == "19c"

    def test_18c_maps_to_19c(self):
        assert normalize_version("18.0.0.0.0") == "19c"

    def test_11g_maps_to_12c(self):
        assert normalize_version("11.2.0.4.0") == "12c"

    def test_empty_returns_default(self):
        assert normalize_version("") == DEFAULT_VERSION

    def test_none_returns_default(self):
        assert normalize_version(None) == DEFAULT_VERSION

    def test_unknown_returns_default(self):
        assert normalize_version("unknown") == DEFAULT_VERSION

    def test_whitespace_stripped(self):
        assert normalize_version("  19.12.0.0.0  ") == "19c"


# ---------------------------------------------------------------------------
# Extract version from profile JSON
# ---------------------------------------------------------------------------


class TestExtractVersionFromProfile:
    def test_standard_profile(self):
        profile = json.dumps(
            {
                "db_config": {
                    "instance_info": [{"version": "19.12.0.0.0", "status": "OPEN"}],
                }
            }
        )
        assert _extract_version_from_profile(profile) == "19.12.0.0.0"

    def test_missing_instance_info(self):
        profile = json.dumps({"db_config": {}})
        assert _extract_version_from_profile(profile) is None

    def test_empty_instance_info(self):
        profile = json.dumps({"db_config": {"instance_info": []}})
        assert _extract_version_from_profile(profile) is None

    def test_invalid_json(self):
        assert _extract_version_from_profile("not json") is None

    def test_none_input(self):
        assert _extract_version_from_profile(None) is None

    def test_flat_profile(self):
        """Profile stored without db_config wrapper."""
        profile = json.dumps(
            {
                "instance_info": [{"version": "21.3.0.0.0"}],
            }
        )
        assert _extract_version_from_profile(profile) == "21.3.0.0.0"


# ---------------------------------------------------------------------------
# DocContext
# ---------------------------------------------------------------------------


class TestDocContext:
    def test_empty_context(self):
        ctx = DocContext(
            alert_type="test",
            database_id="DB-01",
            oracle_version="19c",
        )
        assert ctx.has_docs is False
        assert ctx.syntax_docs == []
        assert ctx.rule_docs == []

    def test_has_docs_with_syntax(self):
        ctx = DocContext(
            alert_type="test",
            database_id="DB-01",
            oracle_version="19c",
            syntax_docs=[
                SyntaxDoc(
                    path="19c/tablespace/alter_tablespace.md",
                    version="19c",
                    topic="tablespace",
                    operation="alter_tablespace",
                    content="test",
                )
            ],
        )
        assert ctx.has_docs is True

    def test_has_docs_with_rules(self):
        ctx = DocContext(
            alert_type="test",
            database_id="DB-01",
            oracle_version="19c",
            rule_docs=[
                RuleDoc(
                    rule_id="test_rule",
                    severity="HIGH",
                    detection_pattern="",
                    condition="",
                    required_action="",
                )
            ],
        )
        assert ctx.has_docs is True


# ---------------------------------------------------------------------------
# RagManager — version resolution
# ---------------------------------------------------------------------------


class TestVersionResolution:
    def test_from_profile(self, tmp_db, environment_repo, policy_loader, settings):
        """Version resolved from live DatabaseProfile."""
        from sentri.core.models import EnvironmentRecord

        environment_repo.upsert(
            EnvironmentRecord(
                database_id="DB-01",
                database_name="TESTDB",
                environment="DEV",
                connection_string="oracle://test",
            )
        )
        # Store a profile with version info
        profile = json.dumps(
            {
                "db_config": {
                    "instance_info": [{"version": "19.12.0.0.0"}],
                }
            }
        )
        environment_repo.update_profile("DB-01", profile, 1)

        mgr = RagManager(policy_loader, environment_repo, settings)
        version = mgr._resolve_version("DB-01")
        assert version == "19c"

    def test_from_env_record(self, tmp_db, environment_repo, policy_loader, settings):
        """Version resolved from EnvironmentRecord.oracle_version."""
        from sentri.core.models import EnvironmentRecord

        environment_repo.upsert(
            EnvironmentRecord(
                database_id="DB-02",
                database_name="TESTDB2",
                environment="UAT",
                connection_string="oracle://test",
                oracle_version="21c",
            )
        )

        mgr = RagManager(policy_loader, environment_repo, settings)
        version = mgr._resolve_version("DB-02")
        assert version == "21c"

    def test_from_settings(self, tmp_db, policy_loader, settings):
        """Version resolved from DatabaseConfig.oracle_version."""
        from sentri.config.settings import DatabaseConfig

        settings.databases.append(
            DatabaseConfig(
                name="VERSION-TEST-DB",
                connection_string="oracle://test",
                environment="DEV",
                oracle_version="23ai",
            )
        )

        mgr = RagManager(policy_loader, settings=settings)
        version = mgr._resolve_version("VERSION-TEST-DB")
        assert version == "23ai"

    def test_fallback_default(self, policy_loader):
        """No version info → defaults to 19c."""
        mgr = RagManager(policy_loader)
        version = mgr._resolve_version("UNKNOWN-DB")
        assert version == DEFAULT_VERSION


# ---------------------------------------------------------------------------
# RagManager — get_context
# ---------------------------------------------------------------------------


class TestGetContext:
    def test_empty_docs(self, policy_loader):
        """No doc files → empty DocContext."""
        mgr = RagManager(policy_loader)
        ctx = mgr.get_context("tablespace_full", "DB-01")
        assert ctx.alert_type == "tablespace_full"
        assert ctx.database_id == "DB-01"
        assert ctx.oracle_version == DEFAULT_VERSION

    def test_loads_syntax_docs(self, policy_loader):
        """With doc files on disk → syntax_docs populated."""
        # The bundled _default_policies/docs/oracle/19c/ should have docs
        mgr = RagManager(policy_loader)
        ctx = mgr.get_context("tablespace_full", "DB-01")
        # Should find at least the alter_tablespace.md doc
        if ctx.syntax_docs:
            assert any("alter_tablespace" in d.operation for d in ctx.syntax_docs)

    def test_loads_rule_docs(self, policy_loader):
        """Rules applicable to tablespace_full are loaded."""
        mgr = RagManager(policy_loader)
        ctx = mgr.get_context("tablespace_full", "DB-01")
        # Should find the bigfile rule
        if ctx.rule_docs:
            rule_ids = [r.rule_id for r in ctx.rule_docs]
            assert "bigfile_no_add_datafile" in rule_ids


# ---------------------------------------------------------------------------
# RagManager — format_for_prompt
# ---------------------------------------------------------------------------


class TestFormatForPrompt:
    def test_empty_context(self, policy_loader):
        """Empty DocContext → empty string."""
        mgr = RagManager(policy_loader)
        ctx = DocContext(
            alert_type="test",
            database_id="DB-01",
            oracle_version="19c",
        )
        assert mgr.format_for_prompt(ctx) == ""

    def test_with_syntax_docs(self, policy_loader):
        """Syntax docs → formatted text with version header."""
        mgr = RagManager(policy_loader)
        ctx = DocContext(
            alert_type="test",
            database_id="DB-01",
            oracle_version="19c",
            syntax_docs=[
                SyntaxDoc(
                    path="19c/tablespace/alter_tablespace.md",
                    version="19c",
                    topic="tablespace",
                    operation="alter_tablespace",
                    content="ALTER TABLESPACE test syntax here",
                )
            ],
        )
        text = mgr.format_for_prompt(ctx)
        assert "Verified Oracle Syntax Reference (19c)" in text
        assert "Alter Tablespace" in text
        assert "ALTER TABLESPACE test syntax here" in text

    def test_with_rules(self, policy_loader):
        """Rules → Hard Rules section in output."""
        mgr = RagManager(policy_loader)
        ctx = DocContext(
            alert_type="test",
            database_id="DB-01",
            oracle_version="19c",
            rule_docs=[
                RuleDoc(
                    rule_id="bigfile_no_add_datafile",
                    severity="CRITICAL",
                    detection_pattern="(?i)ADD\\s+(DATA)?FILE",
                    condition="tablespace_type == BIGFILE",
                    required_action="Use RESIZE instead",
                )
            ],
        )
        text = mgr.format_for_prompt(ctx)
        assert "Hard Rules" in text
        assert "bigfile_no_add_datafile" in text
        assert "CRITICAL" in text
        assert "RESIZE" in text


# ---------------------------------------------------------------------------
# RagManager — config from Settings
# ---------------------------------------------------------------------------


class TestConfigFromSettings:
    def test_default_config_no_settings(self, policy_loader):
        """No settings → defaults (web_fetch off, validate on)."""
        mgr = RagManager(policy_loader)
        assert mgr._config.enable_web_fetch is False
        assert mgr._config.validate_sql is True
        assert mgr._config.cache_hours == 24

    def test_config_from_settings_object(self, policy_loader):
        """Settings.rag fields map to DocConfig."""
        from sentri.config.settings import RagConfig, Settings

        settings = Settings()
        settings.rag = RagConfig(
            enable_web_fetch=True,
            cache_hours=48,
            validate_sql=False,
            default_version="21c",
        )
        mgr = RagManager(policy_loader, settings=settings)
        assert mgr._config.enable_web_fetch is True
        assert mgr._config.cache_hours == 48
        assert mgr._config.validate_sql is False
        assert mgr._config.default_version == "21c"

    def test_config_from_yaml_dict(self):
        """Settings.load parses rag: section from YAML dict."""
        from sentri.config.settings import Settings

        raw = {
            "rag": {
                "enable_web_fetch": True,
                "cache_hours": 12,
                "validate_sql": False,
                "max_docs_in_prompt": 3,
            }
        }
        settings = Settings._from_dict(raw)
        assert settings.rag.enable_web_fetch is True
        assert settings.rag.cache_hours == 12
        assert settings.rag.validate_sql is False
        assert settings.rag.max_docs_in_prompt == 3

    def test_config_defaults_when_rag_section_missing(self):
        """No rag: section in YAML → all defaults."""
        from sentri.config.settings import Settings

        settings = Settings._from_dict({})
        assert settings.rag.enable_web_fetch is False
        assert settings.rag.validate_sql is True
        assert settings.rag.cache_hours == 24
        assert settings.rag.default_version == "19c"
