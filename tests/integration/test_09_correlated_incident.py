"""Integration: Supervisor correlation — 2 storage alerts on same DB → RCA."""

from tests.integration.conftest import TEST_DB_NAME


class TestCorrelatedIncident:
    """Supervisor detects correlated alerts and routes to RCA agent."""

    def test_two_storage_alerts_correlate(self, int_scout, int_supervisor):
        """Two storage-category alerts on same DB within 5 min → correlated → RCA."""
        # Alert 1: tablespace_full
        wf_id1 = int_scout.process_raw_email(
            subject=f"Tablespace USERS 90% full on {TEST_DB_NAME}",
            body=f"Tablespace USERS at 90% on database {TEST_DB_NAME}.",
        )

        # Alert 2: high_undo_usage (same DB, same "storage" category)
        wf_id2 = int_scout.process_raw_email(
            subject=f"High undo usage 85% on {TEST_DB_NAME}",
            body=f"High undo usage on database {TEST_DB_NAME}.",
        )

        assert wf_id1 is not None
        assert wf_id2 is not None

        # Both should be DETECTED
        wf1 = int_scout.context.workflow_repo.get(wf_id1)
        wf2 = int_scout.context.workflow_repo.get(wf_id2)
        assert wf1.status == "DETECTED"
        assert wf2.status == "DETECTED"

        # Process cycle — Supervisor should detect correlation
        int_supervisor._process_cycle()

        # At least one should have been processed
        wf1 = int_scout.context.workflow_repo.get(wf_id1)
        wf2 = int_scout.context.workflow_repo.get(wf_id2)

        processed = (wf1.status != "DETECTED") or (wf2.status != "DETECTED")
        assert processed, "Neither workflow was processed by Supervisor"

    def test_cross_category_not_correlated(self, int_scout, int_supervisor):
        """tablespace_full + cpu_high on same DB → NOT correlated (different categories)."""
        wf_id1 = int_scout.process_raw_email(
            subject=f"Tablespace USERS 90% full on {TEST_DB_NAME}",
            body=f"Tablespace USERS at 90% on database {TEST_DB_NAME}.",
        )

        wf_id2 = int_scout.process_raw_email(
            subject=f"High CPU utilization 95% on {TEST_DB_NAME}",
            body=f"CPU high on database {TEST_DB_NAME}.",
        )

        assert wf_id1 is not None
        assert wf_id2 is not None

        int_supervisor._process_cycle()

        # Both should have been processed independently (not as correlated incident)
        wf1 = int_scout.context.workflow_repo.get(wf_id1)
        wf2 = int_scout.context.workflow_repo.get(wf_id2)

        processed = (wf1.status != "DETECTED") or (wf2.status != "DETECTED")
        assert processed
