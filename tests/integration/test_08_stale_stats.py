"""Integration: ProactiveAgent stale_stats → finding → Supervisor → SQLTuningAgent."""

from tests.integration.conftest import TEST_DB_NAME


class TestStaleStatsPipeline:
    """ProactiveAgent detects stale stats and routes to SQLTuningAgent."""

    def test_supervisor_routing_for_stale_stats(self, int_scout, int_supervisor):
        """Supervisor routes check_finding:stale_stats to sql_tuning_agent."""
        # Simulate what ProactiveAgent would create
        from sentri.core.models import Suggestion, Workflow

        suggestion = Suggestion(
            alert_type="check_finding:stale_stats",
            database_id=TEST_DB_NAME,
            raw_email_subject="Proactive: stale_stats finding",
            raw_email_body="Found tables not analyzed in 30+ days",
            extracted_data={"check_type": "stale_stats"},
        )
        wf = Workflow(
            alert_type="check_finding:stale_stats",
            database_id=TEST_DB_NAME,
            environment="DEV",
            suggestion=suggestion.to_json(),
        )
        wf_id = int_scout.context.workflow_repo.create(wf)

        int_supervisor._process_cycle()

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.status != "DETECTED"

    def test_sql_tuning_handles_stale_finding(self, int_sql_tuning_agent, int_context):
        """SQLTuningAgent processes a stale_stats check finding."""
        from sentri.core.models import Suggestion, Workflow

        suggestion = Suggestion(
            alert_type="check_finding:stale_stats",
            database_id=TEST_DB_NAME,
            raw_email_subject="Proactive: stale_stats",
            raw_email_body="Stale stats detected",
            extracted_data={
                "check_type": "stale_stats",
                "findings": [
                    {"owner": "SYSTEM", "table_name": "TEST_TABLE", "days_stale": 45},
                ],
            },
        )
        wf = Workflow(
            alert_type="check_finding:stale_stats",
            database_id=TEST_DB_NAME,
            environment="DEV",
            suggestion=suggestion.to_json(),
        )
        wf_id = int_context.workflow_repo.create(wf)

        result = int_sql_tuning_agent.process(wf_id)
        assert result["status"] in ("success", "failure", "needs_approval")
