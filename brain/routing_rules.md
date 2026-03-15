---
type: brain_config
name: routing_rules
version: "1.0"
---

# Routing Rules

Configuration for the Supervisor's deterministic router. Maps alert types
to specialist agents, defines categories for correlation, and sets fallback.

## Direct Routing

- tablespace_full → storage_agent
- temp_full → storage_agent
- archive_dest_full → storage_agent
- high_undo_usage → storage_agent
- long_running_sql → sql_tuning_agent
- cpu_high → sql_tuning_agent
- session_blocker → rca_agent
- check_finding:stale_stats → sql_tuning_agent
- check_finding:tablespace_trend → storage_agent
- check_finding:* → storage_agent
- unknown → unknown_alert_agent

## Alert Categories

- storage: [tablespace_full, temp_full, archive_dest_full, high_undo_usage]
- performance: [cpu_high, long_running_sql, session_blocker]
- security: [password_expiry]
- backup: [backup_freshness, archive_gap]

## Fallback

- * → storage_agent
