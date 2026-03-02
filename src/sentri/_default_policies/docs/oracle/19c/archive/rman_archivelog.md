---
version: "19c"
topic: archive
operation: rman_archivelog
keywords: [archive, archivelog, rman, fra, flash recovery area, delete, crosscheck]
applies_to: [archive_dest_full, archive_gap]
web_source: "https://docs.oracle.com/en/database/oracle/oracle-database/19/admin/managing-archived-redo-log-files.html"
---

# RMAN Archive Log Management — Oracle 19c

## Delete Obsolete Archive Logs

Remove archive logs that are no longer needed per the retention policy:

```sql
RMAN> DELETE NOPROMPT ARCHIVELOG ALL COMPLETED BEFORE 'SYSDATE-7';
```

Or delete only those already backed up:

```sql
RMAN> DELETE NOPROMPT ARCHIVELOG ALL BACKED UP 1 TIMES TO DEVICE TYPE DISK;
```

## Delete All Archive Logs (Emergency)

**Use with caution — only when FRA is critically full:**

```sql
RMAN> DELETE NOPROMPT ARCHIVELOG ALL;
```

## Crosscheck and Delete Expired

```sql
RMAN> CROSSCHECK ARCHIVELOG ALL;
RMAN> DELETE NOPROMPT EXPIRED ARCHIVELOG ALL;
```

## Flash Recovery Area (FRA) Management

### Check FRA Usage

```sql
SELECT name, space_limit/1024/1024/1024 AS limit_gb,
       space_used/1024/1024/1024 AS used_gb,
       space_reclaimable/1024/1024/1024 AS reclaimable_gb,
       ROUND(space_used/space_limit*100, 1) AS pct_used
  FROM v$recovery_file_dest;
```

### Increase FRA Size

```sql
ALTER SYSTEM SET db_recovery_file_dest_size = <new_size>;
```

Example:

```sql
ALTER SYSTEM SET db_recovery_file_dest_size = 100G SCOPE=BOTH;
```

### Change FRA Location

```sql
ALTER SYSTEM SET db_recovery_file_dest = '<new_path>' SCOPE=BOTH;
```

## Archive Destination Full

### Check Archive Destinations

```sql
SELECT dest_id, dest_name, status, target, archiver,
       schedule, MOUNTID
  FROM v$archive_dest
 WHERE status != 'INACTIVE';
```

### Switch Log File (Force Archiving)

```sql
ALTER SYSTEM SWITCH LOGFILE;
```

## Data Guard Considerations

- On a standby database, archive logs are received and applied
- Do NOT delete archive logs on standby that haven't been applied
- Check: `SELECT * FROM v$archived_log WHERE applied = 'NO'`
