---
type: alert_pattern
name: archive_gap
severity: HIGH
action_type: RESOLVE_GAP
version: "1.0"
---

# Archive Gap

Detects when a gap exists in the archive log sequence on a standby database relative to its primary. An archive gap means the standby is missing one or more archivelogs required for recovery, which puts the Data Guard configuration at risk and can lead to data loss if the primary fails.

## Email Pattern

```regex
(?i)archive\s+(?:log\s+)?gap\s+(?:detected|found|exists)?\s*.*?(?:thread\s+(\d+)\s+)?.*?(?:sequence\s+(\d+)\s*(?:to|-|through)\s*(\d+))?\s*.*?(?:on|database|standby)\s+(\S+)
```

## Extracted Fields

- `thread_number` = group(1) -- RAC thread number (optional, defaults to 1 for single-instance)
- `low_sequence` = group(2) -- First missing log sequence number (optional)
- `high_sequence` = group(3) -- Last missing log sequence number (optional)
- `database_id` = group(4) -- Target standby database identifier

## Verification Query

Check for archive gaps on the standby database:

```sql
SELECT thread#,
       low_sequence#,
       high_sequence#
  FROM v$archive_gap;
```

If `v$archive_gap` returns rows, a gap is confirmed.

Additional context -- check the last applied archivelog:

```sql
SELECT thread#,
       MAX(sequence#) AS last_applied_sequence,
       MAX(next_time) AS last_applied_time
  FROM v$archived_log
 WHERE applied = 'YES'
   AND dest_id = 1
 GROUP BY thread#
 ORDER BY thread#;
```

Check the current log sequence on the primary (if accessible):

```sql
SELECT thread#,
       sequence# AS current_sequence
  FROM v$log
 WHERE status = 'CURRENT';
```

Check Data Guard status:

```sql
SELECT database_role,
       protection_mode,
       protection_level,
       switchover_status
  FROM v$database;
```

Check Managed Recovery Process (MRP) status:

```sql
SELECT process,
       status,
       thread#,
       sequence#,
       block#
  FROM v$managed_standby
 WHERE process LIKE 'MRP%';
```

## Tolerance

- This is a binary/threshold check: either a gap exists or it does not.
- If `v$archive_gap` returns no rows at verification time, the alert is treated as self-resolved (the gap may have been resolved by automatic log shipping).
- A gap of 1 sequence is LOW urgency; a gap of 5+ sequences is CRITICAL urgency (escalate severity).

## Pre-Flight Checks

- Database is in standby role -- not empty

```sql
SELECT database_role FROM v$database WHERE database_role LIKE '%STANDBY%';
```

- Managed recovery process is known -- not empty

```sql
SELECT process FROM v$managed_standby WHERE process LIKE 'MRP%' FETCH FIRST 1 ROW ONLY;
```

## Forward Action

The remediation depends on the database role and gap characteristics.

**Scenario A: Standby database -- fetch missing logs from primary**

```sql
-- On the STANDBY: Restart log apply to trigger fetch
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL;
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE USING CURRENT LOGFILE DISCONNECT;
```

If automatic fetch does not resolve the gap, manually copy and register:

```sql
-- OS COMMAND: Copy missing logs from primary archive destination
-- scp primary_host:/u01/archivelog/thread_:thread_number/seq_:low_sequence.arc /u01/archivelog/

-- Register the manually copied archivelog on the standby
ALTER DATABASE REGISTER LOGFILE '/u01/archivelog/thread_:thread_number_seq_:sequence.arc';
```

**Scenario B: Primary database -- force log switch and ship**

```sql
-- On the PRIMARY: Force a log switch to ship current logs
ALTER SYSTEM SWITCH LOGFILE;
ALTER SYSTEM ARCHIVE LOG CURRENT;
```

**Scenario C: Gap too large or logs unavailable -- incremental backup**

```sql
-- On the PRIMARY via RMAN: Create incremental backup for standby
-- RMAN COMMAND (not SQL)
RMAN> BACKUP INCREMENTAL FROM SCN :standby_scn DATABASE FORMAT '/tmp/for_standby_%U';
```

```sql
-- On the STANDBY via RMAN: Apply incremental backup
-- RMAN COMMAND (not SQL)
RMAN> CATALOG START WITH '/tmp/for_standby_';
RMAN> RECOVER DATABASE NOREDO;
```

## Rollback Action

```sql
-- Manual intervention required.
-- Archive gap resolution involves log shipping or incremental recovery.
-- These operations are additive (applying logs) and do not have a
-- simple automated rollback.
--
-- If MRP was cancelled and restarted:
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE CANCEL;
-- Then investigate the gap manually before restarting recovery.
```

Automated rollback is not feasible for archive gap remediation because:
1. Log apply is an additive, forward-only operation.
2. Cancelling MRP stops recovery but does not undo applied logs.
3. The DBA must assess whether to flashback the standby or continue forward.

## Validation Query

Verify the gap has been resolved:

```sql
SELECT thread#,
       low_sequence#,
       high_sequence#
  FROM v$archive_gap;
```

**Success criteria**: Query returns no rows (gap fully resolved).

Additionally, verify MRP is running and applying logs:

```sql
SELECT process,
       status,
       thread#,
       sequence#
  FROM v$managed_standby
 WHERE process LIKE 'MRP%';
```

**Success criteria**: MRP process shows `APPLYING_LOG` status.

Check the apply lag:

```sql
SELECT name,
       value,
       datum_time
  FROM v$dataguard_stats
 WHERE name = 'apply lag';
```

**Success criteria**: Apply lag is less than 30 minutes (or within the acceptable threshold defined in the environment policy).

## Risk Level

HIGH -- Archive gap resolution involves:
- Restarting managed recovery, which briefly pauses log apply.
- In severe cases, incremental backup recovery which requires primary database resources.
- If logs are permanently lost (no backup), the standby may need to be rebuilt entirely.
- Incorrect log registration can corrupt the standby.

## Expected Downtime

NONE -- The standby database is not serving application traffic (it is in recovery mode). Restarting MRP does not affect the primary database. However, during the gap, the standby provides reduced disaster recovery protection.

## Estimated Duration

Varies significantly depending on gap size:
- **1-5 missing logs**: ~30 seconds (automatic fetch and apply)
- **5-50 missing logs**: ~5 minutes (manual copy and register)
- **50+ missing logs or logs unavailable**: ~30-60 minutes (incremental backup recovery)
- **Standby rebuild required**: Hours (out of scope for automated remediation)
