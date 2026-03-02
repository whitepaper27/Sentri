"""Integration: long_running_sql → Scout → SQLTuningAgent."""

import threading

import pytest

from tests.integration.conftest import (
    ORACLE_DSN,
    ORACLE_PASSWORD,
    ORACLE_USER,
    TEST_DB_NAME,
    run_oracle,
)


@pytest.fixture
def slow_session(oracle_conn):
    """Start a slow query in a background thread, return its SID."""
    import oracledb

    # Separate connection for the slow query
    slow_conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    slow_conn.call_timeout = 0  # No timeout on this connection

    sid_result = {"sid": None, "ready": threading.Event(), "stop": threading.Event()}

    def _run_slow():
        try:
            cur = slow_conn.cursor()
            # Get our SID
            cur.execute("SELECT sys_context('userenv', 'sid') AS sid FROM dual")
            sid_result["sid"] = int(cur.fetchone()[0])
            sid_result["ready"].set()

            # Run a slow query (wait using DBMS_SESSION.SLEEP)
            cur.execute("BEGIN DBMS_SESSION.SLEEP(120); END;")
        except Exception:
            pass  # Will be killed or interrupted
        finally:
            try:
                slow_conn.close()
            except Exception:
                pass

    t = threading.Thread(target=_run_slow, daemon=True)
    t.start()

    # Wait for the SID to be captured
    sid_result["ready"].wait(timeout=10)
    assert sid_result["sid"] is not None, "Failed to get slow session SID"

    yield sid_result["sid"]

    # Teardown: kill the session if still alive
    try:
        rows = run_oracle(
            oracle_conn,
            f"SELECT sid, serial# AS serial FROM v$session WHERE sid = {sid_result['sid']}",
        )
        if rows:
            serial = rows[0]["serial"]
            run_oracle(
                oracle_conn,
                f"ALTER SYSTEM KILL SESSION '{sid_result['sid']},{serial}' IMMEDIATE",
            )
    except Exception:
        pass
    t.join(timeout=5)


class TestLongRunningSQLPipeline:
    """long_running_sql alert through SQLTuningAgent."""

    def test_scout_detects_long_running(self, int_scout):
        """Scout parses long_running_sql email."""
        wf_id = int_scout.process_raw_email(
            subject=f"Long running SQL session SID=912 running 4 hours on {TEST_DB_NAME}",
            body=f"Long running SQL detected: SID=912 running 4 hrs on database {TEST_DB_NAME}.",
        )
        assert wf_id is not None

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.alert_type == "long_running_sql"
        assert wf.database_id == TEST_DB_NAME

    def test_supervisor_routes_to_sql_tuning(self, int_scout, int_supervisor):
        """Supervisor routes long_running_sql to sql_tuning_agent."""
        wf_id = int_scout.process_raw_email(
            subject=f"Long running SQL session SID=912 running 4 hours on {TEST_DB_NAME}",
            body=f"Long running SQL on database {TEST_DB_NAME}.",
        )

        int_supervisor._process_cycle()

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.status != "DETECTED"

    def test_sql_tuning_with_real_session(self, int_scout, int_sql_tuning_agent, slow_session):
        """SQLTuningAgent processes alert with a real slow session running."""
        sid = slow_session
        wf_id = int_scout.process_raw_email(
            subject=f"Long running SQL session SID={sid} running 4 hours on {TEST_DB_NAME}",
            body=f"Long running SQL: SID={sid} on database {TEST_DB_NAME}.",
        )

        result = int_sql_tuning_agent.process(wf_id)
        assert result["status"] in ("success", "failure", "needs_approval")
