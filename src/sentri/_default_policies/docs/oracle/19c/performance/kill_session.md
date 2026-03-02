---
version: "19c"
topic: performance
operation: kill_session
keywords: [kill session, blocking, session, sid, serial, alter system]
applies_to: [session_blocker, cpu_high, long_running_sql]
web_source: "https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/ALTER-SYSTEM.html"
---

# Kill Session — Oracle 19c

## ALTER SYSTEM KILL SESSION

Terminates a database session. The session's transaction is rolled back.

### Standard Kill

```sql
ALTER SYSTEM KILL SESSION '<sid>,<serial#>';
```

### Immediate Kill

Forces immediate termination (doesn't wait for transaction rollback):

```sql
ALTER SYSTEM KILL SESSION '<sid>,<serial#>' IMMEDIATE;
```

### RAC — Kill Session on Specific Instance

On RAC, include the `@inst_id` to target the correct instance:

```sql
ALTER SYSTEM KILL SESSION '<sid>,<serial#>,@<inst_id>';
```

## ALTER SYSTEM DISCONNECT SESSION

More aggressive — kills the OS process. Use when KILL SESSION hangs:

```sql
ALTER SYSTEM DISCONNECT SESSION '<sid>,<serial#>' IMMEDIATE;
```

## Finding the SID and Serial#

### By Blocking Session

```sql
SELECT blocking_session AS blocker_sid,
       (SELECT serial# FROM v$session WHERE sid = blocking_session) AS blocker_serial
  FROM v$session
 WHERE blocking_session IS NOT NULL;
```

### By SQL_ID (long-running)

```sql
SELECT sid, serial#, username, sql_id, status,
       last_call_et AS seconds_active
  FROM v$session
 WHERE sql_id = '<sql_id>'
   AND status = 'ACTIVE';
```

### By Resource Consumption

```sql
SELECT sid, serial#, username, program,
       last_call_et AS seconds_active
  FROM v$session
 WHERE status = 'ACTIVE'
   AND type = 'USER'
 ORDER BY last_call_et DESC;
```

## Safety Notes

- Always verify the session belongs to the expected user/program before killing
- Killing SYS or background sessions can crash the instance
- Protected sessions (set by `ALTER SESSION SET CONTAINER` or `DBMS_SESSION`) may require IMMEDIATE
- Rollback of large transactions may take time even after KILL
