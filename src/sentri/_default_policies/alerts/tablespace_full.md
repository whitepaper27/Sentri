---
type: alert_pattern
name: tablespace_full
severity: HIGH
action_type: ADD_DATAFILE
version: "1.0"
---

# Tablespace Full

Detects when a permanent tablespace reaches a critical usage threshold and automatically extends it by adding a new datafile.

## Email Pattern

```regex
(?i)(?<!temp\s)(?<!temporary\s)tablespace\s+(\S+)\s+.*?(\d+(?:\.\d+)?)\s*%\s*(?:full|capacity|used).*?(?:on|database)\s+(\S+)
```

## Extracted Fields

- `tablespace_name` = group(1) -- Name of the tablespace (e.g., USERS, SYSAUX)
- `used_percent` = group(2) -- Current usage percentage reported in the alert
- `database_id` = group(3) -- Target database identifier (e.g., PROD-DB-07)

## Verification Query

```sql
SELECT tablespace_name,
       used_percent
  FROM dba_tablespace_usage_metrics
 WHERE tablespace_name = :tablespace_name;
```

Confirms the tablespace usage reported in the email matches the live database metric. Only proceeds if the actual usage is within the configured tolerance of the reported value.

## Tolerance

- `used_percent`: +/- 2% of the value reported in the email.
- If the actual value is **below** the alert threshold minus tolerance, the alert is treated as a false positive (the situation may have resolved itself).

## Pre-Flight Checks

- Tablespace is ONLINE -- ONLINE

```sql
SELECT status FROM dba_tablespaces WHERE tablespace_name = :tablespace_name;
```

- Datafile count is below limit -- > 0

```sql
SELECT value - (SELECT COUNT(*) FROM dba_data_files) AS remaining_slots FROM v$parameter WHERE name = 'db_files';
```

## Forward Action

```sql
ALTER TABLESPACE :tablespace_name
  ADD DATAFILE SIZE 10G
  AUTOEXTEND ON NEXT 1G MAXSIZE 32G;
```

Adds a new 10 GB datafile with autoextend enabled. The datafile will grow in 1 GB increments up to a maximum of 32 GB.

**Post-execution**: Capture the path of the newly created datafile for use in rollback:

```sql
SELECT file_name
  FROM dba_data_files
 WHERE tablespace_name = :tablespace_name
 ORDER BY file_id DESC
 FETCH FIRST 1 ROW ONLY;
```

## Rollback Action

```sql
ALTER TABLESPACE :tablespace_name
  DROP DATAFILE ':new_datafile_path';
```

Drops the datafile that was added by the forward action. The `:new_datafile_path` is captured during forward action execution.

**Prerequisites for rollback**:
- The datafile must be empty (no extents allocated).
- If extents have been allocated, a manual resize or reorganization is required instead.

## Validation Query

```sql
SELECT tablespace_name,
       used_percent
  FROM dba_tablespace_usage_metrics
 WHERE tablespace_name = :tablespace_name;
```

**Success criteria**: `used_percent` must be lower than the value recorded during verification. Typically expect a drop of 10-20 percentage points after adding a 10 GB datafile.

## Risk Level

LOW -- Adding a datafile is an online, non-disruptive operation. It does not require downtime or affect running transactions.

## Expected Downtime

NONE -- This is a fully online operation. Active sessions and transactions are not interrupted.

## Estimated Duration

~15 seconds -- The datafile creation is typically fast on modern storage. Autoextend configuration is instantaneous.
