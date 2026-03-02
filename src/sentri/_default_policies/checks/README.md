# Proactive Health Check Definitions

Drop a `.md` file in this directory to add a new proactive health check.
No code changes required — the ProactiveAgent picks it up on next cycle.

## File Format

YAML frontmatter (between `---`) + Markdown sections with SQL code blocks.

### YAML Frontmatter (Required)

```yaml
---
check_type: unique_check_name
severity: LOW | MEDIUM | HIGH | CRITICAL
schedule: every_6_hours | daily | weekly
routes_to: specialist_agent_name
---
```

- `check_type`: Unique identifier (matches filename without `.md`)
- `severity`: How urgent findings are
- `schedule`: How often to run this check
- `routes_to`: Which specialist handles findings (storage_agent, sql_tuning_agent, rca_agent)

### Required Sections

| Section | Purpose |
|---------|---------|
| `## Description` | What this check detects and why it matters |
| `## Health Query` | SQL SELECT to run (must be read-only, fenced code block) |
| `## Threshold` | Numeric thresholds for alerting (bullet list of `key: value`) |
| `## Recommended Action` | SQL template to remediate (fenced code block) |

### Example

```markdown
---
check_type: my_check
severity: MEDIUM
schedule: daily
routes_to: storage_agent
---

## Description

Detects something that needs attention.

## Health Query

​```sql
SELECT column1, column2 FROM some_view WHERE condition
​```

## Threshold

- column1: 100
- column2: 50

## Recommended Action

​```sql
ALTER SOMETHING TO FIX IT
​```
```

## Existing Checks

| Check | Schedule | Routes To | Description |
|-------|----------|-----------|-------------|
| stale_stats | daily | sql_tuning_agent | Tables not analyzed in 30+ days |
| tablespace_trend | every_6_hours | storage_agent | Tablespaces above 85% usage |
