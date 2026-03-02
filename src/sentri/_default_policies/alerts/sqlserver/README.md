# SQL Server Alert Patterns

Drop `.md` alert pattern files here for SQL Server databases.

Same format as `alerts/*.md` — YAML frontmatter + Markdown sections (Email Pattern, Verification Query, Forward Action, Rollback Action, etc.).

Set `db_engine: sqlserver` in `config/sentri.yaml` for your SQL Server databases.

Example alert types: tempdb_full, log_full, blocking_chain, job_failure, availability_group_failover.
