---
rule_id: cdb_context_required
severity: HIGH
applies_to: [tablespace_full, temp_full, high_undo_usage]
---

# CDB: Container Context Required

## Rule

In a Container Database (CDB), tablespace operations must be executed in the
correct PDB context. Running them in the CDB$ROOT will affect the wrong
container or fail.

## Detection Pattern

```regex
(?i)ALTER\s+(TABLESPACE|DATABASE\s+DATAFILE|DATABASE\s+TEMPFILE)
```

## Condition

Database context: `is_cdb == True` (from `v$database.cdb = 'YES'`)

## Required Action

Ensure the session is connected to the correct PDB before executing tablespace
operations. Use `ALTER SESSION SET CONTAINER = <pdb_name>` first, or connect
directly to the PDB service.

## Example

```sql
-- Switch to the PDB first
ALTER SESSION SET CONTAINER = MYPDB;

-- Then execute the tablespace operation
ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G;
```

## Note

This rule is informational — the SQL itself may be correct, but the execution
context matters. The validator flags this as a reminder to verify PDB context.
