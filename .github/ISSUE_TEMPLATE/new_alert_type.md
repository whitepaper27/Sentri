---
name: New Alert Type
about: Contribute a new alert .md file
title: "[Alert] "
labels: alert-type, contribution
---

**Alert type**
Name: [e.g. datafile_autoextend_off]

**Email pattern**
What does the alert email look like? Include a sample subject/body.

**Verification query**
SQL to check if the problem is real:
```sql
SELECT ...
```

**Fix SQL**
SQL to remediate:
```sql
ALTER ...
```

**Rollback SQL**
SQL to undo the fix:
```sql
ALTER ...
```

**Oracle versions tested**
[e.g. 19c, 21c]

**Notes**
Any edge cases, CDB/RAC considerations, or prerequisites.
