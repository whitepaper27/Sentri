---
version: "19c"
topic: undo
operation: undo_management
keywords: [undo, undo tablespace, undo_retention, ora-01555, snapshot too old]
applies_to: [high_undo_usage]
web_source: "https://docs.oracle.com/en/database/oracle/oracle-database/19/admin/managing-undo.html"
---

# UNDO Tablespace Management — Oracle 19c

## Check Current UNDO Usage

```sql
SELECT tablespace_name,
       ROUND(used_space * 8192 / 1024/1024/1024, 2) AS used_gb,
       ROUND(tablespace_size * 8192 / 1024/1024/1024, 2) AS total_gb,
       ROUND(used_percent, 1) AS pct_used
  FROM dba_tablespace_usage_metrics
 WHERE tablespace_name = (
   SELECT value FROM v$parameter WHERE name = 'undo_tablespace'
 );
```

## Identify Sessions Using UNDO

```sql
SELECT s.sid, s.serial#, s.username, s.program,
       t.used_ublk * (SELECT value FROM v$parameter WHERE name = 'db_block_size') / 1024/1024 AS undo_mb,
       t.start_time
  FROM v$transaction t
  JOIN v$session s ON t.ses_addr = s.saddr
 ORDER BY t.used_ublk DESC;
```

## Remediation Options

### 1. Resize UNDO Tablespace (Add Space)

For SMALLFILE UNDO tablespace:

```sql
ALTER TABLESPACE <undo_tablespace> ADD DATAFILE SIZE 10G
  AUTOEXTEND ON NEXT 1G MAXSIZE 32G;
```

For BIGFILE UNDO tablespace:

```sql
ALTER TABLESPACE <undo_tablespace> RESIZE <new_size>;
```

### 2. Reduce UNDO Retention

Lower the undo_retention parameter (seconds):

```sql
ALTER SYSTEM SET undo_retention = 900 SCOPE=BOTH;
```

Default is typically 900 seconds (15 minutes). Lower values free undo space
faster but increase risk of ORA-01555 (snapshot too old).

### 3. Kill Long-Running Transactions

If a single transaction is consuming excessive undo:

```sql
ALTER SYSTEM KILL SESSION '<sid>,<serial#>' IMMEDIATE;
```

### 4. Switch to a New UNDO Tablespace

Create a new, larger UNDO tablespace and switch:

```sql
CREATE UNDO TABLESPACE UNDOTBS2 DATAFILE SIZE 20G
  AUTOEXTEND ON NEXT 2G MAXSIZE 64G;
ALTER SYSTEM SET undo_tablespace = 'UNDOTBS2' SCOPE=BOTH;
```

Then drop the old one after all transactions complete:

```sql
DROP TABLESPACE UNDOTBS1 INCLUDING CONTENTS AND DATAFILES;
```

## Key Parameters

```sql
SELECT name, value FROM v$parameter
 WHERE name IN ('undo_tablespace', 'undo_retention', 'undo_management');
```

- `undo_management`: Should be `AUTO` (default since 11g)
- `undo_retention`: Seconds to retain undo data (default 900)
- `undo_tablespace`: Name of the active UNDO tablespace
