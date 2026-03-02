---
type: agent_config
name: scout
version: 1
---

# Agent 1: The Scout (Email Parser)

## Purpose

Monitor the configured IMAP inbox for DBA alert emails, parse each message against
known alert patterns using regular expressions, and create structured Suggestion
objects for downstream processing. The Scout is the entry point for all automated
workflows -- no alert is acted upon unless it first passes through this agent.

The Scout continuously polls the inbox on a fixed interval, ensuring near-real-time
detection of database issues reported via email alerting systems (OEM, Grid Control,
custom monitors, etc.).

## Input

| Parameter | Description |
|-----------|-------------|
| **IMAP Connection** | Authenticated connection to the configured DBA alerts mailbox (e.g., `dba-alerts@company.com`) |
| **Poll Interval** | Every **60 seconds** (configurable in `auracore.yaml`) |
| **Alert Patterns** | Regex patterns loaded from `alerts/*.md` policy files |

The Scout reads email metadata (subject, body, sender, timestamp) and matches it
against the five supported alert types defined in the `alerts/` directory.

## Output

A **Suggestion** object written to the `workflows` table in SQLite with status
`DETECTED`. The Suggestion contains:

- `alert_type` -- One of: `tablespace_full`, `archive_dest_full`, `temp_full`, `listener_down`, `archive_gap`
- `database_id` -- The target database extracted from the email
- `environment` -- Looked up from `environment_registry` table
- `suggestion` -- JSON blob containing all parsed metadata (tablespace name, percentage, paths, etc.)
- `status` -- Always `DETECTED` on creation
- `created_at` -- Timestamp of detection

After writing the Suggestion, the Scout signals the orchestrator via a
`threading.Event` so processing can begin immediately without waiting for
the next poll cycle.

## Behavioral Rules

1. **Stateless** -- The Scout retains no memory between polling cycles. Each cycle
   is independent; all state is persisted to SQLite.

2. **Read-Only on Email** -- The Scout never deletes emails. It marks processed
   emails as read (IMAP `\Seen` flag) after successful parsing.

3. **Deterministic Parsing** -- All pattern matching uses regex definitions from
   `alerts/*.md` files. No heuristic or probabilistic matching in the POC.

4. **Idempotent** -- If the same alert email is encountered again (e.g., IMAP
   reconnect), the Scout checks for duplicates by message-id before creating
   a new Suggestion.

5. **Background Thread** -- Runs in an infinite loop on a dedicated daemon thread.
   Does not block the main orchestrator thread.

6. **Pattern-Only** -- Only processes emails that match a known alert pattern.
   Unmatched emails are silently skipped (logged at DEBUG level).

## Timeout

| Operation | Timeout | Action on Timeout |
|-----------|---------|-------------------|
| IMAP connection | **30 seconds** | Retry up to 3 times, then log ERROR and wait for next cycle |
| Per-email parsing | **5 seconds** | Log warning, skip the email, continue to next |
| Full poll cycle | **120 seconds** | Abort cycle, log ERROR, start fresh next cycle |

## Error Handling

- **Unparseable emails**: Log the email subject and sender at WARN level, increment
  `scout_parse_failures` counter, skip the email, and continue processing remaining
  emails in the batch.

- **IMAP connection failure**: Retry the connection up to **3 times** with
  exponential backoff (2s, 4s, 8s). If all retries fail, log at ERROR level and
  sleep until the next poll interval.

- **SQLite write failure**: Log at ERROR level, do **not** mark the email as read
  (so it will be retried on the next cycle), and continue.

- **Duplicate detection**: If a Suggestion for the same `database_id` + `alert_type`
  already exists with status in (`DETECTED`, `VERIFIED`, `AWAITING_APPROVAL`,
  `EXECUTING`), skip creating a duplicate and log at INFO level.

- **Pattern file errors**: If an `alerts/*.md` file fails to parse at startup, log
  at ERROR level and exclude that alert type from detection. Other alert types
  continue to function.
