---
type: alert_pattern
name: cpu_high
severity: HIGH
action_type: KILL_SESSION
version: "1.0"
---

# CPU High

Detects when database CPU utilization is critically high. Identifies the top CPU-consuming sessions and kills the worst offender if it matches kill-safe rules (non-system, non-critical profile).

## Email Pattern

```regex
(?i)(?:high\s+)?cpu\s+(?:utilization|usage|alert).*?(\d+(?:\.\d+)?)\s*%.*?(?:on|database)\s+(\S+)
```

## Extracted Fields

- `cpu_percent` = group(1) -- Current CPU usage percentage
- `database_id` = group(2) -- Target database identifier

## Verification Query

```sql
SELECT stat_name,
       value
  FROM v$osstat
 WHERE stat_name IN ('NUM_CPUS', 'BUSY_TIME', 'IDLE_TIME');
```

Identify top CPU consumers:

```sql
SELECT s.sid,
       s.serial#,
       s.username,
       s.program,
       s.machine,
       s.sql_id,
       s.status,
       ss.value AS cpu_used_centiseconds,
       ROUND(ss.value / 100, 1) AS cpu_seconds
  FROM v$session s
  JOIN v$sesstat ss ON s.sid = ss.sid
  JOIN v$statname sn ON ss.statistic# = sn.statistic#
 WHERE sn.name = 'CPU used by this session'
   AND s.username IS NOT NULL
   AND s.type = 'USER'
 ORDER BY ss.value DESC
 FETCH FIRST 10 ROWS ONLY;
```

## Tolerance

- `cpu_percent`: Must still be above 85% at verification time. If below 85%, treat as self-resolved.
- At least one user session must be consuming significant CPU (> 10% of total).

## Pre-Flight Checks

- Database is accessible -- OPEN

```sql
SELECT status FROM v$instance;
```

- Top CPU session is not a system user -- not SYS

```sql
SELECT username FROM v$session s JOIN v$sesstat ss ON s.sid = ss.sid JOIN v$statname sn ON ss.statistic# = sn.statistic# WHERE sn.name = 'CPU used by this session' AND s.username NOT IN ('SYS', 'SYSTEM', 'DBSNMP') AND s.type = 'USER' ORDER BY ss.value DESC FETCH FIRST 1 ROW ONLY;
```

## Forward Action

```sql
ALTER SYSTEM KILL SESSION ':top_sid,:top_serial' IMMEDIATE;
```

Kills the top CPU-consuming session. The `:top_sid` and `:top_serial` are obtained from the verification query.

**Safety rules**:
- Only kill sessions from non-system, non-critical users.
- Only kill if the session has been consuming CPU for > 5 minutes.
- If top consumer is from a critical profile (e.g., batch jobs), escalate instead of killing.

## Rollback Action

```sql
-- N/A: Session kill is irreversible.
-- The killed session's transaction is automatically rolled back by Oracle.
```

No automated rollback. Application should handle reconnection.

## Validation Query

```sql
SELECT stat_name,
       value
  FROM v$osstat
 WHERE stat_name IN ('NUM_CPUS', 'BUSY_TIME', 'IDLE_TIME');
```

**Success criteria**: CPU utilization should drop below 85% within 1-2 minutes after killing the offending session. If CPU remains high, additional sessions may need attention (escalate).

## Risk Level

MEDIUM -- Killing a high-CPU session may terminate a legitimate long-running operation. Pre-flight checks protect system sessions. Application sessions should handle reconnection gracefully.

## Expected Downtime

NONE -- Only the killed session is affected. Database remains fully operational.

## Estimated Duration

~5 seconds -- Session kill is instantaneous. CPU relief depends on how quickly PMON cleans up the session's resources.
