"""DBA tools for the agentic researcher (v2.1 + v4.0).

Provides 12 read-only Oracle investigation tools that the LLM can call
during multi-turn research.  Built into Sentri — zero user configuration.

v2.1 tools (5): tablespace_info, db_parameters, storage_info, instance_info, query_database
v4.0 tools (7): sql_plan, sql_stats, table_stats, index_info, session_info, top_sql, wait_events

Safety:
  - All connections are read_only=True
  - query_database() rejects DML/DDL (SQL safety guard)
  - get_top_sql() validates metric against allowlist (no SQL injection via ORDER BY)
  - 10s timeout per tool call
  - 50 row limit on query results
  - V$ACTIVE_SESSION_HISTORY excluded (requires Diagnostics Pack license)
"""

from __future__ import annotations

import json
import logging
import re

from sentri.core.llm_interface import ToolCall, ToolDefinition, ToolResult
from sentri.oracle.connection_pool import OracleConnectionPool
from sentri.oracle.query_runner import QueryRunner

logger = logging.getLogger("sentri.llm.tools")

# Max rows returned by any tool query
_MAX_ROWS = 50
_TOOL_TIMEOUT = 10  # seconds

# SQL patterns that must be rejected in query_database
_UNSAFE_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Tool definitions (JSON Schema for LLM)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_tablespace_info",
        description=(
            "Get detailed tablespace information: type (bigfile/smallfile), "
            "status, usage percentage, and all datafiles with paths, sizes, "
            "and autoextend settings. Use this FIRST for tablespace alerts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier (e.g. 'sentri-dev')",
                },
                "tablespace_name": {
                    "type": "string",
                    "description": "The tablespace name to investigate",
                },
            },
            "required": ["database_id", "tablespace_name"],
        },
    ),
    ToolDefinition(
        name="get_db_parameters",
        description=(
            "Get Oracle init parameters by name. Use this to check "
            "db_create_file_dest (OMF), db_block_size, undo_tablespace, "
            "sga_target, or any other parameter."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "param_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of parameter names to query",
                },
            },
            "required": ["database_id", "param_names"],
        },
    ),
    ToolDefinition(
        name="get_storage_info",
        description=(
            "Get datafile storage details for a tablespace: file paths, sizes, "
            "autoextend, max size. Use this to determine correct paths for "
            "new datafiles or resize operations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "tablespace_name": {
                    "type": "string",
                    "description": "The tablespace to get storage details for",
                },
            },
            "required": ["database_id", "tablespace_name"],
        },
    ),
    ToolDefinition(
        name="get_instance_info",
        description=(
            "Get database instance information: Oracle version, hostname, "
            "RAC status, Data Guard role, CDB/PDB architecture, open mode. "
            "Use this to understand the database environment."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
            },
            "required": ["database_id"],
        },
    ),
    ToolDefinition(
        name="query_database",
        description=(
            "Execute any read-only SQL SELECT query against the database. "
            "Only SELECT statements are allowed — DML/DDL is blocked. "
            "Use this for any investigation not covered by the other tools."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "sql": {
                    "type": "string",
                    "description": "A SELECT query to execute (DML/DDL blocked)",
                },
            },
            "required": ["database_id", "sql"],
        },
    ),
    # --- v4.0 Enhanced DBA Tools ---
    ToolDefinition(
        name="get_sql_plan",
        description=(
            "Get the execution plan for a SQL_ID. Shows operations, costs, "
            "access/filter predicates. Use this to understand WHY a SQL is slow."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "sql_id": {
                    "type": "string",
                    "description": "The SQL_ID to get the plan for",
                },
                "child_number": {
                    "type": "integer",
                    "description": "Child cursor number (default 0)",
                },
            },
            "required": ["database_id", "sql_id"],
        },
    ),
    ToolDefinition(
        name="get_sql_stats",
        description=(
            "Get performance statistics for a SQL_ID: elapsed time, CPU, "
            "buffer gets, disk reads, per-execution averages. Optionally "
            "includes captured bind variable values."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "sql_id": {
                    "type": "string",
                    "description": "The SQL_ID to get stats for",
                },
                "include_binds": {
                    "type": "boolean",
                    "description": "Include captured bind values (default false)",
                },
            },
            "required": ["database_id", "sql_id"],
        },
    ),
    ToolDefinition(
        name="get_table_stats",
        description=(
            "Get optimizer statistics for a table: row count, last analyzed date, "
            "stale flag, partitioning info, column-level histograms. Use this to "
            "check if stale stats caused a bad plan."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "table_name": {
                    "type": "string",
                    "description": "The table name to get stats for",
                },
                "owner": {
                    "type": "string",
                    "description": "Schema owner (optional — defaults to all schemas)",
                },
            },
            "required": ["database_id", "table_name"],
        },
    ),
    ToolDefinition(
        name="get_index_info",
        description=(
            "Get index definitions, columns, clustering factor, and usage "
            "monitoring for a table. Use this to find missing or unused indexes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "table_name": {
                    "type": "string",
                    "description": "The table to get index info for",
                },
                "owner": {
                    "type": "string",
                    "description": "Schema owner (optional — defaults to all schemas)",
                },
            },
            "required": ["database_id", "table_name"],
        },
    ),
    ToolDefinition(
        name="get_session_info",
        description=(
            "Get full session diagnostics: current SQL, wait event with decoded "
            "parameters, blocking chain, PGA usage, OS PID, and historical waits. "
            "Use this for session_blocker or long_running_sql alerts."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "sid": {
                    "type": "integer",
                    "description": "The session ID (SID) to investigate",
                },
            },
            "required": ["database_id", "sid"],
        },
    ),
    ToolDefinition(
        name="get_top_sql",
        description=(
            "Find the top N SQL statements by a performance metric. "
            "Valid metrics: cpu_time, elapsed_time, buffer_gets, disk_reads, "
            "executions, parse_calls. Start here for performance investigations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "metric": {
                    "type": "string",
                    "enum": [
                        "cpu_time",
                        "elapsed_time",
                        "buffer_gets",
                        "disk_reads",
                        "executions",
                        "parse_calls",
                    ],
                    "description": "The metric to sort by",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Number of results (default 10)",
                },
                "exclude_sys": {
                    "type": "boolean",
                    "description": "Exclude SYS/SYSTEM schemas (default true)",
                },
            },
            "required": ["database_id", "metric"],
        },
    ),
    ToolDefinition(
        name="get_wait_events",
        description=(
            "Get system-wide wait event analysis: top non-idle waits, current "
            "active session waits, block-class breakdown, and key system stats. "
            "Start here for CPU high or IO investigations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "database_id": {
                    "type": "string",
                    "description": "The database identifier",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Number of top wait events to return (default 20)",
                },
            },
            "required": ["database_id"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Metric validation for get_top_sql (prevents SQL injection via ORDER BY)
# ---------------------------------------------------------------------------

_VALID_TOP_SQL_METRICS = {
    "cpu_time": "cpu_time",
    "elapsed_time": "elapsed_time",
    "buffer_gets": "buffer_gets",
    "disk_reads": "disk_reads",
    "executions": "executions",
    "parse_calls": "parse_calls",
}


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------


class DBAToolExecutor:
    """Executes DBA tool calls against Oracle databases.

    Creates its own connection pool and query runner.
    Each tool call opens and closes its own connection.
    """

    def __init__(self, settings):
        """
        Args:
            settings: Sentri Settings object (has .databases, .get_database())
        """
        self._settings = settings
        self._pool = OracleConnectionPool()
        self._runner = QueryRunner(timeout_seconds=_TOOL_TIMEOUT)

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """Dispatch a tool call to the appropriate handler.

        Always returns a ToolResult — errors are caught and reported.
        """
        handler = {
            "get_tablespace_info": self._handle_tablespace_info,
            "get_db_parameters": self._handle_db_parameters,
            "get_storage_info": self._handle_storage_info,
            "get_instance_info": self._handle_instance_info,
            "query_database": self._handle_query_database,
            # v4.0 tools
            "get_sql_plan": self._handle_sql_plan,
            "get_sql_stats": self._handle_sql_stats,
            "get_table_stats": self._handle_table_stats,
            "get_index_info": self._handle_index_info,
            "get_session_info": self._handle_session_info,
            "get_top_sql": self._handle_top_sql,
            "get_wait_events": self._handle_wait_events,
        }.get(tool_call.name)

        if not handler:
            return ToolResult(
                tool_call_id=tool_call.tool_call_id,
                name=tool_call.name,
                content=json.dumps({"error": f"Unknown tool: {tool_call.name}"}),
                is_error=True,
            )

        try:
            result_data = handler(tool_call.arguments)
            return ToolResult(
                tool_call_id=tool_call.tool_call_id,
                name=tool_call.name,
                content=json.dumps(result_data, default=str),
            )
        except Exception as e:
            logger.warning("Tool %s failed: %s", tool_call.name, e)
            return ToolResult(
                tool_call_id=tool_call.tool_call_id,
                name=tool_call.name,
                content=json.dumps({"error": str(e)}),
                is_error=True,
            )

    def _get_connection(self, database_id: str):
        """Get a read-only connection to the specified database."""
        db_cfg = self._settings.get_database(database_id)
        if not db_cfg:
            raise ValueError(f"No configuration found for database '{database_id}'")

        return self._pool.get_connection(
            database_id=database_id,
            connection_string=db_cfg.connection_string,
            password=db_cfg.password,
            username=db_cfg.username if db_cfg.username else None,
            read_only=True,
        )

    def _query(self, database_id: str, sql: str, params: dict | None = None) -> list[dict]:
        """Execute a read-only query and return results (max _MAX_ROWS)."""
        conn = self._get_connection(database_id)
        try:
            rows = self._runner.execute_read(conn, sql, params, timeout=_TOOL_TIMEOUT)
            return rows[:_MAX_ROWS]
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # --- Tool handlers ---

    def _handle_tablespace_info(self, args: dict) -> dict:
        """Get tablespace type, usage, and datafile details."""
        db_id = args["database_id"]
        tbs_name = args["tablespace_name"].upper()

        # Tablespace metadata (bigfile, type, status)
        tbs_rows = self._query(
            db_id,
            """
            SELECT tablespace_name, status, contents, logging,
                   extent_management, segment_space_management, bigfile
            FROM dba_tablespaces
            WHERE tablespace_name = :tbs
        """,
            {"tbs": tbs_name},
        )

        # Usage percentage
        usage_rows = self._query(
            db_id,
            """
            SELECT tablespace_name,
                   ROUND(used_percent, 2) AS used_percent
            FROM dba_tablespace_usage_metrics
            WHERE tablespace_name = :tbs
        """,
            {"tbs": tbs_name},
        )

        # Datafiles with paths
        df_rows = self._query(
            db_id,
            """
            SELECT file_name, file_id, tablespace_name,
                   ROUND(bytes / 1024 / 1024, 0) AS size_mb,
                   autoextensible,
                   ROUND(maxbytes / 1024 / 1024, 0) AS max_mb,
                   status
            FROM dba_data_files
            WHERE tablespace_name = :tbs
            ORDER BY file_id
        """,
            {"tbs": tbs_name},
        )

        # Also check temp files if it's a TEMP tablespace
        tf_rows = []
        if tbs_rows and tbs_rows[0].get("contents") == "TEMPORARY":
            tf_rows = self._query(
                db_id,
                """
                SELECT file_name, file_id, tablespace_name,
                       ROUND(bytes / 1024 / 1024, 0) AS size_mb,
                       autoextensible,
                       ROUND(maxbytes / 1024 / 1024, 0) AS max_mb
                FROM dba_temp_files
                WHERE tablespace_name = :tbs
                ORDER BY file_id
            """,
                {"tbs": tbs_name},
            )

        return {
            "tablespace": tbs_rows[0] if tbs_rows else None,
            "usage": usage_rows[0] if usage_rows else None,
            "datafiles": df_rows,
            "tempfiles": tf_rows,
        }

    def _handle_db_parameters(self, args: dict) -> dict:
        """Get specific Oracle init parameters."""
        db_id = args["database_id"]
        raw_names = args.get("param_names", [])

        # Handle Gemini sending string "['name']" instead of array ["name"]
        if isinstance(raw_names, str):
            import ast

            try:
                raw_names = ast.literal_eval(raw_names)
            except (ValueError, SyntaxError):
                # Single param name as plain string
                raw_names = [raw_names]

        param_names = [p.lower().strip() for p in raw_names if p]

        if not param_names:
            return {"parameters": {}}

        # Build IN clause with bind vars
        binds = {}
        conditions = []
        for i, name in enumerate(param_names):
            key = f"p{i}"
            binds[key] = name
            conditions.append(f":{key}")

        sql = f"""
            SELECT name, value, isdefault
            FROM v$parameter
            WHERE name IN ({', '.join(conditions)})
        """

        rows = self._query(db_id, sql, binds)
        return {
            "parameters": {
                r["name"]: {"value": r["value"], "is_default": r.get("isdefault", "TRUE")}
                for r in rows
            }
        }

    def _handle_storage_info(self, args: dict) -> dict:
        """Get detailed datafile storage info for a tablespace."""
        db_id = args["database_id"]
        tbs_name = args["tablespace_name"].upper()

        df_rows = self._query(
            db_id,
            """
            SELECT file_name, file_id,
                   ROUND(bytes / 1024 / 1024, 0) AS size_mb,
                   ROUND(user_bytes / 1024 / 1024, 0) AS usable_mb,
                   autoextensible,
                   ROUND(maxbytes / 1024 / 1024, 0) AS max_mb,
                   ROUND(increment_by * (SELECT value FROM v$parameter WHERE name = 'db_block_size') / 1024 / 1024, 0) AS autoextend_increment_mb,
                   online_status
            FROM dba_data_files
            WHERE tablespace_name = :tbs
            ORDER BY file_id
        """,
            {"tbs": tbs_name},
        )

        # Free space per datafile
        free_rows = self._query(
            db_id,
            """
            SELECT file_id,
                   ROUND(SUM(bytes) / 1024 / 1024, 0) AS free_mb
            FROM dba_free_space
            WHERE tablespace_name = :tbs
            GROUP BY file_id
        """,
            {"tbs": tbs_name},
        )

        free_map = {r["file_id"]: r["free_mb"] for r in free_rows}
        for df in df_rows:
            df["free_mb"] = free_map.get(df["file_id"], 0)

        return {
            "tablespace_name": tbs_name,
            "datafile_count": len(df_rows),
            "datafiles": df_rows,
        }

    def _handle_instance_info(self, args: dict) -> dict:
        """Get database and instance identity information."""
        db_id = args["database_id"]

        db_rows = self._query(
            db_id,
            """
            SELECT db_unique_name, name, database_role, open_mode,
                   log_mode, flashback_on, cdb, protection_mode,
                   platform_name
            FROM v$database
        """,
        )

        inst_rows = self._query(
            db_id,
            """
            SELECT instance_name, host_name, version, status,
                   archiver, instance_role, active_state
            FROM v$instance
        """,
        )

        # PDB info (CDB only)
        pdb_rows = []
        try:
            pdb_rows = self._query(
                db_id,
                """
                SELECT con_id, name, open_mode, restricted
                FROM v$pdbs
                ORDER BY con_id
            """,
            )
        except Exception:
            pass  # Not a CDB or no permissions

        return {
            "database": db_rows[0] if db_rows else None,
            "instance": inst_rows[0] if inst_rows else None,
            "pdbs": pdb_rows,
        }

    def _handle_query_database(self, args: dict) -> dict:
        """Execute an arbitrary SELECT query (safety-guarded)."""
        db_id = args["database_id"]
        sql = args.get("sql", "").strip()

        if not sql:
            return {"error": "Empty SQL query"}

        # Safety guard: reject anything that isn't a SELECT
        if _UNSAFE_SQL_RE.search(sql):
            return {
                "error": "Only SELECT queries are allowed. "
                "DML/DDL (INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, etc.) is blocked."
            }

        # Extra safety: must start with SELECT or WITH
        first_word = sql.split()[0].upper() if sql.split() else ""
        if first_word not in ("SELECT", "WITH"):
            return {"error": f"Query must start with SELECT or WITH, got: {first_word}"}

        rows = self._query(db_id, sql)
        return {
            "row_count": len(rows),
            "rows": rows,
        }

    # --- v4.0 Tool handlers ---

    def _handle_sql_plan(self, args: dict) -> dict:
        """Get execution plan for a SQL_ID from V$SQL_PLAN."""
        db_id = args["database_id"]
        sql_id = args["sql_id"]
        child_number = args.get("child_number", 0)

        # SQL metadata
        sql_meta = self._query(
            db_id,
            """
            SELECT sql_id, SUBSTR(sql_text, 1, 500) AS sql_text,
                   plan_hash_value, executions,
                   ROUND(elapsed_time / 1e6, 3) AS elapsed_sec,
                   ROUND(cpu_time / 1e6, 3) AS cpu_sec,
                   buffer_gets, disk_reads
            FROM v$sql
            WHERE sql_id = :sql_id AND child_number = :child
        """,
            {"sql_id": sql_id, "child": child_number},
        )

        # Plan steps
        plan_steps = self._query(
            db_id,
            """
            SELECT id, parent_id, operation, options, object_name,
                   cost, cardinality, bytes, depth,
                   access_predicates, filter_predicates
            FROM v$sql_plan
            WHERE sql_id = :sql_id AND child_number = :child
            ORDER BY id
        """,
            {"sql_id": sql_id, "child": child_number},
        )

        return {
            "sql_id": sql_id,
            "child_number": child_number,
            "metadata": sql_meta[0] if sql_meta else None,
            "plan_steps": plan_steps,
        }

    def _handle_sql_stats(self, args: dict) -> dict:
        """Get performance stats for a SQL_ID from V$SQL."""
        db_id = args["database_id"]
        sql_id = args["sql_id"]
        include_binds = args.get("include_binds", False)

        # Aggregated stats across child cursors
        agg_stats = self._query(
            db_id,
            """
            SELECT sql_id,
                   SUBSTR(MIN(sql_text), 1, 500) AS sql_text,
                   SUM(executions) AS total_executions,
                   ROUND(SUM(elapsed_time) / 1e6, 3) AS total_elapsed_sec,
                   ROUND(SUM(cpu_time) / 1e6, 3) AS total_cpu_sec,
                   SUM(buffer_gets) AS total_buffer_gets,
                   SUM(disk_reads) AS total_disk_reads,
                   SUM(rows_processed) AS total_rows,
                   ROUND(SUM(elapsed_time) / NULLIF(SUM(executions), 0) / 1e6, 6) AS avg_elapsed_sec,
                   ROUND(SUM(buffer_gets) / NULLIF(SUM(executions), 0), 1) AS avg_buffer_gets,
                   COUNT(*) AS child_cursor_count
            FROM v$sql
            WHERE sql_id = :sql_id
            GROUP BY sql_id
        """,
            {"sql_id": sql_id},
        )

        # Per-child cursor breakdown
        children = self._query(
            db_id,
            """
            SELECT child_number, plan_hash_value,
                   executions,
                   ROUND(elapsed_time / 1e6, 3) AS elapsed_sec,
                   ROUND(cpu_time / 1e6, 3) AS cpu_sec,
                   buffer_gets, disk_reads,
                   parsing_schema_name, first_load_time
            FROM v$sql
            WHERE sql_id = :sql_id
            ORDER BY child_number
        """,
            {"sql_id": sql_id},
        )

        result = {
            "sql_id": sql_id,
            "aggregated": agg_stats[0] if agg_stats else None,
            "children": children,
        }

        # Optional bind capture
        if include_binds:
            binds = self._query(
                db_id,
                """
                SELECT child_number, name, position, datatype_string,
                       value_string, last_captured
                FROM v$sql_bind_capture
                WHERE sql_id = :sql_id
                ORDER BY child_number, position
            """,
                {"sql_id": sql_id},
            )
            result["binds"] = binds

        return result

    def _handle_table_stats(self, args: dict) -> dict:
        """Get optimizer statistics for a table from DBA_TABLES."""
        db_id = args["database_id"]
        table_name = args["table_name"].upper()
        owner = args.get("owner", "").upper() or None

        # Table metadata + stats
        owner_filter = "AND t.owner = :owner" if owner else ""
        params = {"tname": table_name}
        if owner:
            params["owner"] = owner

        table_rows = self._query(
            db_id,
            f"""
            SELECT t.owner, t.table_name, t.num_rows, t.blocks, t.avg_row_len,
                   t.last_analyzed, t.partitioned, t.temporary, t.compression,
                   ROUND(t.blocks * (SELECT value FROM v$parameter WHERE name = 'db_block_size') / 1024 / 1024, 1) AS estimated_size_mb,
                   s.stale_stats
            FROM dba_tables t
            LEFT JOIN dba_tab_statistics s
              ON t.owner = s.owner AND t.table_name = s.table_name AND s.partition_name IS NULL
            WHERE t.table_name = :tname {owner_filter}
            ORDER BY t.owner
        """,
            params,
        )

        # Column-level stats
        col_stats = self._query(
            db_id,
            f"""
            SELECT owner, column_name, num_distinct, num_nulls,
                   density, histogram, num_buckets, last_analyzed
            FROM dba_tab_col_statistics
            WHERE table_name = :tname {owner_filter}
            ORDER BY owner, column_name
        """,
            params,
        )

        result = {
            "table_name": table_name,
            "tables": table_rows,
            "column_stats": col_stats,
        }

        # Partition info (only if partitioned)
        if table_rows and any(r.get("partitioned") == "YES" for r in table_rows):
            part_rows = self._query(
                db_id,
                f"""
                SELECT table_owner, table_name, partitioning_type,
                       subpartitioning_type, partition_count
                FROM dba_part_tables
                WHERE table_name = :tname {owner_filter.replace('t.owner', 'table_owner')}
            """,
                params,
            )
            result["partitioning"] = part_rows

        return result

    def _handle_index_info(self, args: dict) -> dict:
        """Get index definitions, columns, and usage for a table."""
        db_id = args["database_id"]
        table_name = args["table_name"].upper()
        owner = args.get("owner", "").upper() or None

        owner_filter = "AND i.table_owner = :owner" if owner else ""
        params = {"tname": table_name}
        if owner:
            params["owner"] = owner

        # Index metadata
        indexes = self._query(
            db_id,
            f"""
            SELECT i.owner AS index_owner, i.index_name, i.index_type,
                   i.table_owner, i.table_name, i.uniqueness, i.status,
                   i.blevel, i.leaf_blocks, i.distinct_keys,
                   i.clustering_factor, i.num_rows AS index_rows,
                   t.num_rows AS table_rows,
                   CASE WHEN t.num_rows > 0
                        THEN ROUND(i.clustering_factor / t.num_rows * 100, 1)
                        ELSE NULL END AS clustering_pct,
                   i.last_analyzed
            FROM dba_indexes i
            LEFT JOIN dba_tables t
              ON i.table_owner = t.owner AND i.table_name = t.table_name
            WHERE i.table_name = :tname {owner_filter}
            ORDER BY i.owner, i.index_name
        """,
            params,
        )

        # Index columns
        columns = self._query(
            db_id,
            f"""
            SELECT ic.index_owner, ic.index_name, ic.column_name,
                   ic.column_position, ic.descend
            FROM dba_ind_columns ic
            WHERE ic.table_name = :tname {owner_filter.replace('i.table_owner', 'ic.index_owner')}
            ORDER BY ic.index_owner, ic.index_name, ic.column_position
        """,
            params,
        )

        result = {
            "table_name": table_name,
            "indexes": indexes,
            "index_columns": columns,
        }

        # Index usage monitoring (may fail on older Oracle versions)
        try:
            usage = self._query(
                db_id,
                """
                SELECT ou.owner, ou.name AS index_name, ou.monitoring, ou.used,
                       ou.start_monitoring, ou.end_monitoring
                FROM v$object_usage ou
                JOIN dba_indexes i
                  ON ou.name = i.index_name AND ou.owner = i.owner
                WHERE i.table_name = :tname
            """,
                {"tname": table_name},
            )
            result["usage_monitoring"] = usage
        except Exception:
            result["usage_monitoring"] = []

        return result

    def _handle_session_info(self, args: dict) -> dict:
        """Get full session diagnostics from V$SESSION."""
        db_id = args["database_id"]
        sid = args["sid"]

        # Session + process info
        session = self._query(
            db_id,
            """
            SELECT s.sid, s.serial#, s.username, s.status, s.osuser, s.machine,
                   s.program, s.module, s.action, s.type,
                   s.sql_id, s.prev_sql_id, s.event, s.wait_class,
                   s.seconds_in_wait, s.state, s.blocking_session,
                   s.blocking_session_status, s.logon_time,
                   p.spid AS os_pid,
                   ROUND(p.pga_used_mem / 1024 / 1024, 1) AS pga_used_mb,
                   ROUND(p.pga_alloc_mem / 1024 / 1024, 1) AS pga_alloc_mb
            FROM v$session s
            LEFT JOIN v$process p ON s.paddr = p.addr
            WHERE s.sid = :sid
        """,
            {"sid": sid},
        )

        # Current wait details
        current_wait = self._query(
            db_id,
            """
            SELECT sid, event, wait_class, state,
                   p1text, p1, p2text, p2, p3text, p3,
                   seconds_in_wait, wait_time_micro
            FROM v$session_wait
            WHERE sid = :sid
        """,
            {"sid": sid},
        )

        # Historical waits for this session (top 10 by time)
        session_events = self._query(
            db_id,
            """
            SELECT event, wait_class, total_waits,
                   ROUND(time_waited_micro / 1e6, 3) AS time_waited_sec,
                   average_wait
            FROM v$session_event
            WHERE sid = :sid
            ORDER BY time_waited_micro DESC
            FETCH FIRST 10 ROWS ONLY
        """,
            {"sid": sid},
        )

        result = {
            "sid": sid,
            "session": session[0] if session else None,
            "current_wait": current_wait[0] if current_wait else None,
            "top_session_events": session_events,
        }

        # If session has an active SQL, fetch it
        if session and session[0].get("sql_id"):
            sql_id = session[0]["sql_id"]
            current_sql = self._query(
                db_id,
                """
                SELECT sql_id, SUBSTR(sql_text, 1, 500) AS sql_text,
                       executions,
                       ROUND(elapsed_time / 1e6, 3) AS elapsed_sec,
                       ROUND(cpu_time / 1e6, 3) AS cpu_sec,
                       buffer_gets, disk_reads
                FROM v$sql
                WHERE sql_id = :sql_id
                FETCH FIRST 1 ROWS ONLY
            """,
                {"sql_id": sql_id},
            )
            result["current_sql"] = current_sql[0] if current_sql else None

        return result

    def _handle_top_sql(self, args: dict) -> dict:
        """Find top N SQL by a validated performance metric."""
        db_id = args["database_id"]
        metric = args["metric"]
        top_n = args.get("top_n", 10)
        exclude_sys = args.get("exclude_sys", True)

        # Security: validate metric against allowlist
        if metric not in _VALID_TOP_SQL_METRICS:
            return {
                "error": f"Invalid metric '{metric}'. "
                f"Valid: {list(_VALID_TOP_SQL_METRICS.keys())}",
            }

        order_column = _VALID_TOP_SQL_METRICS[metric]
        sys_filter = "AND parsing_schema_name NOT IN ('SYS', 'SYSTEM')" if exclude_sys else ""

        rows = self._query(
            db_id,
            f"""
            SELECT sql_id, SUBSTR(sql_text, 1, 200) AS sql_text,
                   parsing_schema_name,
                   executions,
                   ROUND(elapsed_time / 1e6, 3) AS elapsed_sec,
                   ROUND(cpu_time / 1e6, 3) AS cpu_sec,
                   buffer_gets, disk_reads,
                   ROUND(elapsed_time / NULLIF(executions, 0) / 1e6, 6) AS avg_elapsed_sec,
                   ROUND(cpu_time / NULLIF(executions, 0) / 1e6, 6) AS avg_cpu_sec,
                   ROUND(buffer_gets / NULLIF(executions, 0), 1) AS avg_buffer_gets
            FROM v$sql
            WHERE executions > 0 {sys_filter}
            ORDER BY {order_column} DESC
            FETCH FIRST :top_n ROWS ONLY
        """,
            {"top_n": top_n},
        )

        return {
            "metric": metric,
            "top_n": top_n,
            "exclude_sys": exclude_sys,
            "sql_count": len(rows),
            "top_sql": rows,
        }

    def _handle_wait_events(self, args: dict) -> dict:
        """Get system-wide wait event analysis."""
        db_id = args["database_id"]
        top_n = args.get("top_n", 20)

        # Top non-idle system waits
        system_waits = self._query(
            db_id,
            """
            SELECT event, wait_class, total_waits,
                   ROUND(time_waited_micro / 1e6, 3) AS time_waited_sec,
                   ROUND(average_wait_micro / 1e6, 6) AS avg_wait_sec,
                   ROUND(time_waited_micro * 100.0 /
                         NULLIF((SELECT SUM(time_waited_micro) FROM v$system_event
                                 WHERE wait_class != 'Idle'), 0), 1) AS pct_of_non_idle
            FROM v$system_event
            WHERE wait_class != 'Idle'
            ORDER BY time_waited_micro DESC
            FETCH FIRST :top_n ROWS ONLY
        """,
            {"top_n": top_n},
        )

        # Current active session waits (what's happening right now)
        active_waits = self._query(
            db_id,
            """
            SELECT event, wait_class, COUNT(*) AS session_count
            FROM v$session
            WHERE status = 'ACTIVE' AND wait_class != 'Idle'
            GROUP BY event, wait_class
            ORDER BY session_count DESC
        """,
        )

        # Block-class breakdown (buffer contention detail)
        block_waits = self._query(
            db_id,
            """
            SELECT class, count, time
            FROM v$waitstat
            WHERE count > 0
            ORDER BY time DESC
        """,
        )

        # Key system stats for context
        sys_stats = self._query(
            db_id,
            """
            SELECT name, value
            FROM v$sysstat
            WHERE name IN (
                'DB time', 'DB CPU', 'physical reads',
                'physical writes', 'redo size',
                'parse count (total)', 'parse count (hard)',
                'user commits', 'user rollbacks',
                'session logical reads'
            )
            ORDER BY name
        """,
        )

        return {
            "top_system_waits": system_waits,
            "active_session_waits": active_waits,
            "block_class_waits": block_waits,
            "system_stats": {r["name"]: r["value"] for r in sys_stats},
        }
