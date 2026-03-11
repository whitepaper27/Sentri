"""Agent 0: The Profiler - Discovers database characteristics on startup.

Runs comprehensive read-only Oracle queries against each configured database
and stores the FULL raw results in a JSON profile (db_config dict).  The LLM
Researcher uses this rich context to generate database-aware SQL — correct
datafile paths, CDB/PDB context, ASM vs filesystem, Data Guard role, etc.

Refresh strategy:
  - Startup: full 16-query profile
  - Scheduled: every profile_refresh_hours (default 24h)
  - On-demand: sentri reload-profile or YAML change

Each query fails gracefully; missing views or permissions just produce an
empty result for that section.
"""

from __future__ import annotations

import logging

from sentri.core.exceptions import ProfileError
from sentri.core.models import DatabaseProfile
from sentri.oracle.connection_pool import OracleConnectionPool
from sentri.oracle.query_runner import QueryRunner

from .base import AgentContext

logger = logging.getLogger("sentri.agents.profiler")

# ---------------------------------------------------------------------------
# Comprehensive DBA discovery queries (all read-only).
# Every query is wrapped in try/except — if a view doesn't exist or the
# user lacks privileges, that section is simply empty.
# ---------------------------------------------------------------------------

QUERIES: dict[str, str] = {
    # ---- Identity & Role ----
    # 1. Database identity, Data Guard role, protection mode
    "db_identity": """
        SELECT db_unique_name, name, dbid, database_role, open_mode,
               log_mode, flashback_on, cdb, con_id,
               protection_mode, switchover_status, dataguard_broker,
               platform_name, created
        FROM v$database
    """,
    # 2. Instance info (Oracle version, hostname, startup time)
    "instance_info": """
        SELECT instance_name, host_name, version, status,
               archiver, log_switch_wait, logins, shutdown_pending,
               instance_role, active_state, startup_time
        FROM v$instance
    """,
    # ---- Size ----
    # 3. Total database size
    "db_size": """
        SELECT ROUND(SUM(bytes) / 1024 / 1024 / 1024, 2) AS total_gb
        FROM v$datafile
    """,
    # ---- ALL Parameters (one query captures everything) ----
    # 4. Every init parameter with a value
    "all_parameters": """
        SELECT name, value, isdefault
        FROM v$parameter
        WHERE value IS NOT NULL
        ORDER BY name
    """,
    # ---- Storage Layout ----
    # 5. ALL datafiles — paths, sizes, tablespace, autoextend
    "datafiles": """
        SELECT file_name, file_id, tablespace_name,
               ROUND(bytes / 1024 / 1024, 0) AS size_mb,
               autoextensible,
               ROUND(maxbytes / 1024 / 1024, 0) AS max_mb,
               status
        FROM dba_data_files
        ORDER BY tablespace_name, file_id
    """,
    # 6. ALL temp files
    "tempfiles": """
        SELECT file_name, file_id, tablespace_name,
               ROUND(bytes / 1024 / 1024, 0) AS size_mb,
               autoextensible,
               ROUND(maxbytes / 1024 / 1024, 0) AS max_mb
        FROM dba_temp_files
        ORDER BY file_id
    """,
    # ---- Tablespaces ----
    # 7. ALL tablespace definitions (type, status, extent mgmt, bigfile)
    "tablespaces": """
        SELECT tablespace_name, status, contents, logging,
               extent_management, segment_space_management, bigfile
        FROM dba_tablespaces
        ORDER BY tablespace_name
    """,
    # 8. ALL tablespace usage
    "tablespace_usage": """
        SELECT tablespace_name,
               ROUND(used_percent, 1) AS used_pct
        FROM dba_tablespace_usage_metrics
        ORDER BY used_percent DESC
    """,
    # ---- Memory ----
    # 9. SGA component sizes
    "sga_info": """
        SELECT name, ROUND(value / 1024 / 1024, 1) AS size_mb
        FROM v$sga
    """,
    # ---- Redo & Archive ----
    # 10. Redo log groups — size, members, status
    "redo_config": """
        SELECT group#, bytes / 1024 / 1024 AS size_mb, members,
               status, sequence#
        FROM v$log
        ORDER BY group#
    """,
    # 11. Active archive log destinations
    "archive_config": """
        SELECT dest_id, dest_name, status, target, destination
        FROM v$archive_dest
        WHERE status != 'INACTIVE'
        ORDER BY dest_id
    """,
    # 12. Flash Recovery Area usage
    "fra_usage": """
        SELECT name,
               ROUND(space_limit / 1024 / 1024 / 1024, 2) AS limit_gb,
               ROUND(space_used / 1024 / 1024 / 1024, 2) AS used_gb,
               ROUND(space_reclaimable / 1024 / 1024 / 1024, 2) AS reclaimable_gb
        FROM v$recovery_file_dest
    """,
    # ---- CDB / PDB ----
    # 13. PDB list (CDB only — fails gracefully on non-CDB)
    "pdb_list": """
        SELECT con_id, name, open_mode, restricted
        FROM v$pdbs
        ORDER BY con_id
    """,
    # ---- ASM ----
    # 14. ASM diskgroups (fails gracefully if no ASM)
    "asm_diskgroups": """
        SELECT name, type, total_mb, free_mb,
               ROUND(free_mb / NULLIF(total_mb, 0) * 100, 1) AS free_pct
        FROM v$asm_diskgroup
    """,
    # ---- Workload ----
    # 15. Recent workload snapshot
    "workload": """
        SELECT metric_name, value
        FROM v$sysmetric
        WHERE group_id = 2
          AND metric_name IN (
              'Physical Reads Per Sec',
              'Physical Writes Per Sec',
              'User Transaction Per Sec',
              'SQL Service Response Time',
              'DB Block Changes Per Sec',
              'Current Logons Count'
          )
    """,
    # ---- Resource Limits ----
    # 16. Processes, sessions, transactions — current vs limit
    "resource_limits": """
        SELECT resource_name, current_utilization, max_utilization, limit_value
        FROM v$resource_limit
        WHERE resource_name IN ('processes', 'sessions', 'transactions')
    """,
    # ---- Session Container ID ----
    # 17. Actual session container ID (v$database.con_id is always 0 — useless)
    #     SYS_CONTEXT returns the PDB con_id (3+ for PDBs, 1 for CDB$ROOT)
    "session_container": """
        SELECT SYS_CONTEXT('USERENV', 'CON_ID') AS con_id,
               SYS_CONTEXT('USERENV', 'CON_NAME') AS con_name
        FROM dual
    """,
}


