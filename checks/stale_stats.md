---
check_type: stale_stats
severity: MEDIUM
schedule: daily
routes_to: sql_tuning_agent
---

## Description

Detects tables with stale optimizer statistics (not analyzed in over 30 days).
Stale statistics lead to poor execution plans and performance degradation.

## Health Query

```sql
SELECT owner, table_name,
       last_analyzed,
       num_rows,
       ROUND(SYSDATE - last_analyzed) as days_since_analyzed
FROM dba_tables
WHERE owner NOT IN ('SYS','SYSTEM','DBSNMP','OUTLN','MDSYS','ORDSYS','CTXSYS','XDB','WMSYS','APPQOSSYS','DBSFWUSER','GSMADMIN_INTERNAL','LBACSYS','OJVMSYS','REMOTE_SCHEDULER_AGENT')
AND last_analyzed < SYSDATE - 30
AND num_rows > 1000
ORDER BY days_since_analyzed DESC
```

## Threshold

- min_rows: 1000
- max_days_stale: 30
- max_tables: 10

## Recommended Action

Gather fresh optimizer statistics on the detected tables.

```sql
BEGIN DBMS_STATS.GATHER_TABLE_STATS(ownname => '{owner}', tabname => '{table_name}', estimate_percent => DBMS_STATS.AUTO_SAMPLE_SIZE, method_opt => 'FOR ALL COLUMNS SIZE AUTO'); END;
```
