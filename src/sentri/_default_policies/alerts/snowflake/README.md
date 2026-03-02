# Snowflake Alert Patterns

Drop `.md` alert pattern files here for Snowflake databases.

Same format as `alerts/*.md` — YAML frontmatter + Markdown sections (Email Pattern, Verification Query, Forward Action, Rollback Action, etc.).

Set `db_engine: snowflake` in `config/sentri.yaml` for your Snowflake warehouses.

Example alert types: warehouse_suspended, credit_usage_spike, query_timeout, clustering_degraded, storage_growth.
