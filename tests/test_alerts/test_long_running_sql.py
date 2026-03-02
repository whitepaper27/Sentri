"""Test #9: long_running_sql alert — end-to-end offline tests (NEW)."""

import pytest

from sentri.agents.scout import ScoutAgent
from sentri.policy.alert_patterns import AlertPatterns

ALERT_TYPE = "long_running_sql"


MATCHING_EMAILS = [
    {
        "id": "standard",
        "subject": "Long running SQL session SID=912 running 4 hours on sentri-dev",
        "body": "Long running SQL SID=912 running for 4 hours on database sentri-dev.",
    },
    {
        "id": "alt_format",
        "subject": "Long running query session SID 456 running 3 hrs on PROD-DB-07",
        "body": "A query has been running for 3 hours on database PROD-DB-07.",
    },
    {
        "id": "statement_format",
        "subject": "Running statement alert SID=789 8 hours on DEV-DB-01",
        "body": "Long running SQL statement by session 789, 8 hours on database DEV-DB-01.",
    },
]

NON_MATCHING_EMAILS = [
    {
        "id": "sql_error",
        "subject": "SQL error ORA-00942 on PROD-DB-07",
        "body": "SQL execution failed.",
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
    def test_extracts_session_sid(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Long running SQL session SID=912 running 4 hours on sentri-dev"
        match = pattern.search(text)
        assert match is not None
        assert match.group(1) == "912"

    def test_extracts_running_hours(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Long running SQL session SID=912 running 4 hours on sentri-dev"
        match = pattern.search(text)
        assert match.group(2) == "4"

    def test_extracts_database_id(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        pattern = patterns.get_email_pattern(ALERT_TYPE)
        text = "Long running SQL session SID=912 running 4 hours on sentri-dev"
        match = pattern.search(text)
        assert match.group(3) == "sentri-dev"


class TestWorkflowCreation:
    def test_creates_workflow(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Long running SQL session SID=912 running 4 hours on PROD-DB-07",
            body="Long running SQL SID=912 running for 4 hours on database PROD-DB-07.",
        )
        assert wf_id is not None
        wf = agent_context.workflow_repo.get(wf_id)
        assert wf.alert_type == ALERT_TYPE
        assert wf.status == "DETECTED"
        assert wf.database_id == "PROD-DB-07"

    def test_extracted_data_has_sid_and_hours(self, agent_context):
        scout = ScoutAgent(agent_context)
        scout.load_patterns()
        wf_id = scout.process_raw_email(
            subject="Long running SQL session SID=912 running 4 hours on PROD-DB-07",
            body="Long running SQL SID=912 running for 4 hours on database PROD-DB-07.",
        )
        import json

        wf = agent_context.workflow_repo.get(wf_id)
        suggestion = json.loads(wf.suggestion)
        extracted = suggestion["extracted_data"]
        # Should have session_sid and running_hours (or group_1, group_2)
        has_sid = "session_sid" in extracted or "group_1" in extracted
        has_hours = "running_hours" in extracted or "group_2" in extracted
        assert has_sid, f"Expected session_sid in extracted data: {extracted}"
        assert has_hours, f"Expected running_hours in extracted data: {extracted}"


class TestPolicyMetadata:
    def test_action_type(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_action_type(ALERT_TYPE) == "KILL_SESSION"

    def test_severity(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        assert patterns.get_severity(ALERT_TYPE) == "MEDIUM"

    def test_has_forward_action(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_forward_action(ALERT_TYPE)
        assert "KILL SESSION" in sql.upper()

    def test_has_verification_query(self, policy_loader):
        patterns = AlertPatterns(policy_loader)
        sql = patterns.get_verification_query(ALERT_TYPE)
        assert "SELECT" in sql.upper()
