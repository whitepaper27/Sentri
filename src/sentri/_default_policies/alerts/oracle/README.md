# Oracle Alert Patterns

Drop `.md` alert pattern files here for Oracle databases.

Same format as `alerts/*.md` — YAML frontmatter + Markdown sections (Email Pattern, Verification Query, Forward Action, Rollback Action, etc.).

Set `db_engine: oracle` in `config/sentri.yaml` for your Oracle databases.

Existing alert patterns in the parent `alerts/` directory also apply to Oracle databases for backwards compatibility.
