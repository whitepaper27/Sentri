"""Tests for Oracle connection pool — verifies no CURRENT_SCHEMA = SYS."""

from unittest.mock import MagicMock, patch

from sentri.oracle.connection_pool import OracleConnectionPool


class TestConnectionPool:
    def test_parse_connection_string_with_protocol(self):
        user, dsn = OracleConnectionPool._parse_connection_string(
            "oracle://sys@localhost:1521/FREEPDB1"
        )
        assert user == "sys"
        assert dsn == "localhost:1521/FREEPDB1"

    def test_parse_connection_string_without_protocol(self):
        user, dsn = OracleConnectionPool._parse_connection_string("dba@prod-scan:1521/PRODDB")
        assert user == "dba"
        assert dsn == "prod-scan:1521/PRODDB"

    def test_parse_connection_string_no_user(self):
        user, dsn = OracleConnectionPool._parse_connection_string("localhost:1521/DEVDB")
        assert user == "sentri_agent"
        assert dsn == "localhost:1521/DEVDB"

    def test_username_override(self):
        """Config username should override URL-embedded user."""
        pool = OracleConnectionPool()
        mock_oracledb = MagicMock()
        mock_conn = MagicMock()
        mock_oracledb.connect.return_value = mock_conn

        with patch.object(pool, "_get_oracledb", return_value=mock_oracledb):
            _conn = pool.get_connection(
                database_id="test",
                connection_string="oracle://sys@localhost:1521/FREEPDB1",
                password="test123",
                username="sentri_admin",
            )

        # Should connect with overridden username, not URL-parsed "sys"
        mock_oracledb.connect.assert_called_once_with(
            user="sentri_admin",
            password="test123",
            dsn="localhost:1521/FREEPDB1",
        )

    def test_no_current_schema_set(self):
        """CRITICAL: read_only connections must NOT set CURRENT_SCHEMA = SYS.

        Setting CURRENT_SCHEMA = SYS breaks V$ public synonym resolution:
        - V$DATABASE -> SYS."V$DATABASE" (nonexistent) -> ORA-00942
        - The actual fixed view is SYS.V_$DATABASE (with underscore)
        - V$ names are public synonyms that only resolve without schema prefix
        """
        pool = OracleConnectionPool()
        mock_oracledb = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_oracledb.connect.return_value = mock_conn

        with patch.object(pool, "_get_oracledb", return_value=mock_oracledb):
            _conn = pool.get_connection(
                database_id="test",
                connection_string="oracle://sys@localhost:1521/FREEPDB1",
                password="test123",
                read_only=True,
            )

        # cursor.execute should NOT have been called at all
        mock_cursor.execute.assert_not_called()
        # Specifically, no ALTER SESSION
        for c in mock_cursor.execute.call_args_list:
            assert "CURRENT_SCHEMA" not in str(
                c
            ), "Must NOT set CURRENT_SCHEMA = SYS (breaks V$ synonyms)"

    def test_read_write_connection_no_schema(self):
        """Even read_only=False should not set CURRENT_SCHEMA."""
        pool = OracleConnectionPool()
        mock_oracledb = MagicMock()
        mock_conn = MagicMock()
        mock_oracledb.connect.return_value = mock_conn

        with patch.object(pool, "_get_oracledb", return_value=mock_oracledb):
            _conn = pool.get_connection(
                database_id="test",
                connection_string="oracle://sys@localhost:1521/FREEPDB1",
                password="test123",
                read_only=False,
            )

        # No cursor operations at all
        mock_conn.cursor.assert_not_called()
