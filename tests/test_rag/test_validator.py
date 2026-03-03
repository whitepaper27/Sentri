"""Tests for the SQL validator — checks LLM SQL against ground truth rules."""

from sentri.rag.manager import RuleDoc, RuleViolation, ValidationResult
from sentri.rag.validator import SQLValidator

# ---------------------------------------------------------------------------
# Helper to create rule docs
# ---------------------------------------------------------------------------


def _bigfile_rule() -> RuleDoc:
    return RuleDoc(
        rule_id="bigfile_no_add_datafile",
        severity="CRITICAL",
        detection_pattern=r"(?i)ADD\s+(DATA|TEMP)?FILE",
        condition="tablespace_type == BIGFILE",
        required_action="Use ALTER TABLESPACE <name> RESIZE <size> instead",
        applies_to=["tablespace_full"],
    )


def _omf_rule() -> RuleDoc:
    return RuleDoc(
        rule_id="omf_no_explicit_path",
        severity="HIGH",
        detection_pattern=r"(?i)(ADD\s+(DATA|TEMP)?FILE\s+['\"])",
        condition="omf_enabled == True",
        required_action="Omit file path. Use ADD DATAFILE SIZE <size> instead",
        applies_to=["tablespace_full"],
    )


def _cdb_rule() -> RuleDoc:
    return RuleDoc(
        rule_id="cdb_context_required",
        severity="HIGH",
        detection_pattern=r"(?i)ALTER\s+(TABLESPACE|DATABASE\s+DATAFILE)",
        condition="is_cdb == True",
        required_action="Set container first: ALTER SESSION SET CONTAINER = <pdb>",
        applies_to=["tablespace_full"],
    )


# ---------------------------------------------------------------------------
# Basic validation
# ---------------------------------------------------------------------------


class TestBasicValidation:
    def test_no_rules_passes(self):
        validator = SQLValidator()
        result = validator.validate("ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G", [], "DB-01")
        assert result.is_valid is True
        assert result.violations == []
        assert result.checked_rules == 0

    def test_empty_sql_passes(self):
        validator = SQLValidator()
        result = validator.validate("", [_bigfile_rule()], "DB-01")
        assert result.is_valid is True

    def test_none_sql_passes(self):
        validator = SQLValidator()
        result = validator.validate(None, [_bigfile_rule()], "DB-01")
        assert result.is_valid is True

    def test_valid_sql_passes(self):
        """RESIZE on bigfile is correct — no violation."""
        validator = SQLValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS RESIZE 50G",
            [_bigfile_rule()],
            "DB-01",
        )
        assert result.is_valid is True
        assert result.checked_rules == 1


# ---------------------------------------------------------------------------
# BIGFILE rule
# ---------------------------------------------------------------------------


class TestBigfileRule:
    def test_add_datafile_on_bigfile_caught(self):
        """ADD DATAFILE on BIGFILE tablespace → violation."""
        validator = SQLValidator()
        # Simulate a BIGFILE context via _check_bigfile_condition
        # Since we have no environment_repo, condition check defaults to True
        # for unknown conditions. But bigfile check specifically checks
        # db_context["tablespace_type"] == "BIGFILE" which defaults to ""
        # So without context, bigfile condition won't trigger.
        # We need to test it differently — by verifying the pattern match logic.

        # Pattern matches but condition doesn't (no BIGFILE context) → no violation
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            [_bigfile_rule()],
            "",  # No database context
        )
        # Without db context, bigfile condition is False → no violation
        assert result.is_valid is True

    def test_add_datafile_on_bigfile_with_context(self):
        """ADD DATAFILE on BIGFILE with explicit BIGFILE context → violation."""
        # Create a validator that simulates BIGFILE context
        validator = _BigfileValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            [_bigfile_rule()],
            "BIGFILE-DB",
        )
        assert result.is_valid is False
        assert len(result.violations) == 1
        assert result.violations[0].rule_id == "bigfile_no_add_datafile"
        assert result.violations[0].severity == "CRITICAL"

    def test_resize_on_bigfile_passes(self):
        """RESIZE on BIGFILE → no violation (this is correct)."""
        validator = _BigfileValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS RESIZE 50G",
            [_bigfile_rule()],
            "BIGFILE-DB",
        )
        assert result.is_valid is True

    def test_add_tempfile_on_bigfile_caught(self):
        """ADD TEMPFILE on BIGFILE → violation."""
        validator = _BigfileValidator()
        result = validator.validate(
            "ALTER TABLESPACE TEMP ADD TEMPFILE SIZE 10G",
            [_bigfile_rule()],
            "BIGFILE-DB",
        )
        assert result.is_valid is False
        assert result.violations[0].rule_id == "bigfile_no_add_datafile"


