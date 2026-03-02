---
type: core_policy
name: memory_rules
version: 1
---

# Memory Rules

This document defines what the Sentri system retains, what it discards, how long data is kept, and the caching strategy for frequently accessed external data. These rules ensure the system maintains enough history for learning and auditing while preventing unbounded storage growth.

## What to Remember

The following data categories are retained by the system and are considered essential for operation, compliance, and future learning.

### Workflow History

**Retention**: Indefinite (no automatic deletion)

Every workflow record in the `workflows` table is kept permanently. Workflow records represent the complete lifecycle of an alert from detection through resolution and are the primary source of truth for operational metrics.

Workflow records include:
- Alert type and source metadata
- Verification results and confidence scores
- Execution plans (forward action and rollback)
- Execution results (success, failure, rollback)
- Approval chain (who approved, when, and why)
- Timing data (created, updated, completed timestamps)
- All state transitions with timestamps

**Rationale**: Workflow history is required for compliance auditing, operational reporting, and future training of the Analyst agent (Agent 5). The storage cost of workflow records is minimal (typically under 5 KB per workflow) and the value of long-term trend analysis outweighs the storage cost.

### Confidence Scores

**Retention**: Indefinite

Confidence scores represent the system's learned accuracy for each alert type and environment combination. These are stored as part of the workflow record (in the verification field) and aggregated for reporting.

Current confidence score tracking (POC):
- Per alert type: overall success rate of verification (Agent 2 accuracy).
- Per alert type per environment: success rate segmented by environment.
- Per database: success rate for a specific database (detects problematic instances).

Future confidence score tracking (when Agent 5 is implemented):
- Bayesian confidence with prior distributions.
- Decay weighting (recent outcomes weighted more heavily than old ones).
- Anomaly detection on confidence trends.

### Pattern Success Rates

**Retention**: Indefinite

For each of the five alert types, the system tracks:

| Metric | Description | Source |
|--------|-------------|--------|
| Detection accuracy | Percentage of parsed alerts that are real (not false positives) | Agent 2 verification results |
| Execution success rate | Percentage of executions that complete without rollback | Agent 4 execution results |
| Rollback rate | Percentage of executions that required rollback | Agent 4 execution results |
| Mean resolution time | Average time from DETECTED to COMPLETED | Workflow timestamps |
| False positive rate | Percentage of alerts that fail verification | Agent 2 verification results |

These metrics are computed on demand by querying the workflow history. They are not stored separately but derived from the retained workflow records.

### Audit Log

**Retention**: See Retention Policy section below

The audit log is an append-only, immutable record of every action taken by the system. Audit entries are never modified or deleted during their retention period. See the audit_log table schema in the database design for the full structure.

## What to Forget

The following data categories are actively purged after their retention period expires.

### Raw Email Bodies

**Retention**: 30 days after workflow creation

The full text of parsed alert emails is stored temporarily for debugging and reprocessing purposes. After 30 days, the raw email body is purged from the workflow metadata. The parsed metadata (alert type, database, metrics) is retained as part of the workflow record.

**Purge method**: A scheduled task runs daily at 02:00 UTC and removes the `raw_email_body` field from the `metadata` JSON column in workflows older than 30 days.

**Rationale**: Raw email bodies may contain sensitive information (hostnames, internal IPs, personnel names) and have no operational value after the workflow is complete and the parsed data is retained.

### Cache Entries

**Retention**: Per TTL (see Cache TTLs section below)

Cache entries are stored in the `cache` table with an explicit `expires_at` timestamp. Expired entries are purged automatically.

**Purge method**: The cache cleanup task runs every 15 minutes and deletes all rows where `expires_at < CURRENT_TIMESTAMP`.

### Temporary Execution Artifacts

**Retention**: 7 days

During execution, Agent 4 may generate temporary data such as:
- DBMS_OUTPUT capture from PL/SQL blocks.
- Session trace file references.
- Execution plan output from EXPLAIN PLAN.

These artifacts are stored in the workflow metadata and purged after 7 days.

### Connection Test Results

**Retention**: 1 hour

The system periodically tests database connectivity as a precondition for execution. The results of these tests (success/failure, latency, timestamp) are cached for 1 hour. Stale results are purged by the standard cache cleanup.

## Retention Policy

| Data Category | Retention Period | Storage Location | Purge Method |
|--------------|-----------------|------------------|-------------|
| Audit log entries | 1 year | audit_log table | Scheduled purge: daily at 03:00 UTC |
| Workflow records | 90 days (active data) | workflows table | Archived to cold storage, then purged |
| Workflow summary | Indefinite | workflow_summary table (future) | Never purged |
| Raw email bodies | 30 days | workflows.metadata JSON | Scheduled purge: daily at 02:00 UTC |
| Temporary artifacts | 7 days | workflows.metadata JSON | Scheduled purge: daily at 02:00 UTC |
| Cache entries | Per TTL | cache table | Continuous cleanup every 15 minutes |
| Resource lock records | 30 days | resource_locks table | Scheduled purge: daily at 04:00 UTC |
| Application logs | 14 days | Log files on disk | Log rotation via standard tooling |

### Audit Log Archival

After 1 year, audit log entries are archived before deletion:

1. Entries older than 1 year are exported to a JSON file: `~/.sentri/archive/audit_YYYY.json`.
2. The export is verified (row count matches).
3. The archived entries are deleted from the SQLite table.
4. The archive file is retained indefinitely on disk.

This ensures compliance requirements are met while keeping the active SQLite database performant.

### Workflow Archival

After 90 days, completed workflow records follow a similar archival process:

