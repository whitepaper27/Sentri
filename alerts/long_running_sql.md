---
type: alert_pattern
name: long_running_sql
severity: MEDIUM
action_type: KILL_SESSION
version: "1.0"
---

# Long Running SQL

Detects when a SQL statement has been running longer than a configured threshold (typically 2+ hours). Kills the session based on profile rules — only kills sessions from non-critical profiles/users.

## Email Pattern

```regex
(?i)(?:long\s+)?running\s+(?:sql|query|statement)\s+.*?(?:SID|session)\s*[=:#]?\s*(\d+).*?(?:running\s+(?:for\s+)?)?(\d+)\s*(?:hours?|hrs?).*?(?:on|database)\s+(\S+)
```

## Extracted Fields

- `session_sid` = group(1) -- SID of the long-running session
- `running_hours` = group(2) -- How many hours the SQL has been running
- `database_id` = group(3) -- Target database identifier

## Verification Query

```sql
SELECT s.sid,
       s.serial#,
       s.username,
       s.program,
       s.machine,
       s.sql_id,
       s.sql_exec_start,
       ROUND((SYSDATE - s.sql_exec_start) * 24, 1) AS elapsed_hours,
       s.status,
       s.event
  FROM v$session s
 WHERE s.sid = :session_sid
   AND s.sql_exec_start IS NOT NULL;
```

Get the SQL text for diagnostics:

```sql
SELECT sql_id,
       sql_text,
       elapsed_time / 1000000 AS elapsed_seconds,
       cpu_time / 1000000 AS cpu_seconds,
       buffer_gets,
       disk_reads,
       rows_processed,
       executions
  FROM v$sql
 WHERE sql_id = (SELECT sql_id FROM v$session WHERE sid = :session_sid)
 FETCH FIRST 1 ROW ONLY;
```

## Tolerance

- `elapsed_hours`: Session must still be running AND elapsed time must be >= 2 hours at verification time. If the session has completed, treat as self-resolved.
- The SQL must still be actively executing (status = 'ACTIVE' or waiting on an event).

## Pre-Flight Checks

- Session exists and is still running -- ACTIVE

```sql
SELECT sid, status FROM v$session WHERE sid = :session_sid AND status IN ('ACTIVE', 'INACTIVE');
```

- Session is not from a protected profile -- not CRITICAL

```sql
SELECT s.username, p.profile FROM v$session s JOIN dba_users u ON s.username = u.username JOIN dba_profiles p ON u.profile = p.profile WHERE s.sid = :session_sid AND p.profile NOT IN ('DBA_PROFILE', 'SYSTEM_PROFILE', 'CRITICAL_BATCH');
```

## Forward Action

```sql
ALTER SYSTEM KILL SESSION ':session_sid,:serial_number' IMMEDIATE;
```

Kills the long-running session. The `:serial_number` is obtained from the verification query.

**Safety rules**:
- Never kill SYS, SYSTEM, or DBSNMP sessions.
- Never kill sessions from protected profiles (DBA_PROFILE, SYSTEM_PROFILE, CRITICAL_BATCH).
- Only kill if elapsed time exceeds 2 hours.
- Consider the SQL type: SELECT-only queries are safe to kill; DML with large uncommitted changes will trigger a long rollback.
- If the SQL is a known batch job, escalate to the application team instead of killing.

## Rollback Action

```sql
-- N/A: Session kill is irreversible.
-- If the killed session had uncommitted DML, Oracle rolls it back automatically.
-- SELECT queries have no rollback impact.
```

No automated rollback. Oracle handles transaction cleanup.

## Validation Query

```sql
SELECT COUNT(*) AS session_exists
  FROM v$session
 WHERE sid = :session_sid
   AND sql_exec_start IS NOT NULL;
```

**Success criteria**: `session_exists` = 0. The session should no longer be present (or should have a new SQL_EXEC_START if the application reconnected and started a new query).

## Risk Level

MEDIUM -- Killing a long-running query terminates work in progress. For SELECT queries, this is low risk. For DML queries, the rollback may take significant time. Profile-based safety rules prevent killing critical batch jobs.

## Expected Downtime

NONE -- Only the killed session is affected. Database remains fully operational.

## Estimated Duration

~5 seconds for the kill. If the session had a large uncommitted DML transaction, the rollback by PMON may take minutes to hours depending on the transaction size.
