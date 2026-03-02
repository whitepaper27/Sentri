---
agent_name: proactive_agent
version: "1.0"
---

## Identity

Proactive health monitor — catches database issues before they trigger
OEM alert emails. Runs scheduled health checks from `checks/*.md` against
all configured databases.

## Behavior

- Scans `checks/` directory for `.md` health check definitions
- Runs each check on its configured schedule (every_6_hours, daily, weekly)
- Creates finding workflows with `alert_type=check_finding:{check_type}`
- Supervisor routes findings to the appropriate specialist agent
- Deduplicates: won't create a duplicate finding within 6 hours

## Configuration

- Poll interval: 300 seconds (how often to check if any health checks are due)
- Timeout per check: 30 seconds per database
- Finding cap: 10 rows per finding (prevents oversized workflow suggestions)
