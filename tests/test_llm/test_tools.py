"""Tests for DBA tools and DBAToolExecutor (llm/tools.py).

These tests use mocks — no real Oracle connection needed.
Covers v2.1 (5 tools) and v4.0 (7 new tools).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from sentri.core.llm_interface import ToolCall
from sentri.llm.tools import (
    _UNSAFE_SQL_RE,
    _VALID_TOP_SQL_METRICS,
    TOOL_DEFINITIONS,
    DBAToolExecutor,
)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    def test_twelve_tools_defined(self):
        assert len(TOOL_DEFINITIONS) == 12

    def test_tool_names(self):
        names = {t.name for t in TOOL_DEFINITIONS}
        assert names == {
            # v2.1 tools
            "get_tablespace_info",
            "get_db_parameters",
            "get_storage_info",
            "get_instance_info",
            "query_database",
            # v4.0 tools
            "get_sql_plan",
            "get_sql_stats",
            "get_table_stats",
            "get_index_info",
            "get_session_info",
            "get_top_sql",
            "get_wait_events",
        }

    def test_all_have_required_fields(self):
        for t in TOOL_DEFINITIONS:
            assert t.name, "Tool must have a name"
            assert t.description, "Tool must have a description"
            assert "type" in t.parameters, "Tool must have JSON Schema parameters"
            assert t.parameters["type"] == "object"

    def test_all_require_database_id(self):
        for t in TOOL_DEFINITIONS:
            required = t.parameters.get("required", [])
            assert "database_id" in required, f"{t.name} must require database_id"


# ---------------------------------------------------------------------------
# SQL safety guard
# ---------------------------------------------------------------------------


class TestSQLSafetyGuard:
    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO users VALUES (1)",
            "UPDATE tablespace SET size = 100",
            "DELETE FROM audit_log",
            "DROP TABLE users",
            "ALTER TABLESPACE USERS ADD DATAFILE",
            "CREATE TABLE test (id NUMBER)",
            "TRUNCATE TABLE users",
            "MERGE INTO target USING source ON ...",
            "GRANT SELECT ON v$database TO user1",
            "REVOKE DBA FROM user1",
        ],
    )
    def test_rejects_unsafe_sql(self, sql):
        assert _UNSAFE_SQL_RE.search(sql) is not None, f"Should reject: {sql}"

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM v$database",
            "SELECT tablespace_name FROM dba_tablespaces",
            "WITH cte AS (SELECT 1 FROM dual) SELECT * FROM cte",
            "SELECT value FROM v$parameter WHERE name = 'db_create_file_dest'",
        ],
    )
    def test_allows_safe_sql(self, sql):
        assert _UNSAFE_SQL_RE.search(sql) is None, f"Should allow: {sql}"


# ---------------------------------------------------------------------------
# DBAToolExecutor
# ---------------------------------------------------------------------------


class TestDBAToolExecutor:
    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return settings

    @pytest.fixture
    def executor(self, mock_settings):
        return DBAToolExecutor(mock_settings)

    def test_unknown_tool_returns_error(self, executor):
        tc = ToolCall("id1", "nonexistent_tool", {})
        result = executor.execute(tc)
        assert result.is_error is True
        assert "Unknown tool" in result.content

    def test_query_database_rejects_dml(self, executor):
        """query_database must block INSERT/UPDATE/DELETE/etc."""
        tc = ToolCall(
            "id1",
            "query_database",
            {
                "database_id": "dev",
                "sql": "DELETE FROM users WHERE id = 1",
            },
        )
        # Mock connection to avoid real DB call
        with patch.object(executor, "_get_connection"):
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert "error" in data
        assert "Only SELECT" in data["error"]

    def test_query_database_rejects_alter(self, executor):
        tc = ToolCall(
            "id1",
            "query_database",
            {
                "database_id": "dev",
                "sql": "ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            },
        )
        with patch.object(executor, "_get_connection"):
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert "error" in data

    def test_query_database_rejects_non_select(self, executor):
        """Must start with SELECT or WITH."""
        tc = ToolCall(
            "id1",
            "query_database",
            {
                "database_id": "dev",
                "sql": "EXPLAIN PLAN FOR SELECT 1 FROM dual",
            },
        )
        with patch.object(executor, "_get_connection"):
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert "error" in data
        assert "SELECT or WITH" in data["error"]

    def test_query_database_allows_select(self, executor):
        """Valid SELECT should pass safety guard and execute."""
        tc = ToolCall(
            "id1",
            "query_database",
            {
                "database_id": "dev",
                "sql": "SELECT * FROM v$database",
            },
        )
        mock_conn = MagicMock()
        with (
            patch.object(executor, "_get_connection", return_value=mock_conn),
            patch.object(executor, "_query", return_value=[{"name": "ORCL"}]),
        ):
            result = executor.execute(tc)

        assert result.is_error is False
        data = json.loads(result.content)
        assert data["row_count"] == 1

    def test_query_database_allows_with(self, executor):
        tc = ToolCall(
            "id1",
            "query_database",
            {
                "database_id": "dev",
                "sql": "WITH cte AS (SELECT 1 x FROM dual) SELECT x FROM cte",
            },
        )
        with (
            patch.object(executor, "_get_connection", return_value=MagicMock()),
            patch.object(executor, "_query", return_value=[{"x": 1}]),
        ):
            result = executor.execute(tc)
        assert result.is_error is False

    def test_empty_sql_returns_error(self, executor):
        tc = ToolCall(
            "id1",
            "query_database",
            {
                "database_id": "dev",
                "sql": "",
            },
        )
        with patch.object(executor, "_get_connection"):
            result = executor.execute(tc)
        data = json.loads(result.content)
        assert "error" in data

    def test_get_tablespace_info_returns_data(self, executor):
        """get_tablespace_info should call 3 queries and return structured result."""
        mock_rows = [{"tablespace_name": "USERS", "bigfile": "NO", "status": "ONLINE"}]
        with patch.object(executor, "_query", return_value=mock_rows):
            tc = ToolCall(
                "id1",
                "get_tablespace_info",
                {
                    "database_id": "dev",
                    "tablespace_name": "USERS",
                },
            )
            result = executor.execute(tc)

        assert result.is_error is False
        data = json.loads(result.content)
        assert "tablespace" in data
        assert data["tablespace"]["bigfile"] == "NO"

    def test_get_db_parameters_returns_params(self, executor):
        mock_rows = [
            {"name": "db_create_file_dest", "value": "/opt/oracle/oradata", "isdefault": "FALSE"},
        ]
        with patch.object(executor, "_query", return_value=mock_rows):
            tc = ToolCall(
                "id1",
                "get_db_parameters",
                {
                    "database_id": "dev",
                    "param_names": ["db_create_file_dest"],
                },
            )
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert "parameters" in data
        assert "db_create_file_dest" in data["parameters"]
        assert data["parameters"]["db_create_file_dest"]["value"] == "/opt/oracle/oradata"

    def test_get_db_parameters_empty_list(self, executor):
        tc = ToolCall(
            "id1",
            "get_db_parameters",
            {
                "database_id": "dev",
                "param_names": [],
            },
        )
        result = executor.execute(tc)
        data = json.loads(result.content)
        assert data["parameters"] == {}

    def test_get_instance_info_returns_data(self, executor):
        with patch.object(
            executor,
            "_query",
            side_effect=[
                [{"db_unique_name": "ORCL", "cdb": "YES"}],  # v$database
                [{"version": "21.0.0.0.0", "status": "OPEN"}],  # v$instance
                [{"con_id": 3, "name": "FREEPDB1"}],  # v$pdbs
            ],
        ):
            tc = ToolCall("id1", "get_instance_info", {"database_id": "dev"})
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert data["database"]["cdb"] == "YES"
        assert data["instance"]["version"] == "21.0.0.0.0"
        assert len(data["pdbs"]) == 1

    def test_no_db_config_returns_error(self):
        settings = MagicMock()
        settings.get_database.return_value = None

        executor = DBAToolExecutor(settings)
        tc = ToolCall("id1", "get_instance_info", {"database_id": "nonexistent"})
        result = executor.execute(tc)

        assert result.is_error is True
        assert "No configuration" in result.content

    def test_connection_error_returns_error(self, executor):
        """Database connection failure should return error, not crash."""
        with patch.object(
            executor._pool, "get_connection", side_effect=Exception("Connection refused")
        ):
            tc = ToolCall("id1", "get_instance_info", {"database_id": "dev"})
            result = executor.execute(tc)

        assert result.is_error is True
        assert "Connection refused" in result.content


# ---------------------------------------------------------------------------
# v4.0 Tools
# ---------------------------------------------------------------------------


class TestV4ToolDefinitions:
    """Validate v4.0 tool definition structure."""

    def test_all_v4_tools_require_database_id(self):
        v4_names = {
            "get_sql_plan",
            "get_sql_stats",
            "get_table_stats",
            "get_index_info",
            "get_session_info",
            "get_top_sql",
            "get_wait_events",
        }
        for t in TOOL_DEFINITIONS:
            if t.name in v4_names:
                required = t.parameters.get("required", [])
                assert "database_id" in required, f"{t.name} must require database_id"

    def test_top_sql_has_metric_enum(self):
        top_sql = next(t for t in TOOL_DEFINITIONS if t.name == "get_top_sql")
        metric_prop = top_sql.parameters["properties"]["metric"]
        assert "enum" in metric_prop
        assert set(metric_prop["enum"]) == set(_VALID_TOP_SQL_METRICS.keys())


class TestSqlPlanHandler:
    @pytest.fixture
    def executor(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return DBAToolExecutor(settings)

    def test_sql_plan_returns_metadata_and_steps(self, executor):
        meta = [
            {
                "sql_id": "abc123",
                "sql_text": "SELECT 1",
                "plan_hash_value": 999,
                "executions": 100,
                "elapsed_sec": 1.5,
                "cpu_sec": 0.8,
                "buffer_gets": 5000,
                "disk_reads": 10,
            }
        ]
        steps = [
            {
                "id": 0,
                "parent_id": None,
                "operation": "SELECT STATEMENT",
                "options": None,
                "object_name": None,
                "cost": 3,
                "cardinality": 1,
                "bytes": None,
                "depth": 0,
                "access_predicates": None,
                "filter_predicates": None,
            },
            {
                "id": 1,
                "parent_id": 0,
                "operation": "TABLE ACCESS",
                "options": "FULL",
                "object_name": "DUAL",
                "cost": 3,
                "cardinality": 1,
                "bytes": 2,
                "depth": 1,
                "access_predicates": None,
                "filter_predicates": None,
            },
        ]
        with patch.object(executor, "_query", side_effect=[meta, steps]):
            tc = ToolCall("id1", "get_sql_plan", {"database_id": "dev", "sql_id": "abc123"})
            result = executor.execute(tc)

        assert result.is_error is False
        data = json.loads(result.content)
        assert data["sql_id"] == "abc123"
        assert data["child_number"] == 0
        assert data["metadata"]["executions"] == 100
        assert len(data["plan_steps"]) == 2
        assert data["plan_steps"][1]["operation"] == "TABLE ACCESS"


class TestSqlStatsHandler:
    @pytest.fixture
    def executor(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return DBAToolExecutor(settings)

    def test_sql_stats_returns_aggregated(self, executor):
        agg = [
            {
                "sql_id": "abc123",
                "sql_text": "SELECT 1",
                "total_executions": 500,
                "total_elapsed_sec": 10.5,
                "total_cpu_sec": 5.2,
                "total_buffer_gets": 25000,
                "total_disk_reads": 50,
                "total_rows": 500,
                "avg_elapsed_sec": 0.021,
                "avg_buffer_gets": 50.0,
                "child_cursor_count": 2,
            }
        ]
        children = [
            {
                "child_number": 0,
                "plan_hash_value": 111,
                "executions": 300,
                "elapsed_sec": 6.0,
                "cpu_sec": 3.0,
                "buffer_gets": 15000,
                "disk_reads": 30,
                "parsing_schema_name": "HR",
                "first_load_time": "2026-02-23",
            },
            {
                "child_number": 1,
                "plan_hash_value": 222,
                "executions": 200,
                "elapsed_sec": 4.5,
                "cpu_sec": 2.2,
                "buffer_gets": 10000,
                "disk_reads": 20,
                "parsing_schema_name": "HR",
                "first_load_time": "2026-02-23",
            },
        ]
        with patch.object(executor, "_query", side_effect=[agg, children]):
            tc = ToolCall("id1", "get_sql_stats", {"database_id": "dev", "sql_id": "abc123"})
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert data["aggregated"]["total_executions"] == 500
        assert len(data["children"]) == 2
        assert "binds" not in data  # include_binds defaults to false

    def test_sql_stats_with_binds(self, executor):
        agg = [{"sql_id": "abc123", "total_executions": 100}]
        children = []
        binds = [
            {
                "child_number": 0,
                "name": ":id",
                "position": 1,
                "datatype_string": "NUMBER",
                "value_string": "42",
                "last_captured": "2026-02-23",
            }
        ]
        with patch.object(executor, "_query", side_effect=[agg, children, binds]):
            tc = ToolCall(
                "id1",
                "get_sql_stats",
                {
                    "database_id": "dev",
                    "sql_id": "abc123",
                    "include_binds": True,
                },
            )
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert "binds" in data
        assert len(data["binds"]) == 1
        assert data["binds"][0]["value_string"] == "42"


class TestTableStatsHandler:
    @pytest.fixture
    def executor(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return DBAToolExecutor(settings)

    def test_table_stats_returns_data(self, executor):
        tables = [
            {
                "owner": "HR",
                "table_name": "EMPLOYEES",
                "num_rows": 107,
                "blocks": 5,
                "avg_row_len": 69,
                "last_analyzed": "2026-02-20",
                "partitioned": "NO",
                "temporary": "N",
                "compression": "DISABLED",
                "estimated_size_mb": 0.1,
                "stale_stats": "NO",
            }
        ]
        cols = [
            {
                "owner": "HR",
                "column_name": "EMPLOYEE_ID",
                "num_distinct": 107,
                "num_nulls": 0,
                "density": 0.009,
                "histogram": "NONE",
                "num_buckets": 1,
                "last_analyzed": "2026-02-20",
            }
        ]
        with patch.object(executor, "_query", side_effect=[tables, cols]):
            tc = ToolCall(
                "id1",
                "get_table_stats",
                {
                    "database_id": "dev",
                    "table_name": "EMPLOYEES",
                },
            )
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert data["table_name"] == "EMPLOYEES"
        assert data["tables"][0]["num_rows"] == 107
        assert len(data["column_stats"]) == 1
        assert "partitioning" not in data  # not partitioned

    def test_table_stats_partitioned(self, executor):
        tables = [
            {
                "owner": "HR",
                "table_name": "SALES",
                "num_rows": 1000000,
                "blocks": 5000,
                "avg_row_len": 100,
                "last_analyzed": "2026-02-20",
                "partitioned": "YES",
                "temporary": "N",
                "compression": "DISABLED",
                "estimated_size_mb": 40.0,
                "stale_stats": "NO",
            }
        ]
        cols = []
        parts = [
            {
                "table_owner": "HR",
                "table_name": "SALES",
                "partitioning_type": "RANGE",
                "subpartitioning_type": "NONE",
                "partition_count": 12,
            }
        ]
        with patch.object(executor, "_query", side_effect=[tables, cols, parts]):
            tc = ToolCall(
                "id1",
                "get_table_stats",
                {
                    "database_id": "dev",
                    "table_name": "SALES",
                },
            )
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert "partitioning" in data
        assert data["partitioning"][0]["partitioning_type"] == "RANGE"


class TestIndexInfoHandler:
    @pytest.fixture
    def executor(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return DBAToolExecutor(settings)

    def test_index_info_returns_data(self, executor):
        indexes = [
            {
                "index_owner": "HR",
                "index_name": "EMP_PK",
                "index_type": "NORMAL",
                "table_owner": "HR",
                "table_name": "EMPLOYEES",
                "uniqueness": "UNIQUE",
                "status": "VALID",
                "blevel": 1,
                "leaf_blocks": 1,
                "distinct_keys": 107,
                "clustering_factor": 2,
                "index_rows": 107,
                "table_rows": 107,
                "clustering_pct": 1.9,
                "last_analyzed": "2026-02-20",
            }
        ]
        columns = [
            {
                "index_owner": "HR",
                "index_name": "EMP_PK",
                "column_name": "EMPLOYEE_ID",
                "column_position": 1,
                "descend": "ASC",
            }
        ]
        usage = []
        with patch.object(executor, "_query", side_effect=[indexes, columns, usage]):
            tc = ToolCall(
                "id1",
                "get_index_info",
                {
                    "database_id": "dev",
                    "table_name": "EMPLOYEES",
                },
            )
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert data["table_name"] == "EMPLOYEES"
        assert data["indexes"][0]["clustering_pct"] == 1.9
        assert data["index_columns"][0]["column_name"] == "EMPLOYEE_ID"

    def test_index_usage_graceful_fail(self, executor):
        """V$OBJECT_USAGE failure should return empty list, not crash."""
        indexes = [
            {
                "index_owner": "HR",
                "index_name": "EMP_PK",
                "index_type": "NORMAL",
                "table_owner": "HR",
                "table_name": "EMPLOYEES",
                "uniqueness": "UNIQUE",
                "status": "VALID",
                "blevel": 1,
                "leaf_blocks": 1,
                "distinct_keys": 107,
                "clustering_factor": 2,
                "index_rows": 107,
                "table_rows": 107,
                "clustering_pct": 1.9,
                "last_analyzed": "2026-02-20",
            }
        ]
        columns = [
            {
                "index_owner": "HR",
                "index_name": "EMP_PK",
                "column_name": "EMPLOYEE_ID",
                "column_position": 1,
                "descend": "ASC",
            }
        ]

        call_count = [0]

        def mock_query(db_id, sql, params=None):
            call_count[0] += 1
            if call_count[0] <= 2:
                return [indexes[0]] if call_count[0] == 1 else columns
            raise Exception("ORA-00942: table or view does not exist")

        with patch.object(executor, "_query", side_effect=mock_query):
            tc = ToolCall(
                "id1",
                "get_index_info",
                {
                    "database_id": "dev",
                    "table_name": "EMPLOYEES",
                },
            )
            result = executor.execute(tc)

        assert result.is_error is False
        data = json.loads(result.content)
        assert data["usage_monitoring"] == []


class TestSessionInfoHandler:
    @pytest.fixture
    def executor(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return DBAToolExecutor(settings)

    def test_session_info_returns_data(self, executor):
        session = [
            {
                "sid": 123,
                "serial#": 456,
                "username": "HR",
                "status": "ACTIVE",
                "osuser": "oracle",
                "machine": "dbhost",
                "program": "sqlplus",
                "module": None,
                "action": None,
                "type": "USER",
                "sql_id": "xyz789",
                "prev_sql_id": None,
                "event": "db file sequential read",
                "wait_class": "User I/O",
                "seconds_in_wait": 2,
                "state": "WAITING",
                "blocking_session": None,
                "blocking_session_status": None,
                "logon_time": "2026-02-23 10:00:00",
                "os_pid": "12345",
                "pga_used_mb": 5.2,
                "pga_alloc_mb": 8.0,
            }
        ]
        current_wait = [
            {
                "sid": 123,
                "event": "db file sequential read",
                "wait_class": "User I/O",
                "state": "WAITING",
                "p1text": "file#",
                "p1": 4,
                "p2text": "block#",
                "p2": 1234,
                "p3text": "blocks",
                "p3": 1,
                "seconds_in_wait": 2,
                "wait_time_micro": 2000000,
            }
        ]
        events = [
            {
                "event": "db file sequential read",
                "wait_class": "User I/O",
                "total_waits": 500,
                "time_waited_sec": 12.5,
                "average_wait": 0.025,
            }
        ]
        current_sql = [
            {
                "sql_id": "xyz789",
                "sql_text": "SELECT * FROM employees",
                "executions": 5,
                "elapsed_sec": 3.2,
                "cpu_sec": 1.1,
                "buffer_gets": 2000,
                "disk_reads": 100,
            }
        ]

        with patch.object(
            executor, "_query", side_effect=[session, current_wait, events, current_sql]
        ):
            tc = ToolCall("id1", "get_session_info", {"database_id": "dev", "sid": 123})
            result = executor.execute(tc)

        data = json.loads(result.content)
        assert data["sid"] == 123
        assert data["session"]["username"] == "HR"
        assert data["session"]["pga_used_mb"] == 5.2
        assert data["current_wait"]["p1text"] == "file#"
        assert data["current_sql"]["sql_id"] == "xyz789"


class TestTopSqlHandler:
    @pytest.fixture
    def executor(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return DBAToolExecutor(settings)

    def test_top_sql_valid_metric(self, executor):
        rows = [
            {
                "sql_id": "aaa111",
                "sql_text": "SELECT ...",
                "parsing_schema_name": "HR",
                "executions": 1000,
                "elapsed_sec": 50.0,
                "cpu_sec": 30.0,
                "buffer_gets": 500000,
                "disk_reads": 1000,
                "avg_elapsed_sec": 0.05,
                "avg_cpu_sec": 0.03,
                "avg_buffer_gets": 500.0,
            },
        ]
        with patch.object(executor, "_query", return_value=rows):
            tc = ToolCall(
                "id1",
                "get_top_sql",
                {
                    "database_id": "dev",
                    "metric": "cpu_time",
                },
            )
            result = executor.execute(tc)

        assert result.is_error is False
        data = json.loads(result.content)
        assert data["metric"] == "cpu_time"
        assert data["top_n"] == 10  # default
        assert data["exclude_sys"] is True  # default
        assert len(data["top_sql"]) == 1

    def test_top_sql_invalid_metric(self, executor):
        """Invalid metric must return error, not execute SQL."""
        tc = ToolCall(
            "id1",
            "get_top_sql",
            {
                "database_id": "dev",
                "metric": "drop_table",
            },
        )
        result = executor.execute(tc)

        # Not is_error=True because the handler returns an error dict, not raises
        data = json.loads(result.content)
        assert "error" in data
        assert "Invalid metric" in data["error"]
        assert "drop_table" in data["error"]

    def test_top_sql_exclude_sys(self, executor):
        """exclude_sys=true should filter SYS/SYSTEM."""
        queries_captured = []

        def capture_query(db_id, sql, params=None):
            queries_captured.append(sql)
            return []

        with patch.object(executor, "_query", side_effect=capture_query):
            tc = ToolCall(
                "id1",
                "get_top_sql",
                {
                    "database_id": "dev",
                    "metric": "elapsed_time",
                    "exclude_sys": True,
                },
            )
            executor.execute(tc)

        assert len(queries_captured) == 1
        assert "SYS" in queries_captured[0]
        assert "SYSTEM" in queries_captured[0]

    def test_top_sql_include_sys(self, executor):
        """exclude_sys=false should NOT filter SYS/SYSTEM."""
        queries_captured = []

        def capture_query(db_id, sql, params=None):
            queries_captured.append(sql)
            return []

        with patch.object(executor, "_query", side_effect=capture_query):
            tc = ToolCall(
                "id1",
                "get_top_sql",
                {
                    "database_id": "dev",
                    "metric": "buffer_gets",
                    "exclude_sys": False,
                },
            )
            executor.execute(tc)

        assert len(queries_captured) == 1
        assert "SYS" not in queries_captured[0]


class TestWaitEventsHandler:
    @pytest.fixture
    def executor(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return DBAToolExecutor(settings)

    def test_wait_events_returns_all_sections(self, executor):
        system_waits = [
            {
                "event": "db file sequential read",
                "wait_class": "User I/O",
                "total_waits": 100000,
                "time_waited_sec": 500.0,
                "avg_wait_sec": 0.005,
                "pct_of_non_idle": 45.2,
            }
        ]
        active_waits = [
            {"event": "db file sequential read", "wait_class": "User I/O", "session_count": 3}
        ]
        block_waits = [{"class": "data block", "count": 50000, "time": 2500}]
        sys_stats = [
            {"name": "DB CPU", "value": 123456},
            {"name": "DB time", "value": 234567},
            {"name": "physical reads", "value": 100000},
        ]

        with patch.object(
            executor,
            "_query",
            side_effect=[
                system_waits,
                active_waits,
                block_waits,
                sys_stats,
            ],
        ):
            tc = ToolCall("id1", "get_wait_events", {"database_id": "dev"})
            result = executor.execute(tc)

        assert result.is_error is False
        data = json.loads(result.content)
        assert len(data["top_system_waits"]) == 1
        assert data["top_system_waits"][0]["pct_of_non_idle"] == 45.2
        assert data["active_session_waits"][0]["session_count"] == 3
        assert data["block_class_waits"][0]["class"] == "data block"
        assert data["system_stats"]["DB CPU"] == 123456


class TestV4Dispatch:
    """Verify all 7 new tool names dispatch to correct handlers."""

    @pytest.fixture
    def executor(self):
        settings = MagicMock()
        db_cfg = MagicMock()
        db_cfg.connection_string = "oracle://sys@localhost:1521/FREEPDB1"
        db_cfg.password = "test123"
        db_cfg.username = "sys"
        settings.get_database.return_value = db_cfg
        return DBAToolExecutor(settings)

    @pytest.mark.parametrize(
        "tool_name,args",
        [
            ("get_sql_plan", {"database_id": "dev", "sql_id": "abc"}),
            ("get_sql_stats", {"database_id": "dev", "sql_id": "abc"}),
            ("get_table_stats", {"database_id": "dev", "table_name": "T"}),
            ("get_index_info", {"database_id": "dev", "table_name": "T"}),
            ("get_session_info", {"database_id": "dev", "sid": 1}),
            ("get_top_sql", {"database_id": "dev", "metric": "cpu_time"}),
            ("get_wait_events", {"database_id": "dev"}),
        ],
    )
    def test_dispatch_v4_tool(self, executor, tool_name, args):
        """Each v4 tool name should dispatch (not return 'Unknown tool')."""
        with patch.object(executor, "_query", return_value=[]):
            tc = ToolCall("id1", tool_name, args)
            result = executor.execute(tc)

        assert "Unknown tool" not in result.content

    def test_all_tools_read_only(self):
        """Verify no tool handler uses execute_write (conceptual check)."""
        import inspect

        source = inspect.getsource(DBAToolExecutor)
        assert "execute_write" not in source
        # Also verify no tool handler creates its own connection with read_only=False
        assert "read_only=False" not in source
