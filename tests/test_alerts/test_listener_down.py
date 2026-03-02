"""Test #4: listener_down alert — end-to-end offline tests."""

import pytest

from sentri.agents.scout import ScoutAgent
from sentri.policy.alert_patterns import AlertPatterns

ALERT_TYPE = "listener_down"


MATCHING_EMAILS = [
    {
        "id": "standard",
        "subject": "Listener LISTENER down on sentri-dev",
        "body": "Listener LISTENER is down on database sentri-dev.",
    },
    {
        "id": "critical_format",
        "subject": "CRITICAL: Listener LISTENER down on PROD-DB-07",
        "body": "TNS listener is not running on the production server database PROD-DB-07.",
    },
    {
        "id": "named_listener",
        "subject": "Listener LISTENER_SCAN down on UAT-DB-03",
        "body": "The listener LISTENER_SCAN has stopped on database UAT-DB-03.",
    },
]

NON_MATCHING_EMAILS = [
    {
        "id": "listener_warning",
        "subject": "Listener connection count high on PROD-DB-07",
        "body": "Listener has 500 active connections.",
    },
    {
        "id": "unrelated",
        "subject": "Tablespace USERS 92% full on PROD-DB-07",
        "body": "Regular tablespace alert.",
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
    def test_extracts_listener_name(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Listener LISTENER down on sentri-dev"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "LISTENER"

    def test_extracts_database_id(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Listener LISTENER down on sentri-dev"
        match = pattern.search(text)
        assert match.group(2) == "sentri-dev"


class TestWorkflowCreation:
    def test_creates_workflow(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Listener LISTENER down on PROD-DB-07",
            body="TNS listener is not running on database PROD-DB-07.",
        )
        assert wf_id is not None
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.alert_type == ALERT_TYPE
        assert wf.status == "DETECTED"
        assert wf.database_id == "PROD-DB-07"


class TestPolicyMetadata:
    def test_action_type(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_action_type(ALERT_TYPE) == "START_LISTENER"

    def test_severity(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_severity(ALERT_TYPE) == "CRITICAL"

    def test_has_verification_query(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_verification_query(ALERT_TYPE)
        # listener_down uses tnsping (OS command), not a SQL SELECT
        assert len(sql) > 0, "Should have a verification query or command"
