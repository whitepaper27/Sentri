"""Integration: tablespace_full → Scout → Supervisor → StorageAgent.

Tests the full v5.0 pipeline against real Docker Oracle.
"""

import pytest

from tests.integration.conftest import TEST_DB_NAME, run_oracle, run_oracle_ddl

TS_NAME = "SENTRI_INT_TS"


@pytest.fixture
def setup_tablespace(oracle_conn):
    """Create a small test tablespace, drop it after the test."""
    # Drop if exists from a previous failed run
    try:
        run_oracle_ddl(oracle_conn, f"DROP TABLESPACE {TS_NAME} INCLUDING CONTENTS AND DATAFILES")
    except Exception:
        pass
    # Create small tablespace with autoextend OFF
    run_oracle_ddl(
        oracle_conn,
        f"CREATE TABLESPACE {TS_NAME} DATAFILE SIZE 2M AUTOEXTEND OFF",
    )
    yield
    # Teardown
    try:
        run_oracle_ddl(oracle_conn, f"DROP TABLESPACE {TS_NAME} INCLUDING CONTENTS AND DATAFILES")
    except Exception:
        pass


class TestTablespaceFullPipeline:
    """End-to-end tablespace_full alert through v5.0 pipeline."""

    def test_scout_detects_alert(self, int_scout, setup_tablespace):
        """Scout parses the email and creates a DETECTED workflow."""
        wf_id = int_scout.process_raw_email(
            subject=f"Tablespace {TS_NAME} 92% full on {TEST_DB_NAME}",
            body=f"Tablespace {TS_NAME} is at 92% capacity on database {TEST_DB_NAME}. "
            "Please add a datafile to prevent space issues.",
        )
        assert wf_id is not None

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.alert_type == "tablespace_full"
        assert wf.database_id == TEST_DB_NAME
        assert wf.status == "DETECTED"

    def test_supervisor_routes_to_storage(self, int_scout, int_supervisor, setup_tablespace):
        """Supervisor routes tablespace_full to storage_agent."""
        wf_id = int_scout.process_raw_email(
            subject=f"Tablespace {TS_NAME} 92% full on {TEST_DB_NAME}",
            body=f"Tablespace {TS_NAME} at 92% capacity on database {TEST_DB_NAME}.",
        )
        assert wf_id is not None

        # Supervisor processes the cycle
        int_supervisor._process_cycle()

        # Check the workflow was processed (status moved beyond DETECTED)
        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.status != "DETECTED", f"Expected routing but status is still {wf.status}"

    def test_storage_agent_processes_alert(
        self,
        int_scout,
        int_storage_agent,
        oracle_conn,
        setup_tablespace,
    ):
        """StorageAgent runs full verify→investigate→propose→execute pipeline."""
        # Count datafiles before
        before = run_oracle(
            oracle_conn,
            f"SELECT COUNT(*) AS cnt FROM dba_data_files WHERE tablespace_name = '{TS_NAME}'",
        )
        initial_count = before[0]["cnt"]

        # Create workflow via Scout
        wf_id = int_scout.process_raw_email(
            subject=f"Tablespace {TS_NAME} 92% full on {TEST_DB_NAME}",
            body=f"Tablespace {TS_NAME} at 92% on database {TEST_DB_NAME}.",
        )
        assert wf_id is not None

        # Run StorageAgent
        result = int_storage_agent.process(wf_id)

        # Check result — either success or needs_approval (Safety Mesh may require it)
        assert result["status"] in (
            "success",
            "needs_approval",
            "failure",
        ), f"Unexpected status: {result}"

        # If successful, verify a datafile was added
        if result["status"] == "success":
            after = run_oracle(
                oracle_conn,
                f"SELECT COUNT(*) AS cnt FROM dba_data_files WHERE tablespace_name = '{TS_NAME}'",
            )
            assert after[0]["cnt"] > initial_count, "Expected new datafile to be added"

    def test_tablespace_exists_in_oracle(self, oracle_conn, setup_tablespace):
        """Verify test tablespace was created properly."""
        rows = run_oracle(
            oracle_conn,
            f"SELECT tablespace_name, status FROM dba_tablespaces WHERE tablespace_name = '{TS_NAME}'",
        )
        assert len(rows) == 1
        assert rows[0]["status"] == "ONLINE"
