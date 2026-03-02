"""Test #7: cpu_high alert — end-to-end offline tests (NEW)."""

import pytest

from sentri.agents.scout import ScoutAgent
from sentri.policy.alert_patterns import AlertPatterns

ALERT_TYPE = "cpu_high"


MATCHING_EMAILS = [
    {
        "id": "standard",
        "subject": "High CPU utilization 97% on sentri-dev",
        "body": "CPU utilization 97% on database sentri-dev.",
    },
    {
        "id": "alt_format",
        "subject": "CPU usage alert 95.5% on PROD-DB-07",
        "body": "High CPU usage at 95.5% on database PROD-DB-07.",
    },
    {
        "id": "high_cpu",
        "subject": "High CPU alert 88% on DEV-DB-01",
        "body": "CPU utilization has been above 85% for 10 minutes on database DEV-DB-01.",
    },
]

NON_MATCHING_EMAILS = [
    {
        "id": "memory_alert",
        "subject": "High memory usage 92% on PROD-DB-07",
        "body": "Memory is running high.",
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
    def test_extracts_cpu_percent(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "High CPU utilization 97% on sentri-dev"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "97"

    def test_extracts_database_id(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "High CPU utilization 97% on sentri-dev"
        match = pattern.search(text)
        assert match.group(2) == "sentri-dev"


class TestWorkflowCreation:
    def test_creates_workflow(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="High CPU utilization 97% on PROD-DB-07",
            body="CPU utilization 97% on database PROD-DB-07.",
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