class ProfilerAgent:
    """Profiles each configured Oracle database with read-only queries.

    Not a BaseAgent subclass — it runs once at startup, not per-workflow.
    """

    def __init__(self, context: AgentContext):
        self.context = context
        self._oracle_pool = OracleConnectionPool()
        self._query_runner = QueryRunner(timeout_seconds=30)

    def profile_all(self) -> dict[str, DatabaseProfile]:
        """Profile every database in config. Returns {db_id: profile}."""
        results: dict[str, DatabaseProfile] = {}

        for db_cfg in self.context.settings.databases:
            try:
                profile = self.profile_database(db_cfg.name)
                results[db_cfg.name] = profile
                logger.info(
                    "Profiled %s: type=%s, size=%.1fGB, OMF=%s, CDB=%s, RAC=%s",
                    db_cfg.name,
                    profile.db_type,
                    profile.db_size_gb,
                    profile.omf_enabled,
                    profile.is_cdb,
                    profile.is_rac,
                )
            except Exception as e:
                logger.warning("Failed to profile %s: %s", db_cfg.name, e)

        return results

    def profile_database(self, database_id: str) -> DatabaseProfile:
        """Profile a single database and store the result."""
        db_cfg = self.context.settings.get_database(database_id)
        if not db_cfg:
            raise ProfileError(f"No config for database {database_id}")

        conn = self._oracle_pool.get_connection(
            database_id=database_id,
            connection_string=db_cfg.connection_string,
            password=db_cfg.password,
            username=db_cfg.username if db_cfg.username else None,
            read_only=True,
        )

        try:
            profile = self._run_discovery(database_id, conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        # Store in environment_registry
        env_record = self.context.environment_repo.get(database_id)
        version = (env_record.profile_version + 1) if env_record else 1
        profile.version = version

        self.context.environment_repo.update_profile(database_id, profile.to_json(), version)

        return profile

    def _run_discovery(self, database_id: str, conn) -> DatabaseProfile:
        """Run ALL discovery queries and build a comprehensive profile."""
        profile = DatabaseProfile(database_id=database_id)
        db_config: dict = {}

        # Run every query — failures are non-fatal
        for query_name, sql in QUERIES.items():
            try:
                rows = self._query_runner.execute_read(conn, sql)
                db_config[query_name] = rows
            except Exception as e:
                logger.warning("%s query failed for %s: %s", query_name, database_id, e)
                db_config[query_name] = []

        profile.db_config = db_config

        # Derive typed convenience fields for rule-based routing
        self._derive_fields(profile)

        return profile

    # ------------------------------------------------------------------
    # Derive typed fields from raw db_config
    # These booleans/dicts power rule-based routing (confidence thresholds,
    # environment checks).  The LLM uses the full db_config dict directly.
    # ------------------------------------------------------------------

    def _derive_fields(self, profile: DatabaseProfile) -> None:
        """Populate typed convenience fields from raw query results."""
        cfg = profile.db_config

        # --- DB identity ---
        db_id_rows = cfg.get("db_identity", [])
        if db_id_rows:
            info = db_id_rows[0]
            profile.is_cdb = str(info.get("cdb", "NO")).upper() == "YES"

        # --- Session container ID ---
        # v$database.con_id is always 0 (database-level, NOT session-level).
        # Use SYS_CONTEXT('USERENV', 'CON_ID') to detect the actual PDB.
        # PDB connections return con_id >= 3, CDB$ROOT returns 1.
        session_rows = cfg.get("session_container", [])
        if session_rows:
            profile.con_id = int(session_rows[0].get("con_id", 0))
        elif db_id_rows:
            # Fallback to v$database.con_id (always 0, but better than nothing)
            profile.con_id = int(db_id_rows[0].get("con_id", 0))

        # --- DB size ---
        size_rows = cfg.get("db_size", [])
        if size_rows and size_rows[0].get("total_gb") is not None:
            profile.db_size_gb = float(size_rows[0]["total_gb"])

        # --- Parameters: OMF, RAC, critical, non-standard ---
        param_rows = cfg.get("all_parameters", [])
        param_map = {r["name"]: r["value"] for r in param_rows}

        omf_dest = param_map.get("db_create_file_dest", "")
        profile.omf_enabled = bool(omf_dest and omf_dest.strip())

        cluster_db = param_map.get("cluster_database", "FALSE")
        profile.is_rac = str(cluster_db).upper() == "TRUE"

        critical_names = {
            "sga_target",
            "sga_max_size",
            "pga_aggregate_target",
            "memory_target",
            "memory_max_target",
            "processes",
            "sessions",
            "open_cursors",
            "db_block_size",
            "db_files",
            "undo_tablespace",
            "undo_retention",
            "log_archive_dest_1",
            "parallel_max_servers",
            "cursor_sharing",
            "optimizer_adaptive_statistics",
            "recyclebin",
            "deferred_segment_creation",
            "result_cache_max_size",
            "control_file_record_keep_time",
        }
        profile.critical_parameters = {
            r["name"]: r["value"] for r in param_rows if r["name"] in critical_names
        }

        profile.non_standard_params = {
            r["name"]: r["value"]
            for r in param_rows
            if r.get("isdefault") == "FALSE" and not r["name"].startswith("_")
        }

        # --- Workload metrics ---
        workload_rows = cfg.get("workload", [])
        profile.workload_metrics = {
            r["metric_name"]: round(float(r["value"]), 2)
            for r in workload_rows
            if r.get("value") is not None
        }

        # --- Risk areas (tablespaces > 80%) ---
        usage_rows = cfg.get("tablespace_usage", [])
        profile.risk_areas = [
            {"tablespace": r["tablespace_name"], "used_pct": float(r["used_pct"])}
            for r in usage_rows
            if float(r.get("used_pct", 0)) > 80
        ]

        # --- DB type from workload ---
        profile.db_type = self._infer_db_type(profile)

    @staticmethod
    def _infer_db_type(profile: DatabaseProfile) -> str:
        """Guess OLTP/OLAP/MIXED from workload metrics."""
        reads = profile.workload_metrics.get("Physical Reads Per Sec", 0)
        writes = profile.workload_metrics.get("Physical Writes Per Sec", 0)
        txns = profile.workload_metrics.get("User Transaction Per Sec", 0)

        if txns > 50:
            return "OLTP"
        if reads > 0 and writes > 0 and reads / max(writes, 1) > 10:
            return "OLAP"
        if txns > 5:
            return "OLTP"
        return "MIXED"
