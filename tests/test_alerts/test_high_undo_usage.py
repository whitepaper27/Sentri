"""Test #8: high_undo_usage alert — end-to-end offline tests (NEW)."""

import pytest

from sentri.agents.scout import ScoutAgent
from sentri.policy.alert_patterns import AlertPatterns

ALERT_TYPE = "high_undo_usage"


MATCHING_EMAILS = [
    {
        "id": "standard",
        "subject": "High undo usage 91% on sentri-dev",
        "body": "Undo usage 91% on database sentri-dev.",
    },
    {
        "id": "alt_format",
        "subject": "Undo tablespace usage alert 88% on PROD-DB-07",
        "body": "High undo tablespace utilization at 88% on database PROD-DB-07.",
    },
    {
        "id": "undo_full",
        "subject": "High undo full 95.2% on DEV-DB-01",
        "body": "Undo tablespace near capacity on database DEV-DB-01.",
    },
]

NON_MATCHING_EMAILS = [
    {
        "id": "tablespace_full",
        "subject": "Tablespace USERS 92% full on PROD-DB-07",
        "body": "Regular tablespace alert.",
    },
    {
        "id": "unrelated",
        "subject": "Backup completed successfully",
        "body": "No issues.",
    },
]


class TestRegexMatching:
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
    def test_extracts_undo_percent(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "High undo usage 91% on sentri-dev"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "91"

    def test_extracts_database_id(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "High undo usage 91% on sentri-dev"
        match = pattern.search(text)
        assert match.group(2) == "sentri-dev"


class TestWorkflowCreation:
    def test_creates_workflow(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="High undo usage 91% on PROD-DB-07",
            body="Undo usage 91% on database PROD-DB-07.",
        )
        assert wf_id is not None
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.alert_type == ALERT_TYPE
        assert wf.status == "DETECTED"
        assert wf.database_id == "PROD-DB-07"


class TestPolicyMetadata:
    def test_action_type(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_action_type(ALERT_TYPE) == "KILL_SESSION"

    def test_severity(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_severity(ALERT_TYPE) == "HIGH"

    def test_has_forward_action(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_forward_action(ALERT_TYPE)
        assert "KILL SESSION" in sql.upper()

    def test_has_verification_query(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_verification_query(ALERT_TYPE)
        assert "SELECT" in sql.upper()
