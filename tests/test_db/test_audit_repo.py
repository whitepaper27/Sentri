"""Test audit repository (append-only)."""

from sentri.core.models import AuditRecord


def test_create_audit_record(audit_repo, workflow_repo):
    from sentri.core.models import Workflow

    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    record = AuditRecord(
        workflow_id=wf.id,
        action_type="ADD_DATAFILE",
        database_id="DB-01",
        environment="DEV",
        executed_by="agent4_executor",
        result="SUCCESS",
        action_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
    )
    row_id = audit_repo.create(record)
    assert row_id > 0


def test_find_by_workflow(audit_repo, workflow_repo):
    from sentri.core.models import Workflow

    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    for result in ["SUCCESS", "FAILED"]:
        audit_repo.create(
            AuditRecord(
                workflow_id=wf.id,
                action_type="TEST",
                database_id="DB-01",
                environment="DEV",
                executed_by="test",
                result=result,
            )
        )

    records = audit_repo.find_by_workflow(wf.id)
    assert len(records) == 2


def test_count_by_result(audit_repo, workflow_repo):
    from sentri.core.models import Workflow

    wf = Workflow(alert_type="test", database_id="DB-01", environment="DEV")
    workflow_repo.create(wf)

    for _ in range(3):
        audit_repo.create(
            AuditRecord(
                workflow_id=wf.id,
                action_type="T",
                database_id="DB-01",
                environment="DEV",
                executed_by="test",
                result="SUCCESS",
            )
        )
    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="T",
            database_id="DB-01",
            environment="DEV",
            executed_by="test",
            result="FAILED",
        )
    )

    counts = audit_repo.count_by_result()
    assert counts["SUCCESS"] == 3
    assert counts["FAILED"] == 1
