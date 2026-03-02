---
version: "12c"
topic: tablespace
operation: alter_tablespace
keywords: [tablespace, add datafile, resize, autoextend, bigfile, smallfile, datafile]
applies_to: [tablespace_full]
web_source: "https://docs.oracle.com/en/database/oracle/oracle-database/12.2/sqlrf/ALTER-TABLESPACE.html"
---

# ALTER TABLESPACE — Oracle 12c (12.2)

## SMALLFILE Tablespace (Standard)

SMALLFILE tablespaces can have multiple datafiles (up to db_files parameter limit).

### Add Datafile

```sql
ALTER TABLESPACE <name> ADD DATAFILE '<path>' SIZE <size>;
ALTER TABLESPACE <name> ADD DATAFILE '<path>' SIZE <size>
  AUTOEXTEND ON NEXT <increment> MAXSIZE <max>;
```

With Oracle Managed Files (OMF) — no path needed:

```sql
ALTER TABLESPACE <name> ADD DATAFILE SIZE <size>;
```

### Resize Existing Datafile

```sql
ALTER DATABASE DATAFILE '<path>' RESIZE <new_size>;
```

### Enable Autoextend

```sql
ALTER DATABASE DATAFILE '<path>'
  AUTOEXTEND ON NEXT <increment> MAXSIZE <max>;
```

## BIGFILE Tablespace

**CRITICAL: BIGFILE tablespaces have exactly ONE datafile. Cannot ADD DATAFILE.**

### Resize (Only Valid Operation)

```sql
ALTER TABLESPACE <name> RESIZE <new_size>;
```

**NEVER use ADD DATAFILE on a BIGFILE tablespace — it will fail with ORA-32771.**

## 12c-Specific Notes

- 12c introduced `IF EXISTS` clause (12.2+): `ALTER TABLESPACE ... IF EXISTS`
- Container databases (CDB): Must be in the correct PDB context
- Multitenant: `ALTER SESSION SET CONTAINER = <pdb_name>` before tablespace operations

## Common Sizes

- Standard datafile: `SIZE 10G AUTOEXTEND ON NEXT 1G MAXSIZE 32G`
- Large datafile: `SIZE 20G AUTOEXTEND ON NEXT 2G MAXSIZE 32G`
- BIGFILE resize: `RESIZE 50G` (or larger as needed)

## Path Convention

New datafiles should follow the existing naming convention:
- Check existing paths: `SELECT file_name FROM dba_data_files WHERE tablespace_name = '<name>'`
- Use the same directory and similar filename pattern
- Example: if existing is `/u01/oradata/MYDB/users01.dbf`, new should be `/u01/oradata/MYDB/users02.dbf`
