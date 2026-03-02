---
type: alert_pattern
name: high_undo_usage
severity: HIGH
action_type: KILL_SESSION
version: "1.0"
---

# High Undo Usage

Detects when undo tablespace usage is critically high, which can cause ORA-30036 (unable to extend undo segment) errors. Identifies the sessions consuming the most undo and kills the worst offender if safe.

## Email Pattern

```regex
(?i)(?:high\s+)?undo\s+(?:tablespace\s+)?(?:usage|utilization|full|alert).*?(\d+(?:\.\d+)?)\s*%.*?(?:on|database)\s+(\S+)
```

## Extracted Fields

- `undo_percent` = group(1) -- Current undo usage percentage
- `database_id` = group(2) -- Target database identifier

## Verification Query

```sql
SELECT tablespace_name,
       ROUND(used_percent, 2) AS used_percent
  FROM dba_tablespace_usage_metrics
 WHERE tablespace_name = (SELECT value FROM v$parameter WHERE name = 'undo_tablespace');
```

Identify top undo consumers:

```sql
SELECT s.sid,
       s.serial#,
       s.username,
       s.program,
       s.machine,
       s.sql_id,
       t.used_ublk * (SELECT value FROM v$parameter WHERE name = 'db_block_size') / 1024 / 1024 AS undo_mb,
       t.start_time
  FROM v$transaction t
  JOIN v$session s ON t.ses_addr = s.saddr
 WHERE s.username IS NOT NULL
 ORDER BY t.used_ublk DESC
 FETCH FIRST 10 ROWS ONLY;
```

## Tolerance

- `used_percent`: Must still be above 80% at verification time. If below 80%, treat as self-resolved.
- At least one session must be consuming significant undo (> 100 MB).

## Pre-Flight Checks

- Undo tablespace exists -- not empty

```sql
SELECT value FROM v$parameter WHERE name = 'undo_tablespace';
```

- Undo tablespace is ONLINE -- ONLINE

```sql
SELECT status FROM dba_tablespaces WHERE tablespace_name = (SELECT value FROM v$parameter WHERE name = 'undo_tablespace');
```

## Forward Action

```sql
ALTER SYSTEM KILL SESSION ':top_undo_sid,:top_undo_serial' IMMEDIATE;
```

Kills the session consuming the most undo space. The `:top_undo_sid` and `:top_undo_serial` are obtained from the verification query.

**Safety rules**:
- Never kill SYS, SYSTEM, or DBSNMP sessions.
- Only kill if the session's undo usage exceeds 100 MB.
- If the top consumer is a legitimate batch process, consider escalating instead.
- After killing, Oracle automatically rolls back the transaction, which will free undo space.

## Rollback Action

```sql
-- N/A: Session kill is irreversible.
-- Oracle automatically rolls back the killed session's uncommitted transaction.
-- This rollback itself will temporarily increase undo usage before freeing it.
```

No automated rollback. The killed session's transaction rollback is handled by Oracle.

## Validation Query

```sql
SELECT tablespace_name,
       ROUND(used_percent, 2) AS used_percent
  FROM dba_tablespace_usage_metrics
 WHERE tablespace_name = (SELECT value FROM v$parameter WHERE name = 'undo_tablespace');
```

**Success criteria**: `used_percent` should decrease after the killed session's transaction is rolled back. Note: undo usage may temporarily spike during rollback before dropping. Allow 2-5 minutes for large transactions to roll back.

## Risk Level

MEDIUM -- Killing a session with a large uncommitted transaction causes Oracle to roll it back, which takes time and temporarily increases undo pressure. Pre-flight checks protect system sessions.

## Expected Downtime

NONE -- Only the killed session is affected. Database remains operational, though rollback of a large transaction may cause temporary performance impact.

## Estimated Duration

~5 seconds for the kill. Rollback duration depends on transaction size — large transactions may take minutes to roll back.
