"""Test workflow repository CRUD operations."""

from sentri.core.constants import WorkflowStatus
from sentri.core.models import Workflow


def test_create_and_get(workflow_repo):
    wf = Workflow(
        alert_type="tablespace_full",
        database_id="DEV-DB-01",
        environment="DEV",
        status=WorkflowStatus.DETECTED.value,
    )
    wf_id = workflow_repo.create(wf)
    assert wf_id == wf.id

    fetched = workflow_repo.get(wf_id)
    assert fetched is not None
    assert fetched.alert_type == "tablespace_full"
    assert fetched.database_id == "DEV-DB-01"
    assert fetched.status == "DETECTED"


def test_update_status(workflow_repo):
    wf = Workflow(alert_type="temp_full", database_id="UAT-DB-03", environment="UAT")
    workflow_repo.create(wf)

    workflow_repo.update_status(wf.id, "VERIFIED", verification='{"is_valid": true}')

    fetched = workflow_repo.get(wf.id)
    assert fetched.status == "VERIFIED"
    assert fetched.verification == '{"is_valid": true}'


def test_find_by_status(workflow_repo):
    for i, status in enumerate(["DETECTED", "DETECTED", "VERIFIED", "COMPLETED"]):
        wf = Workflow(
            alert_type="tablespace_full",
            database_id=f"DB-{i}",
            environment="DEV",
            status=status,
        )
        workflow_repo.create(wf)

    detected = workflow_repo.find_by_status("DETECTED")
    assert len(detected) == 2

    verified = workflow_repo.find_by_status("VERIFIED")
    assert len(verified) == 1


def test_find_actionable(workflow_repo):
    for status in ["DETECTED", "VERIFIED", "APPROVED", "COMPLETED", "FAILED"]:
        wf = Workflow(
            alert_type="tablespace_full",
            database_id="DB-01",
            environment="DEV",
            status=status,
        )
        workflow_repo.create(wf)

    actionable = workflow_repo.find_actionable()
    statuses = {w.status for w in actionable}
    assert "DETECTED" in statuses
    assert "VERIFIED" in statuses
    assert "APPROVED" in statuses
    assert "COMPLETED" not in statuses
    assert "FAILED" not in statuses


def test_count_by_status(workflow_repo):
    for status in ["DETECTED", "DETECTED", "COMPLETED"]:
        wf = Workflow(alert_type="test", database_id="DB", environment="DEV", status=status)
        workflow_repo.create(wf)

    counts = workflow_repo.count_by_status()
    assert counts["DETECTED"] == 2
    assert counts["COMPLETED"] == 1


def test_find_duplicates(workflow_repo):
    wf1 = Workflow(
        alert_type="tablespace_full",
        database_id="PROD-DB-07",
        environment="PROD",
        status="EXECUTING",
    )
    wf2 = Workflow(
        alert_type="tablespace_full",
        database_id="PROD-DB-07",
        environment="PROD",
        status="DETECTED",
    )
    wf3 = Workflow(
        alert_type="temp_full",
        database_id="PROD-DB-07",
        environment="PROD",
        status="DETECTED",
    )
    workflow_repo.create(wf1)
    workflow_repo.create(wf2)
    workflow_repo.create(wf3)

    dupes = workflow_repo.find_duplicates("PROD-DB-07", "tablespace_full")
    assert len(dupes) == 2  # wf1 and wf2

    dupes2 = workflow_repo.find_duplicates("PROD-DB-07", "temp_full")
    assert len(dupes2) == 1  # wf3
