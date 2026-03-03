"""Integration: archive_dest_full → Scout → StorageAgent.

Oracle Free lacks RMAN, so execution is expected to fail gracefully.
"""

from tests.integration.conftest import TEST_DB_NAME


class TestArchiveDestFullPipeline:
    """archive_dest_full alert — tests routing + graceful failure."""

    def test_scout_detects_archive_alert(self, int_scout):
        """Scout parses archive_dest_full email."""
        wf_id = int_scout.process_raw_email(
            subject=f"Archive log destination LOG_ARCHIVE_DEST_1 95% full on {TEST_DB_NAME}",
            body=f"Archive destination at 95% on database {TEST_DB_NAME}. "
            "Database may stall if unable to write archive logs.",
        )
        assert wf_id is not None

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.alert_type == "archive_dest_full"
        assert wf.database_id == TEST_DB_NAME

    def test_supervisor_routes_to_storage(self, int_scout, int_supervisor):
        """Supervisor routes archive_dest_full to storage_agent."""
        wf_id = int_scout.process_raw_email(
            subject=f"Archive log destination LOG_ARCHIVE_DEST_1 95% full on {TEST_DB_NAME}",
            body=f"Archive destination full on database {TEST_DB_NAME}.",
        )

        int_supervisor._process_cycle()

        wf = int_scout.context.workflow_repo.get(wf_id)
        # Should have been processed (moved beyond DETECTED)
        assert wf.status != "DETECTED"

    def test_storage_agent_handles_failure_gracefully(self, int_scout, int_storage_agent):
        """StorageAgent processes archive alert — may fail due to no RMAN, but no crash."""
        wf_id = int_scout.process_raw_email(
            subject=f"Archive log destination LOG_ARCHIVE_DEST_1 95% full on {TEST_DB_NAME}",
            body=f"Archive destination at 95% on database {TEST_DB_NAME}.",
        )

        # Should not raise an unhandled exception
        result = int_storage_agent.process(wf_id)
        assert result["status"] in ("success", "failure", "needs_approval")
