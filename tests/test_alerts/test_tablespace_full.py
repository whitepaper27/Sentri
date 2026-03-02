"""Test #1: tablespace_full alert — end-to-end offline tests."""

import pytest

from sentri.agents.scout import ScoutAgent
from sentri.policy.alert_patterns import AlertPatterns

ALERT_TYPE = "tablespace_full"


# --- Email variations that SHOULD match ---

MATCHING_EMAILS = [
    {
        "id": "standard",
        "subject": "Tablespace USERS_TEST 92% full on sentri-dev",
        "body": "Tablespace USERS_TEST is 92% full on database sentri-dev.",
    },
    {
        "id": "prod_format",
        "subject": "ALERT: Tablespace USERS 92% full on PROD-DB-07",
        "body": "The tablespace USERS on database PROD-DB-07 has reached 92% capacity.",
    },
    {
        "id": "different_pct",
        "subject": "Tablespace SYSAUX 78.5% used on DEV-DB-01",
        "body": "Tablespace SYSAUX 78.5% capacity on database DEV-DB-01.",
    },
]

# --- Email that should NOT match ---

NON_MATCHING_EMAILS = [
    {
        "id": "temp_tablespace",
        "subject": "Temporary tablespace TEMP 88% full on sentri-dev",
        "body": "Temp tablespace is full.",
    },
    {
        "id": "unrelated",
        "subject": "Weekly DBA Report",
        "body": "No issues found.",
    },
]


class TestRegexMatching:
    """Test that the regex pattern matches expected email formats."""

    def test_pattern_loads(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        assert pattern is not None

    @pytest.mark.parametrize("email", MATCHING_EMAILS, ids=[e["id"] for e in MATCHING_EMAILS])
    def test_matches_valid_emails(self, policy_loader, email):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        full_text = f"{email['subject']}\n{email['body']}"
        match = pattern.search(full_text)
        assert match is not None, f"Pattern should match: {email['subject']}"

    @pytest.mark.parametrize(
        "email", NON_MATCHING_EMAILS, ids=[e["id"] for e in NON_MATCHING_EMAILS]
    )
    def test_rejects_non_matching(self, policy_loader, email):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        full_text = f"{email['subject']}\n{email['body']}"
        match = pattern.search(full_text)
        assert match is None, f"Pattern should NOT match: {email['subject']}"


class TestFieldExtraction:
    """Test that regex groups extract the correct fields."""

    def test_extracts_tablespace_name(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Tablespace USERS_TEST 92% full on sentri-dev"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "USERS_TEST"

    def test_extracts_percent(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Tablespace USERS_TEST 92% full on sentri-dev"
        match = pattern.search(text)
        assert match.group(2) == "92"

    def test_extracts_database_id(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Tablespace USERS_TEST 92% full on sentri-dev"
        match = pattern.search(text)
        assert match.group(3) == "sentri-dev"


class TestWorkflowCreation:
    """Test Scout creates a workflow from this alert email."""

    def test_creates_workflow(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Tablespace USERS 92% full on PROD-DB-07",
            body="The tablespace USERS on database PROD-DB-07 has reached 92% capacity.",
        )
        assert wf_id is not None
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.alert_type == ALERT_TYPE
        assert wf.status == "DETECTED"
        assert wf.database_id == "PROD-DB-07"

    def test_extracted_data_in_suggestion(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Tablespace USERS 92% full on PROD-DB-07",
            body="The tablespace USERS on database PROD-DB-07 has reached 92% capacity.",
        )
        import json

        wf = agent_context.workflow_repo.get(wf_id)
        suggestion = json.loads(wf.suggestion)
        extracted = suggestion["extracted_data"]
        assert "tablespace_name" in extracted or "group_1" in extracted


class TestPolicyMetadata:
    """Test that policy .md file provides correct metadata."""

    def test_action_type(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_action_type(ALERT_TYPE) == "ADD_DATAFILE"

    def test_severity(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_severity(ALERT_TYPE) == "HIGH"

    def test_has_forward_action(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_forward_action(ALERT_TYPE)
        assert "ADD DATAFILE" in sql.upper()

    def test_has_rollback_action(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_rollback_action(ALERT_TYPE)
        assert "DROP DATAFILE" in sql.upper()

    def test_has_verification_query(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_verification_query(ALERT_TYPE)
        assert "SELECT" in sql.upper()

    def test_risk_level(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        risk = patterns.get_risk_level(ALERT_TYPE)
        # risk_level section may include description text after the level keyword
        assert risk.startswith(("LOW", "MEDIUM", "HIGH", "CRITICAL"))
