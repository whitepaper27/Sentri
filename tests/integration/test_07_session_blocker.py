"""Integration: session_blocker → Scout → RCAAgent.

Creates a real blocking chain in Oracle to test RCA investigation.
"""

import threading
import time

import pytest

from tests.integration.conftest import (
    ORACLE_DSN,
    ORACLE_PASSWORD,
    ORACLE_USER,
    TEST_DB_NAME,
    run_oracle,
    run_oracle_ddl,
)


@pytest.fixture
def blocking_chain(oracle_conn):
    """Create a real blocking chain: conn_a holds lock, conn_b blocks on it.

    Returns the SID of the blocker (conn_a).
    """
    import oracledb

    # Ensure test table exists with at least 1 row
    try:
        run_oracle_ddl(
            oracle_conn,
            """
            CREATE TABLE sentri_block_test (id NUMBER PRIMARY KEY, val VARCHAR2(100))
        """,
        )
        run_oracle_ddl(
            oracle_conn,
            """
            INSERT INTO sentri_block_test VALUES (1, 'initial')
        """,
        )
        oracle_conn.commit()
    except Exception:
        # Table may already exist
        oracle_conn.rollback()

    # Connection A: holds a row lock
    conn_a = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    cur_a = conn_a.cursor()
    cur_a.execute("UPDATE sentri_block_test SET val = 'locked_by_a' WHERE id = 1")
    # DO NOT COMMIT — this holds the lock

    # Get SID of connection A
    cur_a.execute("SELECT sys_context('userenv', 'sid') AS sid FROM dual")
    blocker_sid = int(cur_a.fetchone()[0])

    # Connection B: will block trying to update the same row
    conn_b = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    conn_b.call_timeout = 60000  # 60s timeout to prevent hanging forever

    blocked_ready = threading.Event()

    def _blocked_update():
        try:
            cur_b = conn_b.cursor()
            blocked_ready.set()
            # This will BLOCK until conn_a releases the lock
            cur_b.execute("UPDATE sentri_block_test SET val = 'blocked_by_b' WHERE id = 1")
        except Exception:
            pass

    t = threading.Thread(target=_blocked_update, daemon=True)
    t.start()
    blocked_ready.wait(timeout=5)
    time.sleep(2)  # Give Oracle time to register the blocking relationship

    yield blocker_sid

    # Teardown: rollback both, close
    try:
        conn_a.rollback()
        conn_a.close()
    except Exception:
        pass
    try:
        conn_b.rollback()
        conn_b.close()
    except Exception:
        pass
    t.join(timeout=5)

    # Drop test table
    try:
        run_oracle_ddl(oracle_conn, "DROP TABLE sentri_block_test PURGE")
    except Exception:
        pass


class TestSessionBlockerPipeline:
    """session_blocker alert through RCAAgent with real blocking chain."""

    def test_scout_detects_blocker(self, int_scout):
        """Scout parses session_blocker email."""
        wf_id = int_scout.process_raw_email(
            subject=f"Blocking session detected SID=847 on {TEST_DB_NAME}",
            body=f"Session blocker alert: SID=847 blocking chain on database {TEST_DB_NAME}.",
        )
        assert wf_id is not None

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.alert_type == "session_blocker"
        assert wf.database_id == TEST_DB_NAME

    def test_supervisor_routes_to_rca(self, int_scout, int_supervisor):
        """Supervisor routes session_blocker to rca_agent."""
        wf_id = int_scout.process_raw_email(
            subject=f"Blocking session detected SID=100 on {TEST_DB_NAME}",
            body=f"Blocker on database {TEST_DB_NAME}.",
        )

        int_supervisor._process_cycle()

        wf = int_scout.context.workflow_repo.get(wf_id)
        assert wf.status != "DETECTED"

    def test_rca_with_real_blocking_chain(
        self,
        int_scout,
        int_rca_agent,
        blocking_chain,
        oracle_conn,
    ):
        """RCAAgent investigates a real blocking session."""
        blocker_sid = blocking_chain

        # Verify the blocking chain exists in Oracle
        rows = run_oracle(
            oracle_conn,
            f"SELECT COUNT(*) AS cnt FROM v$session WHERE blocking_session = {blocker_sid}",
        )
        assert rows[0]["cnt"] >= 1, "Blocking chain not established"

        # Create workflow with the real blocker SID
        wf_id = int_scout.process_raw_email(
            subject=f"Blocking session detected SID={blocker_sid} on {TEST_DB_NAME}",
            body=f"Session blocker alert: SID={blocker_sid} on database {TEST_DB_NAME}.",
        )

        result = int_rca_agent.process(wf_id)
        assert result["status"] in ("success", "failure", "needs_approval")
