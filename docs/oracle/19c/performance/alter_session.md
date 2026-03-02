---
version: "19c"
topic: performance
operation: alter_session
keywords: [session, alter session, resource limit, cpu, parallel, optimizer]
applies_to: [cpu_high, long_running_sql]
web_source: "https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/ALTER-SESSION.html"
---

# ALTER SESSION — Oracle 19c

## Resource Management

### Set Resource Consumer Group

Move a session to a different resource plan consumer group:

```sql
BEGIN
  DBMS_RESOURCE_MANAGER.SWITCH_CONSUMER_GROUP_FOR_SESS(
    session_id   => <sid>,
    session_serial => <serial#>,
    consumer_group => '<group_name>'
  );
END;
/
```

### Cancel Long-Running SQL

Cancel the current SQL statement without killing the session:

```sql
ALTER SYSTEM CANCEL SQL '<sid>,<serial#>';
```

Note: Available in Oracle 18c+ only.

## Session Parameters

### Set Session-Level Parameters

```sql
ALTER SESSION SET <parameter_name> = <value>;
```

Common examples:

```sql
ALTER SESSION SET optimizer_mode = 'ALL_ROWS';
ALTER SESSION SET parallel_degree_limit = 4;
ALTER SESSION SET sort_area_size = 104857600;
```

## CPU Management

### Identify High-CPU Sessions

```sql
SELECT s.sid, s.serial#, s.username, s.program,
       ss.value/100 AS cpu_seconds
  FROM v$session s
  JOIN v$sesstat ss ON s.sid = ss.sid
  JOIN v$statname sn ON ss.statistic# = sn.statistic#
 WHERE sn.name = 'CPU used by this session'
   AND s.status = 'ACTIVE'
   AND s.type = 'USER'
 ORDER BY ss.value DESC
 FETCH FIRST 10 ROWS ONLY;
```

### Resource Manager Intervention

For CPU-bound sessions, prefer Resource Manager over KILL:

1. Switch to a low-priority consumer group
2. Set CPU utilization limits via resource plan
3. Only KILL if the session doesn't respond to resource limits
