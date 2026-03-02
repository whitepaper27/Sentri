"""Test #3: archive_dest_full alert — end-to-end offline tests."""

import pytest

from sentri.agents.scout import ScoutAgent
from sentri.policy.alert_patterns import AlertPatterns

ALERT_TYPE = "archive_dest_full"


MATCHING_EMAILS = [
    {
        "id": "standard",
        "subject": "Archive log destination LOG_ARCHIVE_DEST_1 95% full on sentri-dev",
        "body": "Archive destination LOG_ARCHIVE_DEST_1 95% full on database sentri-dev.",
    },
    {
        "id": "alt_format",
        "subject": "CRITICAL: Archive destination /u01/archivelog 95% full on DEV-DB-01",
        "body": "Archive log destination is running out of space on database DEV-DB-01.",
    },
    {
        "id": "dest_used",
        "subject": "Archive dest FRA 89% used on PROD-DB-07",
        "body": "Archive log dest FRA at 89% capacity on database PROD-DB-07.",
    },
]

NON_MATCHING_EMAILS = [
    {
        "id": "archive_gap",
        "subject": "Archive gap detected on DEV-DB-01",
        "body": "Gap in sequence.",
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
    def test_extracts_dest_name(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Archive log destination LOG_ARCHIVE_DEST_1 95% full on sentri-dev"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "LOG_ARCHIVE_DEST_1"

    def test_extracts_percent(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Archive log destination LOG_ARCHIVE_DEST_1 95% full on sentri-dev"
        match = pattern.search(text)
        assert match.group(2) == "95"

    def test_extracts_database_id(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Archive log destination LOG_ARCHIVE_DEST_1 95% full on sentri-dev"
        match = pattern.search(text)
        assert match.group(3) == "sentri-dev"


class TestWorkflowCreation:
    def test_creates_workflow(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Archive destination /u01/archivelog 95% full on DEV-DB-01",
            body="Archive log destination is running out of space on database DEV-DB-01.",
        )
        assert wf_id is not None
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.alert_type == ALERT_TYPE
        assert wf.status == "DETECTED"


class TestPolicyMetadata:
    def test_action_type(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_action_type(ALERT_TYPE) == "DELETE_ARCHIVES"

    def test_severity(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_severity(ALERT_TYPE) == "CRITICAL"

    def test_has_forward_action(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_forward_action(ALERT_TYPE)
        assert "CROSSCHECK" in sql.upper() or "DELETE" in sql.upper()

    def test_has_verification_query(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_verification_query(ALERT_TYPE)
        assert "SELECT" in sql.upper()
