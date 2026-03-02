"""Test #6: session_blocker alert — end-to-end offline tests (NEW)."""

import pytest

from sentri.agents.scout import ScoutAgent
from sentri.policy.alert_patterns import AlertPatterns

ALERT_TYPE = "session_blocker"


MATCHING_EMAILS = [
    {
        "id": "standard",
        "subject": "Blocking session detected SID=847 on sentri-dev",
        "body": "Blocker session SID: 847 on database sentri-dev, blocking 12 sessions.",
    },
    {
        "id": "alt_format",
        "subject": "Session blocker alert SID=123 on PROD-DB-07",
        "body": "Blocking session detected, session 123 is blocking others on database PROD-DB-07.",
    },
    {
        "id": "chain_format",
        "subject": "Blocker chain detected session SID 456 on DEV-DB-01",
        "body": "A blocking chain has been detected with SID 456 on database DEV-DB-01.",
    },
]

NON_MATCHING_EMAILS = [
    {
        "id": "session_killed",
        "subject": "Session SID=847 killed successfully on sentri-dev",
        "body": "Session has been terminated.",
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
    def test_extracts_blocking_sid(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Blocking session detected SID=847 on sentri-dev"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "847"

    def test_extracts_database_id(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Blocking session detected SID=847 on sentri-dev"
        match = pattern.search(text)
        assert match.group(2) == "sentri-dev"


class TestWorkflowCreation:
    def test_creates_workflow(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Blocking session detected SID=847 on PROD-DB-07",
            body="Blocker session SID: 847 on database PROD-DB-07, blocking 12 sessions.",
        )
        assert wf_id is not None
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.alert_type == ALERT_TYPE
        assert wf.status == "DETECTED"
        assert wf.database_id == "PROD-DB-07"

    def test_extracted_data_has_sid(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Blocking session detected SID=847 on PROD-DB-07",
            body="Blocker session SID: 847 on database PROD-DB-07.",
        )
        import json

        wf = agent_context.workflow_repo.get(wf_id)
        suggestion = json.loads(wf.suggestion)
        extracted = suggestion["extracted_data"]
        assert "blocking_sid" in extracted or "group_1" in extracted


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
