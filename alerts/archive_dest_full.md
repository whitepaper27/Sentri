---
type: alert_pattern
name: archive_dest_full
severity: CRITICAL
action_type: DELETE_ARCHIVES
version: "1.0"
---

# Archive Destination Full

Detects when an archive log destination reaches capacity, which can cause the database to stall if it cannot write archive logs. Remediates by crosschecking and deleting expired archivelogs via RMAN.

## Email Pattern

```regex
(?i)archive\s+(?:log\s+)?dest(?:ination)?\s+(\S+)\s+.*?(\d+(?:\.\d+)?)\s*%\s*(?:full|used|capacity).*?(?:on|database)\s+(\S+)
```

## Extracted Fields

- `dest_name` = group(1) -- Archive destination identifier (e.g., LOG_ARCHIVE_DEST_1, /u01/archivelog)
- `percent_full` = group(2) -- Current usage percentage of the destination
- `database_id` = group(3) -- Target database identifier

## Verification Query

For Flash Recovery Area (FRA) destinations:

```sql
SELECT name AS dest_name,
       space_limit,
       space_used,
       ROUND(space_used / space_limit * 100, 2) AS percent_full,
       space_reclaimable,
       number_of_files
  FROM v$recovery_file_dest;
```

For standard archive destinations:

```sql
SELECT dest_id,
       dest_name,
       status,
       destination,
       error
  FROM v$archive_dest
 WHERE status = 'VALID'
   AND dest_name = :dest_name;
```

Supplementary check to enumerate archivelog files and their backup status:

```sql
SELECT sequence#,
       first_time,
       next_time,
       applied,
       backed_up_count
  FROM v$archived_log
 WHERE deleted = 'NO'
   AND dest_id = :dest_id
 ORDER BY sequence# DESC
 FETCH FIRST 50 ROWS ONLY;
```

Confirms the archive destination usage matches the alert. Only proceeds if actual usage is within tolerance.

## Tolerance

- `percent_full`: +/- 3% of the value reported in the email.
- If below 80% at verification time, treat as self-resolved (e.g., a backup job already cleaned space).

## Pre-Flight Checks

- Database is in ARCHIVELOG mode -- ARCHIVELOG

```sql
SELECT log_mode FROM v$database;
```

- Recovery file dest is configured -- not empty

```sql
SELECT value FROM v$parameter WHERE name = 'db_recovery_file_dest';
```

## Forward Action

The remediation uses RMAN commands, not direct SQL:

```sql
-- RMAN commands (executed via subprocess)
CROSSCHECK ARCHIVELOG ALL;
DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-7';
```

**Execution method**: These commands are run through an RMAN subprocess, not a SQL connection. The agent invokes:

```
rman target / <<EOF
CROSSCHECK ARCHIVELOG ALL;
DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-7';
EOF
```

**Safety checks before execution**:
1. Verify all archivelogs older than 7 days have been backed up at least once.
2. If any un-backed-up logs exist in the deletion window, reduce the window or skip deletion.

## Rollback Action

```sql
-- N/A: Archivelog deletion is irreversible.
-- However, this is safe because:
--   1. Only EXPIRED logs (already removed from disk) are deleted from the catalog.
--   2. Only logs older than 7 days (and already backed up) are purged.
--   3. If logs were not backed up, the safety check prevents deletion.
```

No automated rollback is possible for deleted archivelogs. If archivelogs are needed after deletion, they must be restored from backup.

## Validation Query

```sql
SELECT name AS dest_name,
       space_limit,
       space_used,
       ROUND(space_used / space_limit * 100, 2) AS percent_full,
       space_reclaimable
  FROM v$recovery_file_dest;
```

**Success criteria**: `percent_full` must be lower than the value recorded during verification. A drop of at least 10 percentage points is expected after cleaning expired logs older than 7 days.

## Risk Level

MEDIUM -- Deleting archivelogs is irreversible, but the safety checks (backup verification, 7-day retention window) minimize data loss risk. If backup verification fails, the action is aborted.

## Expected Downtime

NONE -- RMAN crosscheck and delete operations do not impact running database sessions or active transactions. The database remains fully operational.

## Estimated Duration

~60 seconds -- Crosscheck time depends on the number of archivelogs. Deletion is fast once the crosscheck completes. Large environments with thousands of logs may take longer.
