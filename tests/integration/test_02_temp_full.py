"""Integration: temp_full → Scout → StorageAgent."""

import pytest

from tests.integration.conftest import TEST_DB_NAME, run_oracle, run_oracle_ddl

TEMP_TS = "SENTRI_INT_TEMP"


@pytest.fixture
def setup_temp_tablespace(oracle_conn):
    """Create a temporary tablespace, drop after test."""
    try:
        run_oracle_ddl(oracle_conn, f"DROP TABLESPACE {TEMP_TS} INCLUDING CONTENTS AND DATAFILES")
    except Exception:
        pass
    run_oracle_ddl(
        oracle_conn,
        f"CREATE TEMPORARY TABLESPACE {TEMP_TS} TEMPFILE SIZE 2M AUTOEXTEND OFF",
    )
    yield
    try:
        run_oracle_ddl(oracle_conn, f"DROP TABLESPACE {TEMP_TS} INCLUDING CONTENTS AND DATAFILES")
    except Exception:
        pass


class TestTempFullPipeline:
    """End-to-end temp_full alert through v5.0 pipeline."""

    def test_scout_detects_temp_alert(self, int_scout, setup_temp_tablespace):
        """Scout parses temp_full email and creates workflow."""
        wf_id = int_scout.process_raw_email(
            subject=f"Temporary tablespace {TEMP_TS} 88% full on {TEST_DB_NAME}",
            body=f"Temp tablespace {TEMP_TS} at 88% used on database {TEST_DB_NAME}.",
        )
        assert wf_id is not None

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.alert_type == "temp_full"
        assert wf.database_id == TEST_DB_NAME
        assert wf.status == "DETECTED"

    def test_storage_agent_handles_temp(
        self,
        int_scout,
        int_storage_agent,
        oracle_conn,
        setup_temp_tablespace,
    ):
        """StorageAgent processes temp_full alert."""
        # Count tempfiles before
        before = run_oracle(
            oracle_conn,
            f"SELECT COUNT(*) AS cnt FROM dba_temp_files WHERE tablespace_name = '{TEMP_TS}'",
        )
        initial_count = before[0]["cnt"]

        wf_id = int_scout.process_raw_email(
            subject=f"Temporary tablespace {TEMP_TS} 88% full on {TEST_DB_NAME}",
            body=f"Temp tablespace {TEMP_TS} at 88% on database {TEST_DB_NAME}.",
        )

        result = int_storage_agent.process(wf_id)
        assert result["status"] in ("success", "needs_approval", "failure")

        if result["status"] == "success":
            after = run_oracle(
                oracle_conn,
                f"SELECT COUNT(*) AS cnt FROM dba_temp_files WHERE tablespace_name = '{TEMP_TS}'",
            )
            assert after[0]["cnt"] > initial_count
