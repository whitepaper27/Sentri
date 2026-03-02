"""Test #5: archive_gap alert — end-to-end offline tests."""

import pytest

from sentri.agents.scout import ScoutAgent
from sentri.policy.alert_patterns import AlertPatterns

ALERT_TYPE = "archive_gap"


MATCHING_EMAILS = [
    {
        "id": "standard",
        "subject": "Archive gap detected sequence 1045-1052 on sentri-dev",
        "body": "Gap in archive log sequence detected on database sentri-dev.",
    },
    {
        "id": "alt_format",
        "subject": "ALERT: Archive gap detected on DEV-DB-01",
        "body": "Gap in archive log sequence detected between threads on database DEV-DB-01.",
    },
    {
        "id": "prod_format",
        "subject": "CRITICAL: Archive gap detected on standby PROD-DB-07",
        "body": "Archive log gap found on database PROD-DB-07.",
    },
]

NON_MATCHING_EMAILS = [
    {
        "id": "archive_dest",
        "subject": "Archive destination LOG_ARCHIVE_DEST_1 95% full on DEV-DB-01",
        "body": "Archive destination full.",
    },
    {
        "id": "unrelated",
        "subject": "Database backup completed",
        "body": "All good.",
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


class TestWorkflowCreation:
    def test_creates_workflow(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Archive gap detected on DEV-DB-01",
            body="Gap in archive log sequence detected between threads on database DEV-DB-01.",
        )
        assert wf_id is not None
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.alert_type == ALERT_TYPE
        assert wf.status == "DETECTED"


class TestPolicyMetadata:
    def test_action_type(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_action_type(ALERT_TYPE) == "RESOLVE_GAP"

    def test_severity(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_severity(ALERT_TYPE) == "HIGH"

    def test_has_verification_query(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_verification_query(ALERT_TYPE)
        assert "SELECT" in sql.upper()
