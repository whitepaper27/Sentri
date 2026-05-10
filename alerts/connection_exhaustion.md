---
type: alert_pattern
name: connection_exhaustion
severity: CRITICAL
action_type: TERMINATE_IDLE_CONNECTIONS
version: "1.0"
---

# Connection Exhaustion

Detects when the number of active database sessions approaches or exceeds the `processes` parameter
limit, threatening to prevent new connections (ORA-00020). Remediates by terminating long-idle
INACTIVE sessions from non-critical schemas to free up connection slots.

## Email Pattern

```regex
(?i)(?:connection\s+(?:limit|exhaustion|threshold)|(?:max\s+)?(?:processes|sessions)\s+(?:limit\s+)?(?:reached|approaching|exceeded|critical)).*?(?:(\d+)\s*/\s*(\d+))?.*?(?:on|database|db)\s+(\S+)
```

## Extracted Fields

- `current_sessions` = group(1) -- Current session count (may be absent; will be verified live)
- `max_sessions` = group(2) -- Maximum processes/sessions limit (may be absent; will be verified live)
- `database_id` = group(3) -- Target database identifier

## Verification Query

Check current session count vs processes limit:

```sql
SELECT p.value AS processes_limit,
       (SELECT COUNT(*) FROM v$session WHERE type = 'USER') AS current_user_sessions,
       (SELECT COUNT(*) FROM v$session) AS total_sessions,
       ROUND(
         (SELECT COUNT(*) FROM v$session WHERE type = 'USER') /
         NULLIF(p.value, 0) * 100, 1
       ) AS utilization_pct
  FROM v$parameter p
 WHERE p.name = 'processes';
```

Get breakdown of idle sessions that can be safely terminated:

```sql
SELECT s.username,
       s.machine,
       s.program,
       s.status,
       s.last_call_et AS idle_seconds,
       ROUND(s.last_call_et / 60, 1) AS idle_minutes,
       COUNT(*) AS session_count
  FROM v$session s
 WHERE s.type = 'USER'
   AND s.status = 'INACTIVE'
   AND s.last_call_et > 1800
   AND s.username NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'RMAN')
 GROUP BY s.username, s.machine, s.program, s.status, s.last_call_et
 ORDER BY s.last_call_et DESC
 FETCH FIRST 20 ROWS ONLY;
```

## Tolerance

- Alert is valid if utilization_pct >= 80% (processes limit approaching).
- If utilization_pct < 70% at verification time, treat as self-resolved (load subsided).
- At least 5 idle sessions (inactive > 30 minutes) must exist before proceeding with termination.

## Pre-Flight Checks

- Utilization is still critical -- >= 80

```sql
SELECT ROUND(
  (SELECT COUNT(*) FROM v$session WHERE type = 'USER') /
  NULLIF((SELECT TO_NUMBER(value) FROM v$parameter WHERE name = 'processes'), 0) * 100
) AS utilization_pct FROM dual;
```

- There are idle sessions eligible for termination -- >= 1

```sql
SELECT COUNT(*) AS eligible_idle
  FROM v$session
 WHERE type = 'USER'
   AND status = 'INACTIVE'
   AND last_call_et > 1800
   AND username NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'RMAN');
```

## Forward Action

Kill sessions that have been idle (INACTIVE) for more than 30 minutes, excluding critical
system users and sessions:

```sql
BEGIN
  FOR s IN (
    SELECT sid, serial#, username
      FROM v$session
     WHERE type = 'USER'
       AND status = 'INACTIVE'
       AND last_call_et > 1800
       AND username NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'RMAN', 'APEX_PUBLIC_USER')
     ORDER BY last_call_et DESC
     FETCH FIRST 50 ROWS ONLY
  ) LOOP
    BEGIN
      EXECUTE IMMEDIATE 'ALTER SYSTEM KILL SESSION ''' || s.sid || ',' || s.serial# || ''' IMMEDIATE';
    EXCEPTION
      WHEN OTHERS THEN NULL; -- Session may have already disconnected
    END;
  END LOOP;
END;
```

Kills up to 50 longest-idle inactive sessions. The `EXCEPTION WHEN OTHERS THEN NULL` is safe here
because a session that already disconnected should not prevent the loop from continuing.

**Safety rules**:
- Only INACTIVE sessions are killed (not ACTIVE — those are doing real work).
- Only sessions idle > 30 minutes (1800 seconds) are targeted.
- SYS, SYSTEM, DBSNMP, RMAN are always excluded.
- Maximum 50 sessions per remediation pass to avoid excessive disruption.
- If utilization is still critical after this pass, escalate — may need `processes` parameter increase.

## Rollback Action

```sql
-- N/A: Session termination is irreversible.
-- Killed sessions have their uncommitted transactions rolled back by Oracle automatically.
-- Applications that hold idle connections should be configured with connection pool timeouts
-- to prevent recurrence. Escalate if this alert fires repeatedly.
```

## Validation Query

```sql
SELECT ROUND(
  (SELECT COUNT(*) FROM v$session WHERE type = 'USER') /
  NULLIF((SELECT TO_NUMBER(value) FROM v$parameter WHERE name = 'processes'), 0) * 100
) AS utilization_pct_after,
(SELECT COUNT(*) FROM v$session WHERE type = 'USER') AS sessions_after
FROM dual;
```

**Success criteria**: `utilization_pct_after` should be < 80%. If still >= 80% after killing idle
sessions, escalate — the database may need the `processes` parameter increased (requires DBA approval
and an instance restart in non-CDB deployments).

## Risk Level

MEDIUM -- Terminating idle sessions disconnects non-active application connections. Applications
with proper connection pool retry logic will reconnect automatically. Applications without retry
may show transient errors. Pre-flight checks limit termination to genuinely idle sessions (> 30 min).

## Expected Downtime

NONE for active workloads. Idle connections being terminated will observe a disconnect and
must reconnect, which modern connection pools handle transparently.

## Estimated Duration

~10–30 seconds depending on the number of sessions killed. Each ALTER SYSTEM KILL SESSION
is typically < 1 second for INACTIVE sessions.
