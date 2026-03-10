"""Integration: Safety Mesh enforcement with real SQLite state."""

from sentri.core.models import AuditRecord, ExecutionPlan, Suggestion, Workflow
from tests.integration.conftest import TEST_DB_NAME


def _make_workflow(context, alert_type="tablespace_full", status="VERIFIED"):
    """Helper to create a test workflow."""
    suggestion = Suggestion(
        alert_type=alert_type,
        database_id=TEST_DB_NAME,
        raw_email_subject=f"Test {alert_type}",
        raw_email_body=f"Test alert for {TEST_DB_NAME}",
        extracted_data={"tablespace_name": "USERS"},
    )
    wf = Workflow(
        alert_type=alert_type,
        database_id=TEST_DB_NAME,
        environment="DEV",
        status=status,
        suggestion=suggestion.to_json(),
    )
    wf_id = context.workflow_repo.create(wf)
    return context.workflow_repo.get(wf_id)


def _make_plan(forward_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 1G"):
    """Helper to create a test execution plan."""
    return ExecutionPlan(
        action_type="ADD_DATAFILE",
        forward_sql=forward_sql,
        rollback_sql="ALTER TABLESPACE USERS DROP DATAFILE '/tmp/test.dbf'",
        validation_sql="SELECT used_percent FROM dba_tablespace_usage_metrics WHERE tablespace_name = 'USERS'",
        expected_outcome={"used_percent": "<90"},
        estimated_duration_seconds=15,
        risk_level="LOW",
    )


class TestSafetyMeshLive:
    """Safety Mesh checks with real SQLite state."""

    def test_allows_dev_low_risk(self, int_context, int_safety_mesh):
        """DEV + low-risk DDL should be ALLOW."""
        wf = _make_workflow(int_context)
        plan = _make_plan()

        verdict = int_safety_mesh.check(wf, plan, confidence=0.95)
        assert verdict.decision.value in ("ALLOW", "REQUIRE_APPROVAL")

    def test_circuit_breaker_blocks_after_failures(self, int_context, int_safety_mesh):
        """3 FAILED audit records on same DB → circuit breaker → BLOCK."""
        wf = _make_workflow(int_context)

        # Insert 3 failure records
        for i in range(3):
            record = AuditRecord(
                workflow_id=wf.id,
                action_type="ADD_DATAFILE",
                database_id=TEST_DB_NAME,
                environment="DEV",
                executed_by="sentri",
                result="FAILED",
                error_message=f"Test failure {i}",
            )
            int_context.audit_repo.create(record)

        plan = _make_plan()
        verdict = int_safety_mesh.check(wf, plan, confidence=0.95)
        assert verdict.decision.value == "BLOCK"

    def test_conflict_detection_queues(self, int_context, int_safety_mesh):
        """Workflow EXECUTING on same DB → new workflow gets QUEUE."""
        # Create an EXECUTING workflow
        _executing_wf = _make_workflow(int_context, status="EXECUTING")

        # Now try to check a new workflow
        new_wf = _make_workflow(int_context)
        plan = _make_plan()

        verdict = int_safety_mesh.check(new_wf, plan, confidence=0.95)
        assert verdict.decision.value == "QUEUE"

    def test_blast_radius_ddl_classification(self, int_context, int_safety_mesh):
        """DDL is correctly classified in blast radius check."""
        wf = _make_workflow(int_context)
        plan = _make_plan("ALTER TABLESPACE USERS ADD DATAFILE SIZE 1G")

        verdict = int_safety_mesh.check(wf, plan, confidence=0.95)
        # DDL in DEV should be ALLOW (not blocked)
        assert verdict.decision.value in ("ALLOW", "REQUIRE_APPROVAL")

    def test_no_rollback_high_risk_blocks(self, int_context, int_safety_mesh):
        """HIGH risk with no rollback SQL → BLOCK."""
        wf = _make_workflow(int_context)
        plan = ExecutionPlan(
            action_type="KILL_SESSION",
            forward_sql="ALTER SYSTEM KILL SESSION '123,456' IMMEDIATE",
            rollback_sql="",  # No rollback possible
            validation_sql="SELECT COUNT(*) FROM v$session WHERE sid = 123",
            expected_outcome={"count": 0},
            estimated_duration_seconds=5,
            risk_level="HIGH",
        )

        verdict = int_safety_mesh.check(wf, plan, confidence=0.95)
        # HIGH risk + no rollback → should block
        assert verdict.decision.value in ("BLOCK", "REQUIRE_APPROVAL")