1. Workflows in terminal states (COMPLETED, DENIED, ESCALATED) older than 90 days are exported.
2. A summary record is preserved (workflow ID, alert type, database, outcome, timestamps) for indefinite retention.
3. The full workflow record is deleted from the active table.
4. Archive files are stored in `~/.sentri/archive/workflows_YYYY_MM.json`.

Workflows that are not in a terminal state (e.g., stuck in AWAITING_APPROVAL due to a bug) are flagged for review rather than archived.

## Cache TTLs

The cache table stores frequently accessed data to reduce external lookups and database queries. Each cache entry has a specific TTL based on its category.

| Cache Category | TTL | Key Pattern | Description |
|---------------|-----|-------------|-------------|
| MOS documents | 24 hours | `mos:doc:{doc_id}` | Oracle My Oracle Support document content (future use) |
| Database metadata | 1 hour | `dbmeta:{database_id}` | Oracle version, architecture, tablespace list |
| Connection status | 5 minutes | `conn:{database_id}` | Last connection test result (success/fail/latency) |
| Environment config | 1 hour | `env:{database_id}` | Environment registry entry for a database |
| Alert patterns | 24 hours | `pattern:{alert_type}` | Compiled regex patterns for email parsing |
| Policy files | 1 hour | `policy:{policy_name}` | Parsed policy file content |

### Cache Invalidation

In addition to TTL-based expiry, certain events trigger immediate cache invalidation:

- **Configuration change**: When a DBA updates `sentri.yaml` or any environment file, all `env:*` and `dbmeta:*` cache entries are invalidated.
- **Policy update**: When a `.md` policy file is modified, the corresponding `policy:*` cache entry is invalidated.
- **Manual flush**: The `sentri cache clear` CLI command purges all cache entries immediately.

### Cache Miss Behavior

When a cache miss occurs:

1. The system fetches the data from the authoritative source (database query, file read, etc.).
2. The result is stored in the cache with the appropriate TTL.
3. If the authoritative source is unavailable, the system checks for a stale cache entry (expired but not yet purged). If a stale entry exists, it is used with a WARNING log indicating stale data is in use. This prevents cascading failures when an external dependency is temporarily unavailable.

### Cache Storage Limits

The cache table should not exceed 10,000 entries or 50 MB of total data. If either limit is approached:

1. Entries with the shortest remaining TTL are evicted first.
2. A WARNING is logged indicating cache pressure.
3. If the limit is exceeded despite eviction, the oldest expired entries are force-purged immediately.

## Short-Term Memory

Configuration for the short-term memory system that provides recent action context to the LLM researcher. Memory is always scoped per database — actions on database A never appear in the context for database B.

### Lookback Windows

| Context Type | Default Window | Max Items | Description |
|-------------|----------------|-----------|-------------|
| Recent actions | 24 hours | 10 | Executed actions from audit_log |
| Recent outcomes | 24 hours | 10 | Workflow outcomes (completed/failed/escalated) |
| Failed approaches | 30 days | 10 | Failed actions to avoid repeating |

### Memory Rules for LLM

When recent actions are provided to the researcher:

- Do NOT repeat an action done less than 6 hours ago on the same target
- If the same alert fired again within 24 hours after a successful fix, suggest a LARGER action or escalate
- If a previous action FAILED, do NOT suggest the same approach — try an alternative
- Reference specific past actions in your reasoning (e.g., "10GB was added 4h ago but filled again")
- If 3+ actions on the same database in 24 hours, recommend root cause investigation

### Per-Environment Overrides

| Environment | Lookback | Notes |
|-------------|----------|-------|
| PROD | 48 hours | Longer memory for production — more cautious |
| UAT | 24 hours | Standard |
| DEV | 12 hours | Shorter memory — DEV changes frequently |

## Long-Term Memory

Configuration for long-term historical context. The system provides the LLM with summarized alert history so it can detect recurring patterns (weekly, biweekly, monthly) and avoid approaches that have failed before.

### History Settings

| Setting | Value | Description |
|---------|-------|-------------|
| History lookback | 90 days | How far back to include in historical summary |
| Max events per alert | 10 | Maximum events shown per alert type |

### LLM Rules for Long-Term Patterns

When historical alert patterns are provided to the researcher:

- Look for recurring intervals between events (daily, weekly, biweekly, monthly)
- Note day-of-week clustering (e.g., all on Fridays suggests batch job or maintenance window)
- If a pattern suggests root cause, recommend addressing root cause not just symptoms
- If same alert recurs after successful fix, suggest LARGER proactive action or escalation
- If an action_type has high failure rate, suggest alternative approaches
- Reference specific dates and patterns in your reasoning

## Investigation Memory

Configuration for investigation analysis files saved by specialist agents. Each investigation is saved as a `.md` file in `~/.sentri/investigations/` with full findings, candidates considered, and the decision rationale.

### Investigation Settings

| Setting | Value | Description |
|---------|-------|-------------|
| Retention | 90 days | How long to keep investigation .md files |
| Max files per prompt | 5 | Maximum past investigations loaded into LLM context |
| File format | Markdown with YAML frontmatter | Human-readable, DBA-inspectable |
| Naming | `YYYY-MM-DD_HHMMSS_{db}_{alert}.md` | Chronological, sortable |

### LLM Rules for Past Investigations

When past investigation analyses are provided to the researcher:

- Review what was found in prior investigations on the same database
- If a prior investigation identified root cause, check if the same root cause applies now
- Reference specific findings (wait events, top SQL, blocking chains) from past investigations
- If a prior fix was selected and succeeded, consider the same approach first
- If a prior fix was selected and failed, avoid the same approach — try alternatives
- Use investigation history to build confidence in recurring patterns
