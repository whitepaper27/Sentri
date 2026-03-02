---
version: "19c"
topic: tablespace
operation: temp_tablespace
keywords: [temp, tempfile, temporary, tablespace, resize, add tempfile]
applies_to: [temp_full]
web_source: "https://docs.oracle.com/en/database/oracle/oracle-database/19/admin/managing-tablespaces.html"
---

# Temporary Tablespace Management — Oracle 19c

## SMALLFILE Temporary Tablespace

### Add Tempfile

```sql
ALTER TABLESPACE <temp_tablespace_name> ADD TEMPFILE '<path>/<filename>.dbf' SIZE <size>;
```

### Add Tempfile with Autoextend

```sql
ALTER TABLESPACE <temp_tablespace_name> ADD TEMPFILE '<path>/<filename>.dbf' SIZE <size>
  AUTOEXTEND ON NEXT <increment> MAXSIZE <maxsize>;
```

### Add Tempfile (OMF)

```sql
ALTER TABLESPACE <temp_tablespace_name> ADD TEMPFILE SIZE <size>
  AUTOEXTEND ON NEXT <increment> MAXSIZE <maxsize>;
```

### Resize Existing Tempfile

```sql
ALTER DATABASE TEMPFILE '<path>/<filename>.dbf' RESIZE <new_size>;
```

## BIGFILE Temporary Tablespace

**CRITICAL: BIGFILE temp tablespace = ONE tempfile only. Use RESIZE.**

```sql
ALTER TABLESPACE <temp_tablespace_name> RESIZE <new_size>;
```

## Key Differences from Permanent Tablespace

- Use `ADD TEMPFILE` not `ADD DATAFILE`
- Use `ALTER DATABASE TEMPFILE` not `ALTER DATABASE DATAFILE` for resize
- Tempfiles are recreated on instance restart (data is transient)
- Check type: `SELECT tablespace_name, contents FROM dba_tablespaces` — look for `TEMPORARY`

## How to Check Temp Usage

```sql
SELECT tablespace_name, tablespace_size/1024/1024 AS size_mb,
       allocated_space/1024/1024 AS allocated_mb,
       free_space/1024/1024 AS free_mb
  FROM dba_temp_free_space;
```

## Common Sizes

- Standard tempfile: `SIZE 10G AUTOEXTEND ON NEXT 1G MAXSIZE 32G`
- Large sort operations: `SIZE 20G AUTOEXTEND ON NEXT 2G MAXSIZE 64G`
