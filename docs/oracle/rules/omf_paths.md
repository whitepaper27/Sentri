---
rule_id: omf_no_explicit_path
severity: HIGH
applies_to: [tablespace_full, temp_full]
---

# OMF: No Explicit Paths

## Rule

When Oracle Managed Files (OMF) is enabled (`db_create_file_dest` is set),
you should NOT specify explicit file paths in `ADD DATAFILE` or `ADD TEMPFILE`
commands. Oracle will automatically manage the file location and naming.

Using explicit paths with OMF is not an error but is bad practice — it bypasses
OMF's automatic file management and can lead to inconsistent file locations.

## Detection Pattern

```regex
(?i)(ADD\s+(DATA|TEMP)?FILE\s+['\"])
```

## Condition

Database context: `omf_enabled == True` (from `v$parameter` where `name = 'db_create_file_dest'` has a non-empty value)

## Required Action

Omit the file path. Use `ADD DATAFILE SIZE <size>` instead of `ADD DATAFILE '<path>' SIZE <size>`.

## Example Violation

- BAD: `ALTER TABLESPACE USERS ADD DATAFILE '/u01/oradata/MYDB/users02.dbf' SIZE 10G`
- GOOD: `ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G AUTOEXTEND ON NEXT 1G MAXSIZE 32G`
