"""SQL queries for the memory system (short-term + long-term).

All memory queries live here for clean separation. Each query is database-scoped
(always filters by database_id) and time-bounded.
"""

# Recent executed actions from audit_log (scoped by database + time window)
RECENT_ACTIONS_SQL = """\
SELECT action_type, action_sql, result, error_message, timestamp, database_id
FROM audit_log
WHERE database_id = ?
  AND timestamp > datetime('now', ? || ' hours')
ORDER BY timestamp DESC
LIMIT ?
"""

# Recent workflow outcomes (scoped by database + alert type + time window)
RECENT_OUTCOMES_SQL = """\
SELECT alert_type, status, verification, metadata, created_at, database_id
FROM workflows
WHERE database_id = ?
  AND alert_type = ?
  AND created_at > datetime('now', ? || ' hours')
  AND status IN ('COMPLETED', 'FAILED', 'ROLLED_BACK', 'ESCALATED')
ORDER BY created_at DESC
LIMIT ?
"""

# Failed approaches — longer lookback, LLM should avoid repeating these
FAILED_APPROACHES_SQL = """\
SELECT action_type, action_sql, error_message, timestamp
FROM audit_log
WHERE database_id = ?
  AND result = 'FAILED'
  AND timestamp > datetime('now', ? || ' days')
ORDER BY timestamp DESC
LIMIT 10
"""

# ---------------------------------------------------------------------------
# v3.3: Long-term memory queries (90-day window)
# ---------------------------------------------------------------------------

# Historical alert summary — all terminal workflows per database
ALERT_HISTORY_SQL = """\
SELECT alert_type, created_at, status,
       CAST(strftime('%w', created_at) AS INTEGER) AS day_of_week
FROM workflows
WHERE database_id = ?
  AND created_at > datetime('now', ? || ' days')
  AND status IN ('COMPLETED', 'FAILED', 'ROLLED_BACK', 'ESCALATED',
                  'VERIFICATION_FAILED')
ORDER BY alert_type, created_at DESC
"""

# Historical failure stats — action_types with repeated failures
FAILURE_STATS_SQL = """\
SELECT action_type,
       COUNT(*) AS total,
       SUM(CASE WHEN result = 'FAILED' THEN 1 ELSE 0 END) AS failures,
       SUM(CASE WHEN result = 'SUCCESS' THEN 1 ELSE 0 END) AS successes,
       GROUP_CONCAT(
           CASE WHEN result = 'FAILED' THEN error_message END, ' | '
       ) AS error_messages
FROM audit_log
WHERE database_id = ?
  AND timestamp > datetime('now', ? || ' days')
GROUP BY action_type
HAVING total >= 2
ORDER BY total DESC
"""
