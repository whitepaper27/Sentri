---
type: agent
name: agent0_profiler
version: "2.0"
---

# Agent 0: The Profiler

Profiles each configured Oracle database on startup with 7 read-only discovery queries. The resulting profile is stored in the environment_registry and used by Agents 3 and 4 for smarter decision-making.

## Schedule

- Runs once on startup (background thread)
- Re-profiles on `sentri profile --refresh`
- Never runs during an active workflow

## Permissions

- **Read-only** Oracle access (SELECT on v$ and dba_ views)
- Writes only to Sentri's own SQLite (environment_registry.database_profile)

## Discovery Queries

1. **DB Identity** — `v$database` (role, open mode, CDB status)
2. **DB Size** — `v$datafile` (total GB)
3. **Configuration** — `v$parameter` for OMF, RAC, CDB settings
4. **Critical Parameters** — Top 20 from `v$parameter` (SGA, PGA, processes)
5. **Workload** — `v$sysmetric` (I/O, transactions, logons)
6. **Risk Areas** — `dba_tablespace_usage_metrics` (tablespaces >80%)
7. **Non-Standard** — `v$parameter WHERE isdefault = 'FALSE'`

## Profile Usage

- **OMF Detection** — If `db_create_file_dest` is set, Executor omits explicit datafile paths (Oracle manages them automatically)
- **CDB Awareness** — Adjusts SQL for container database context
- **RAC Awareness** — Logs RAC status for operator visibility
- **Risk Highlighting** — Flags tablespaces already near capacity before any alert arrives

## Error Handling

- If a query fails, that section is left empty — profiling is best-effort
- If the entire database is unreachable, log a warning and skip it
- Never block startup — profiling runs in a background thread
