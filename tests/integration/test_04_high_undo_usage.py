"""Integration: high_undo_usage → Scout → StorageAgent."""

from tests.integration.conftest import TEST_DB_NAME


class TestHighUndoUsagePipeline:
    """high_undo_usage alert through v5.0 pipeline."""

    def test_scout_detects_undo_alert(self, int_scout):
        """Scout parses high_undo_usage email."""
        wf_id = int_scout.process_raw_email(
            subject=f"High undo usage 91% on {TEST_DB_NAME}",
            body=f"High undo usage alert: undo tablespace UNDOTBS1 at 91% "
            f"on database {TEST_DB_NAME}. Risk of ORA-30036.",
        )
        assert wf_id is not None

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.alert_type == "high_undo_usage"
        assert wf.database_id == TEST_DB_NAME

    def test_supervisor_routes_to_storage(self, int_scout, int_supervisor):
        """Supervisor routes high_undo_usage to storage_agent."""
        wf_id = int_scout.process_raw_email(
            subject=f"High undo usage 88% on {TEST_DB_NAME}",
            body=f"High undo usage on database {TEST_DB_NAME}.",
        )

        int_supervisor._process_cycle()

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.status != "DETECTED"

    def test_storage_agent_processes_undo(self, int_scout, int_storage_agent):
        """StorageAgent processes high_undo_usage alert without crashing."""
        wf_id = int_scout.process_raw_email(
            subject=f"High undo usage 91% on {TEST_DB_NAME}",
            body=f"Undo tablespace at 91% on database {TEST_DB_NAME}.",
        )

        result = int_storage_agent.process(wf_id)
        assert result["status"] in ("success", "failure", "needs_approval")
