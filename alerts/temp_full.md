---
type: alert_pattern
name: temp_full
severity: HIGH
action_type: ADD_TEMPFILE
version: "1.0"
---

# Temp Tablespace Full

Detects when a temporary tablespace is running out of space, which causes sort operations, hash joins, and other temp-heavy queries to fail with ORA-01652. Remediates by adding a new tempfile.

## Email Pattern

```regex
(?i)temp(?:orary)?\s+tablespace\s+(\S+)\s+.*?(\d+(?:\.\d+)?)\s*%\s*(?:full|used|capacity).*?(?:on|database)\s+(\S+)
```

## Extracted Fields

- `temp_tablespace` = group(1) -- Name of the temporary tablespace (e.g., TEMP, TEMP2)
- `used_percent` = group(2) -- Current usage percentage reported in the alert
- `database_id` = group(3) -- Target database identifier

## Verification Query

```sql
SELECT tf.tablespace_name,
       ROUND(tf.allocated_bytes / 1024 / 1024, 2) AS allocated_mb,
       ROUND(tf.free_bytes / 1024 / 1024, 2) AS free_mb,
       ROUND((tf.allocated_bytes - tf.free_bytes) / tf.allocated_bytes * 100, 2) AS pct_used
  FROM (
    SELECT tablespace_name,
           SUM(bytes_used + bytes_free) AS allocated_bytes,
           SUM(bytes_free) AS free_bytes
      FROM v$temp_space_header
     GROUP BY tablespace_name
  ) tf
 WHERE tf.tablespace_name = :temp_tablespace;
```

Alternative verification using `dba_temp_free_space`:

```sql
SELECT tablespace_name,
       tablespace_size / 1024 / 1024 AS total_mb,
       allocated_space / 1024 / 1024 AS allocated_mb,
       free_space / 1024 / 1024 AS free_mb,
       ROUND((allocated_space - free_space) / tablespace_size * 100, 2) AS pct_used
  FROM dba_temp_free_space
 WHERE tablespace_name = :temp_tablespace;
```

Confirms the temp tablespace usage reported in the email. Also checks for large sort consumers:

```sql
SELECT s.sid,
       s.serial#,
       s.username,
       su.tablespace,
       ROUND(su.blocks * (SELECT value FROM v$parameter WHERE name = 'db_block_size') / 1024 / 1024, 2) AS temp_mb
  FROM v$sort_usage su
  JOIN v$session s ON su.session_addr = s.saddr
 WHERE su.tablespace = :temp_tablespace
 ORDER BY su.blocks DESC
 FETCH FIRST 10 ROWS ONLY;
```

## Tolerance

- `pct_used`: +/- 5% of the value reported in the email.
- Temporary tablespace usage is volatile (sessions release temp space when queries complete), so a wider tolerance is used than for permanent tablespaces.
- If below 70% at verification time, treat as self-resolved.

## Pre-Flight Checks

- Temp tablespace exists -- not empty

```sql
SELECT tablespace_name FROM dba_tablespaces WHERE tablespace_name = :temp_tablespace AND contents = 'TEMPORARY';
```

- Temp tablespace is ONLINE -- ONLINE

```sql
SELECT status FROM dba_tablespaces WHERE tablespace_name = :temp_tablespace;
```

## Forward Action

```sql
ALTER TABLESPACE :temp_tablespace
  ADD TEMPFILE SIZE 5G
  AUTOEXTEND ON NEXT 512M MAXSIZE 16G;
```

Adds a new 5 GB tempfile with autoextend enabled. The tempfile will grow in 512 MB increments up to 16 GB.

**Post-execution**: Capture the path of the newly created tempfile for rollback:

```sql
SELECT file_name,
       file_id,
       tablespace_name,
       bytes / 1024 / 1024 AS size_mb
  FROM dba_temp_files
 WHERE tablespace_name = :temp_tablespace
 ORDER BY file_id DESC
 FETCH FIRST 1 ROW ONLY;
```

## Rollback Action

```sql
ALTER TABLESPACE :temp_tablespace
  DROP TEMPFILE ':new_tempfile_path';
```

Drops the tempfile that was added by the forward action. The `:new_tempfile_path` is captured during forward action execution.

**Prerequisites for rollback**:
- The tempfile must not be actively in use by sort operations.
- If sessions are actively using the tempfile, they must complete or be terminated first.

## Validation Query

```sql
SELECT tablespace_name,
       tablespace_size / 1024 / 1024 AS total_mb,
       allocated_space / 1024 / 1024 AS allocated_mb,
       free_space / 1024 / 1024 AS free_mb,
       ROUND((allocated_space - free_space) / tablespace_size * 100, 2) AS pct_used
  FROM dba_temp_free_space
 WHERE tablespace_name = :temp_tablespace;
```

**Success criteria**: `pct_used` must be lower than the value recorded during verification, or total available space has increased. Because temp space is volatile, the percentage may not drop immediately, but the `total_mb` should reflect the additional capacity.

## Risk Level

LOW -- Adding a tempfile is an online, non-disruptive operation. It does not require downtime or affect running transactions or sort operations.

## Expected Downtime

NONE -- Fully online operation. Active sessions using temp space are not interrupted.

## Estimated Duration

~10 seconds -- Tempfile creation is fast. Autoextend configuration is instantaneous.
