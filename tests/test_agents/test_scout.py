"""Test Scout agent email parsing and pattern matching."""

import pytest

from sentri.agents.scout import ScoutAgent

SAMPLE_EMAILS = [
    {
        "subject": "ALERT: Tablespace USERS 92% full on PROD-DB-07",
        "body": "The tablespace USERS on database PROD-DB-07 has reached 92% capacity.",
        "expected_type": "tablespace_full",
        "expected_db": "PROD-DB-07",
    },
    {
        "subject": "CRITICAL: Archive destination /u01/archivelog 95% full on DEV-DB-01",
        "body": "Archive log destination is running out of space.",
        "expected_type": "archive_dest_full",
        "expected_db": "DEV-DB-01",
    },
    {
        "subject": "ALERT: Temp tablespace TEMP 88% full on UAT-DB-03",
        "body": "Temporary tablespace is approaching capacity.",
        "expected_type": "temp_full",
        "expected_db": "UAT-DB-03",
    },
    {
        "subject": "CRITICAL: Listener LISTENER down on PROD-DB-07",
        "body": "TNS listener is not running on the production server.",
        "expected_type": "listener_down",
        "expected_db": "PROD-DB-07",
    },
    {
        "subject": "ALERT: Archive gap detected on DEV-DB-01",
        "body": "Gap in archive log sequence detected between threads.",
        "expected_type": "archive_gap",
        "expected_db": "DEV-DB-01",
    },
]


def test_scout_creates_workflow(agent_context):
    """Test that Scout can parse an email and create a workflow."""
    scout = ScoutAgent(agent_context)
    scout.load_patterns()

    wf_id = scout.process_raw_email(
        subject="ALERT: Tablespace USERS 92% full on PROD-DB-07",
        body="The tablespace USERS on database PROD-DB-07 has reached 92% capacity.",
    )

    assert wf_id is not None
    wf = agent_context.workflow_repo.get(wf_id)
    assert wf is not None
    assert wf.status == "DETECTED"
    assert wf.alert_type == "tablespace_full"


def test_scout_no_match_creates_unknown(agent_context):
    """Test that unrecognized emails create an 'unknown' alert workflow."""
    scout = ScoutAgent(agent_context)
    scout.load_patterns()

    wf_id = scout.process_raw_email(
        subject="Weekly DBA Report",
        body="Here is the weekly summary of database operations.",
    )
    assert wf_id is not None
    wf = agent_context.workflow_repo.get(wf_id)
    assert wf.alert_type == "unknown"
    assert wf.database_id == "UNKNOWN"


@pytest.mark.parametrize(
    "email_data", SAMPLE_EMAILS, ids=[e["expected_type"] for e in SAMPLE_EMAILS]
)
def test_scout_pattern_matching(agent_context, email_data):
    """Test all 5 alert patterns are matched correctly."""
    scout = ScoutAgent(agent_context)
    scout.load_patterns()

    wf_id = scout.process_raw_email(
        subject=email_data["subject"],
        body=email_data["body"],
    )

    assert wf_id is not None, f"Failed to match: {email_data['subject']}"
    wf = agent_context.workflow_repo.get(wf_id)
    assert wf.alert_type == email_data["expected_type"]
