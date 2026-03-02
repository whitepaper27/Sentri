# PostgreSQL Alert Patterns

Drop `.md` alert pattern files here for PostgreSQL databases.

Same format as `alerts/*.md` — YAML frontmatter + Markdown sections (Email Pattern, Verification Query, Forward Action, Rollback Action, etc.).

Set `db_engine: postgres` in `config/sentri.yaml` for your PostgreSQL databases.

Example alert types: connection_exhaustion, replication_lag, bloat_detected, vacuum_not_running, long_transaction.
