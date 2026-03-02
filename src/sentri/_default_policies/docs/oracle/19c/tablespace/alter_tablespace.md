---
version: "19c"
topic: tablespace
operation: alter_tablespace
keywords: [tablespace, add datafile, resize, autoextend, bigfile, smallfile, datafile]
applies_to: [tablespace_full]
web_source: "https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/ALTER-TABLESPACE.html"
---

# ALTER TABLESPACE — Oracle 19c

## SMALLFILE Tablespace (Standard)

SMALLFILE tablespaces can have multiple datafiles (up to db_files parameter limit).

### Add Datafile (with explicit path)

```sql
ALTER TABLESPACE <tablespace_name> ADD DATAFILE '<path>/<filename>.dbf' SIZE <size>;
```

### Add Datafile with Autoextend

```sql
ALTER TABLESPACE <tablespace_name> ADD DATAFILE '<path>/<filename>.dbf' SIZE <size>
  AUTOEXTEND ON NEXT <increment> MAXSIZE <maxsize>;
```

### Add Datafile (OMF — no path needed)

When `db_create_file_dest` is set (Oracle Managed Files), omit the path:

```sql
ALTER TABLESPACE <tablespace_name> ADD DATAFILE SIZE <size>
  AUTOEXTEND ON NEXT <increment> MAXSIZE <maxsize>;
```

### Resize Existing Datafile

```sql
ALTER DATABASE DATAFILE '<path>/<filename>.dbf' RESIZE <new_size>;
```

### Enable Autoextend on Existing Datafile

```sql
ALTER DATABASE DATAFILE '<path>/<filename>.dbf'
  AUTOEXTEND ON NEXT <increment> MAXSIZE <maxsize>;
```

## BIGFILE Tablespace

**CRITICAL: BIGFILE tablespaces have exactly ONE datafile. You CANNOT add another.**

### Resize (the ONLY valid operation)

```sql
ALTER TABLESPACE <tablespace_name> RESIZE <new_size>;
```

### INVALID — Do NOT Use

```sql
-- WRONG: This will fail with ORA-32771
ALTER TABLESPACE <tablespace_name> ADD DATAFILE SIZE <size>;
```

## How to Determine Tablespace Type

```sql
SELECT tablespace_name, bigfile
  FROM dba_tablespaces
 WHERE tablespace_name = '<name>';
```

- `bigfile = 'YES'` → BIGFILE → use RESIZE only
- `bigfile = 'NO'` → SMALLFILE → can ADD DATAFILE

## Common Sizes

- Standard datafile: `SIZE 10G AUTOEXTEND ON NEXT 1G MAXSIZE 32G`
- Large datafile: `SIZE 20G AUTOEXTEND ON NEXT 2G MAXSIZE 32G`
- BIGFILE resize: `RESIZE 50G` (or larger as needed)

## Path Convention

New datafiles should follow the existing naming convention:
- Check existing paths: `SELECT file_name FROM dba_data_files WHERE tablespace_name = '<name>'`
- Use the same directory and similar filename pattern
- Example: if existing is `/u01/oradata/MYDB/users01.dbf`, new should be `/u01/oradata/MYDB/users02.dbf`
