"""Integration: cpu_high → Scout → SQLTuningAgent."""

from tests.integration.conftest import TEST_DB_NAME


class TestCPUHighPipeline:
    """cpu_high alert through v5.0 pipeline."""

    def test_scout_detects_cpu_alert(self, int_scout):
        """Scout parses cpu_high email."""
        wf_id = int_scout.process_raw_email(
            subject=f"High CPU utilization 97% on {TEST_DB_NAME}",
            body=f"CPU high alert: CPU at 97% on database {TEST_DB_NAME}. "
            "Top consumer running full table scan.",
        )
        assert wf_id is not None

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.alert_type == "cpu_high"
        assert wf.database_id == TEST_DB_NAME

    def test_supervisor_routes_to_sql_tuning(self, int_scout, int_supervisor):
        """Supervisor routes cpu_high to sql_tuning_agent."""
        wf_id = int_scout.process_raw_email(
            subject=f"High CPU utilization 97% on {TEST_DB_NAME}",
            body=f"CPU high on database {TEST_DB_NAME}.",
        )

        int_supervisor._process_cycle()

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.status != "DETECTED"

    def test_sql_tuning_processes_cpu(self, int_scout, int_sql_tuning_agent):
        """SQLTuningAgent processes cpu_high alert without crashing."""
        wf_id = int_scout.process_raw_email(
            subject=f"High CPU utilization 97% on {TEST_DB_NAME}",
            body=f"CPU at 97% on database {TEST_DB_NAME}.",
        )

        result = int_sql_tuning_agent.process(wf_id)
        assert result["status"] in ("success", "failure", "needs_approval")