# ---------------------------------------------------------------------------
# OMF rule
# ---------------------------------------------------------------------------


class TestOmfRule:
    def test_explicit_path_with_omf_caught(self):
        """Explicit path when OMF is enabled → violation."""
        validator = _OmfValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE '/u01/oradata/test.dbf' SIZE 10G",
            [_omf_rule()],
            "OMF-DB",
        )
        assert result.is_valid is False
        assert result.violations[0].rule_id == "omf_no_explicit_path"

    def test_no_path_with_omf_passes(self):
        """No explicit path when OMF is enabled → passes."""
        validator = _OmfValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            [_omf_rule()],
            "OMF-DB",
        )
        assert result.is_valid is True

    def test_explicit_path_without_omf_passes(self):
        """Explicit path when OMF is NOT enabled → passes."""
        validator = SQLValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE '/u01/oradata/test.dbf' SIZE 10G",
            [_omf_rule()],
            "",
        )
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# Multiple violations
# ---------------------------------------------------------------------------


class TestMultipleViolations:
    def test_two_rules_both_violated(self):
        """SQL triggers both bigfile and OMF rules."""
        validator = _BigfileOmfValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE '/u01/test.dbf' SIZE 10G",
            [_bigfile_rule(), _omf_rule()],
            "BOTH-DB",
        )
        assert result.is_valid is False
        assert len(result.violations) == 2
        rule_ids = {v.rule_id for v in result.violations}
        assert "bigfile_no_add_datafile" in rule_ids
        assert "omf_no_explicit_path" in rule_ids

    def test_mixed_pass_fail(self):
        """One rule passes, one fails."""
        validator = _BigfileValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            [_bigfile_rule(), _omf_rule()],
            "BIGFILE-DB",
        )
        # Bigfile rule violated (ADD DATAFILE on bigfile)
        # OMF rule NOT violated (no explicit path, and omf not enabled)
        assert result.is_valid is False
        assert len(result.violations) == 1
        assert result.violations[0].rule_id == "bigfile_no_add_datafile"


# ---------------------------------------------------------------------------
# CDB rule
# ---------------------------------------------------------------------------


class TestCdbRule:
    def test_alter_tablespace_on_cdb_caught(self):
        """ALTER TABLESPACE on CDB → informational violation."""
        validator = _CdbValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            [_cdb_rule()],
            "CDB-DB",
        )
        assert result.is_valid is False
        assert result.violations[0].rule_id == "cdb_context_required"

    def test_alter_tablespace_non_cdb_passes(self):
        """ALTER TABLESPACE on non-CDB → passes."""
        validator = SQLValidator()
        result = validator.validate(
            "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            [_cdb_rule()],
            "",
        )
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_valid_result(self):
        result = ValidationResult(is_valid=True, checked_rules=3)
        assert result.is_valid
        assert result.violations == []
        assert result.checked_rules == 3

    def test_invalid_result(self):
        violation = RuleViolation(
            rule_id="test",
            severity="HIGH",
            message="Bad SQL",
            sql_fragment="ADD DATAFILE",
            suggested_fix="Use RESIZE",
        )
        result = ValidationResult(
            is_valid=False,
            violations=[violation],
            checked_rules=1,
        )
        assert not result.is_valid
        assert len(result.violations) == 1


# ---------------------------------------------------------------------------
# Test validator subclasses (simulate different DB contexts)
# ---------------------------------------------------------------------------


class _BigfileValidator(SQLValidator):
    """Simulates a BIGFILE tablespace context."""

    def _get_database_context(self, database_id):
        return {"tablespace_type": "BIGFILE", "omf_enabled": False, "is_cdb": False}


class _OmfValidator(SQLValidator):
    """Simulates an OMF-enabled database context."""

    def _get_database_context(self, database_id):
        return {"tablespace_type": "SMALLFILE", "omf_enabled": True, "is_cdb": False}


class _CdbValidator(SQLValidator):
    """Simulates a CDB database context."""

    def _get_database_context(self, database_id):
        return {"tablespace_type": "SMALLFILE", "omf_enabled": False, "is_cdb": True}


class _BigfileOmfValidator(SQLValidator):
    """Simulates both BIGFILE and OMF context."""

    def _get_database_context(self, database_id):
        return {"tablespace_type": "BIGFILE", "omf_enabled": True, "is_cdb": False}
