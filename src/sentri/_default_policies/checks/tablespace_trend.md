---
check_type: tablespace_trend
severity: HIGH
schedule: every_6_hours
routes_to: storage_agent
---

## Description

Proactively detects tablespaces approaching capacity before alerts trigger.
Identifies tablespaces above 85% utilization that may fill up soon.

## Health Query

```sql
SELECT ts.tablespace_name,
       ts.status,
       ROUND((ts.used_space * ts.block_size) / (1024*1024)) as used_mb,
       ROUND((ts.tablespace_size * ts.block_size) / (1024*1024)) as total_mb,
       ROUND(ts.used_percent, 1) as pct_used
FROM dba_tablespace_usage_metrics ts
WHERE ts.used_percent > 85
ORDER BY ts.used_percent DESC
```

## Threshold

- pct_used: 85
- exclude_tablespaces: TEMP,UNDOTBS1

## Recommended Action

Add datafile to the tablespace before it reaches critical threshold.

```sql
ALTER TABLESPACE {tablespace_name} ADD DATAFILE SIZE 500M AUTOEXTEND ON NEXT 100M MAXSIZE 2G
```
