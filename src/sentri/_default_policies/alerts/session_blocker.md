---
type: alert_pattern
name: session_blocker
severity: HIGH
action_type: KILL_SESSION
version: "1.0"
---

# Session Blocker

Detects when a database session is blocking other sessions, causing lock contention and application hangs. Remediates by killing the blocking session after verification.

## Email Pattern

```regex
(?i)(?:session\s+)?block(?:er|ing)\s+(?:session\s+)?(?:detected|alert|chain).*?(?:SID|session)\s*[=:#]?\s*(\d+).*?(?:on|database)\s+(\S+)
```

## Extracted Fields

- `blocking_sid` = group(1) -- SID of the blocking session
- `database_id` = group(2) -- Target database identifier

## Verification Query

```sql
SELECT s.sid AS blocking_sid,
       s.serial#,
       s.username,
       s.program,
       s.machine,
       s.status,
       s.sql_id,
       s.logon_time,
       ROUND((SYSDATE - s.logon_time) * 24 * 60, 1) AS minutes_connected,
       (SELECT COUNT(*) FROM v$session w WHERE w.blocking_session = s.sid) AS blocked_count
  FROM v$session s
 WHERE s.sid = :blocking_sid
   AND EXISTS (SELECT 1 FROM v$session w WHERE w.blocking_session = s.sid);
```

Confirms the session is actually blocking other sessions. Also checks blocked session details:

```sql
SELECT s.sid AS blocked_sid,
       s.username,
       s.program,
       s.event,
       s.seconds_in_wait,
       s.blocking_session AS blocker_sid
  FROM v$session s
 WHERE s.blocking_session = :blocking_sid
 ORDER BY s.seconds_in_wait DESC;
```

## Tolerance

- `blocked_count`: Must be >= 1. If the blocking session is no longer blocking anyone, treat as self-resolved.
- Verification must confirm the blocker SID exists AND is actively blocking at least one other session.

## Pre-Flight Checks

- Blocking session exists -- not empty

```sql
SELECT sid, serial#, username FROM v$session WHERE sid = :blocking_sid;
```

- Session is not a critical system process -- not SYS

```sql
SELECT username FROM v$session WHERE sid = :blocking_sid AND username NOT IN ('SYS', 'SYSTEM', 'DBSNMP');
```

## Forward Action

```sql
ALTER SYSTEM KILL SESSION ':blocking_sid,:serial_number' IMMEDIATE;
```

Kills the blocking session immediately. The `:serial_number` is obtained from the verification query.

**Safety rules**:
- Never kill SYS, SYSTEM, or DBSNMP sessions.
- Never kill sessions from critical schemas (defined in environment config).
- If blocker is an application session, kill is safe — the application should handle reconnection.

## Rollback Action

```sql
-- N/A: Session kill is irreversible.
-- The killed session's uncommitted transaction is automatically rolled back by Oracle.
-- The application should reconnect and retry the operation.
```

No automated rollback. The killed session's uncommitted work is rolled back by Oracle automatically.

## Validation Query

```sql
SELECT COUNT(*) AS remaining_blocked
  FROM v$session
 WHERE blocking_session = :blocking_sid;
```

**Success criteria**: `remaining_blocked` = 0. All previously blocked sessions should now be free.

## Risk Level

MEDIUM -- Killing a session terminates any in-progress transaction and rolls it back. The application must handle reconnection. Critical system sessions are protected by pre-flight checks.

## Expected Downtime

NONE -- Only the killed session is affected. Other sessions and the database remain operational.

## Estimated Duration

~5 seconds -- ALTER SYSTEM KILL SESSION is nearly instantaneous. Oracle's PMON process handles cleanup.
