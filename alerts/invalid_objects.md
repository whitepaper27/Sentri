---
type: alert_pattern
name: invalid_objects
severity: MEDIUM
action_type: COMPILE_INVALID_OBJECTS
version: "1.0"
---

# Invalid Objects

Detects when database objects (procedures, functions, packages, triggers, views) are in INVALID state,
typically after DDL changes, imports, or Oracle upgrades. Remediates by recompiling all invalid objects
using UTL_RECOMP.

## Email Pattern

```regex
(?i)(?:invalid\s+(?:database\s+)?objects?|objects?\s+in\s+invalid\s+state).*?(?:(\d+)\s+(?:invalid\s+)?objects?\s+)?(?:on|database|db)\s+(\S+)
```

## Extracted Fields

- `invalid_count` = group(1) -- Number of invalid objects (may be absent; use 0 as default)
- `database_id` = group(2) -- Target database identifier

## Verification Query

```sql
SELECT COUNT(*) AS invalid_count
  FROM dba_objects
 WHERE status = 'INVALID'
   AND object_type IN ('PROCEDURE', 'FUNCTION', 'PACKAGE', 'PACKAGE BODY',
                       'TRIGGER', 'VIEW', 'TYPE', 'TYPE BODY', 'SYNONYM')
   AND owner NOT IN ('SYS', 'SYSTEM', 'OUTLN', 'XDB', 'WMSYS', 'DBSNMP',
                     'APEX_PUBLIC_USER', 'FLOWS_FILES');
```

Lists the invalid objects for diagnostic context:

```sql
SELECT owner, object_name, object_type, last_ddl_time
  FROM dba_objects
 WHERE status = 'INVALID'
   AND object_type IN ('PROCEDURE', 'FUNCTION', 'PACKAGE', 'PACKAGE BODY',
                       'TRIGGER', 'VIEW', 'TYPE', 'TYPE BODY', 'SYNONYM')
   AND owner NOT IN ('SYS', 'SYSTEM', 'OUTLN', 'XDB', 'WMSYS', 'DBSNMP',
                     'APEX_PUBLIC_USER', 'FLOWS_FILES')
 ORDER BY owner, object_type, object_name
 FETCH FIRST 50 ROWS ONLY;
```

## Tolerance

- `invalid_count`: Must be >= 1. If 0 invalid objects exist at verification time, treat as self-resolved.
- Verification proceeds even if the reported count differs from live count (objects may have been
  manually recompiled between alert and verification).

## Pre-Flight Checks

- UTL_RECOMP package is accessible -- rowcount > 0

```sql
SELECT COUNT(*) FROM dba_objects WHERE object_name = 'UTL_RECOMP' AND owner = 'SYS' AND status = 'VALID';
```

- Database is in READ WRITE mode -- READ WRITE

```sql
SELECT open_mode FROM v$database;
```

## Forward Action

```sql
EXEC UTL_RECOMP.RECOMP_PARALLEL(4);
```

Recompiles all invalid objects in the database using 4 parallel workers. UTL_RECOMP handles
dependency ordering automatically — it recompiles in the correct order to resolve inter-object
dependencies.

**Post-execution validation step**: Query remaining invalid objects:

```sql
SELECT COUNT(*) AS still_invalid
  FROM dba_objects
 WHERE status = 'INVALID'
   AND owner NOT IN ('SYS', 'SYSTEM', 'OUTLN', 'XDB', 'WMSYS', 'DBSNMP',
                     'APEX_PUBLIC_USER', 'FLOWS_FILES');
```

**Safety notes**:
- UTL_RECOMP.RECOMP_PARALLEL is safe to run on a live database.
- It does not lock objects during recompilation (uses ALTER ... COMPILE).
- If some objects remain invalid after recompilation, they likely depend on missing
  base objects — escalate to the DBA for root cause analysis.
- Do not run during peak load; recompilation acquires brief compile locks.

## Rollback Action

```sql
-- N/A: Recompilation is idempotent and non-destructive.
-- If recompilation causes unexpected errors, individual objects can be examined via:
-- SELECT object_name, object_type, status FROM dba_objects WHERE status = 'INVALID';
-- No data is modified. Rollback is not applicable.
```

## Validation Query

```sql
SELECT COUNT(*) AS still_invalid
  FROM dba_objects
 WHERE status = 'INVALID'
   AND object_type IN ('PROCEDURE', 'FUNCTION', 'PACKAGE', 'PACKAGE BODY',
                       'TRIGGER', 'VIEW', 'TYPE', 'TYPE BODY', 'SYNONYM')
   AND owner NOT IN ('SYS', 'SYSTEM', 'OUTLN', 'XDB', 'WMSYS', 'DBSNMP',
                     'APEX_PUBLIC_USER', 'FLOWS_FILES');
```

**Success criteria**: `still_invalid` should be 0 or significantly less than the pre-fix count.
Persistent invalids after recompilation indicate missing dependencies requiring DBA investigation.

## Risk Level

LOW -- UTL_RECOMP.RECOMP_PARALLEL recompiles objects online without affecting running sessions.
Compile locks are brief. No data is modified. The operation is safe on production systems.

## Expected Downtime

NONE -- Recompilation is fully online. Running sessions are not interrupted.

## Estimated Duration

~30 seconds for a typical database with < 100 invalid objects. Larger schemas with complex
package hierarchies may take 2–5 minutes.
