"""Smoke test: verify Docker Oracle is reachable and test schema exists."""

from tests.integration.conftest import run_oracle


class TestOracleConnection:
    """Basic Oracle connectivity checks."""

    def test_dual_query(self, oracle_conn):
        rows = run_oracle(oracle_conn, "SELECT 1 AS val FROM dual")
        assert rows[0]["val"] == 1

    def test_database_open(self, oracle_conn):
        rows = run_oracle(oracle_conn, "SELECT open_mode FROM v$database")
        assert rows[0]["open_mode"] == "READ WRITE"

    def test_system_user_connected(self, oracle_conn):
        rows = run_oracle(oracle_conn, "SELECT USER FROM dual")
        assert rows[0]["user"] == "SYSTEM"

    def test_v_session_accessible(self, oracle_conn):
        rows = run_oracle(
            oracle_conn,
            "SELECT COUNT(*) AS cnt FROM v$session WHERE status = 'ACTIVE'",
        )
        assert rows[0]["cnt"] >= 1

    def test_dba_tablespaces_accessible(self, oracle_conn):
        rows = run_oracle(
            oracle_conn,
            "SELECT COUNT(*) AS cnt FROM dba_tablespaces",
        )
        assert rows[0]["cnt"] >= 1
