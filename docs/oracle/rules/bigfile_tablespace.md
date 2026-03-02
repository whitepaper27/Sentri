---
rule_id: bigfile_no_add_datafile
severity: CRITICAL
applies_to: [tablespace_full, temp_full]
---

# BIGFILE: No ADD DATAFILE

## Rule

BIGFILE tablespaces can only have exactly ONE datafile. The `ADD DATAFILE` and
`ADD TEMPFILE` commands are invalid for BIGFILE tablespaces and will fail with
ORA-32771.

## Detection Pattern

```regex
(?i)ADD\s+(DATA|TEMP)?FILE
```

## Condition

Database context: `tablespace_type == "BIGFILE"` (from `dba_tablespaces.bigfile = 'YES'`)

## Required Action

Use `ALTER TABLESPACE <name> RESIZE <size>` instead of ADD DATAFILE.

## Example Violation

- BAD: `ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G`
- GOOD: `ALTER TABLESPACE USERS RESIZE 50G`
