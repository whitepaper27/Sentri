---
type: agent_config
name: auditor
version: 1
---

# Agent 2: The Auditor (Verifier)

## Purpose

Validate that alerts detected by Agent 1 (Scout) represent real, actionable database
issues and are not false positives. The Auditor connects directly to the target Oracle
database, runs read-only verification queries, compares actual metrics against the
values reported in the alert email, and checks for duplicate active workflows.

The Auditor is the quality gate of the system. No fix is ever proposed or executed
unless the Auditor confirms the issue is genuine with a sufficient confidence score.

## Input

| Parameter | Description |
|-----------|-------------|
| **Suggestion** | A Suggestion object from the `workflows` table with status `DETECTED` |
| **Alert Policy** | The corresponding `alerts/<alert_type>.md` file containing verification queries |
| **Environment Config** | Connection details from `environment_registry` table |

The Auditor receives its input synchronously from the orchestrator -- it is called
as a function, not run as a background thread.

## Output

A **VerificationReport** object stored as JSON in the `verification` column of the
`workflows` table. The report contains:

- `valid` -- Boolean indicating whether the alert is confirmed
- `confidence` -- Float between 0.0 and 1.0 indicating certainty
- `actual_metrics` -- The real values observed on the database
- `reported_metrics` -- The values extracted from the alert email
- `deviation` -- Percentage difference between actual and reported
- `duplicate_check` -- Whether another active workflow exists for this resource
- `timestamp` -- When verification was performed
- `verification_queries` -- The SQL statements that were executed (for audit)

On completion, the workflow status is updated to `VERIFIED` (if valid) or
`FALSE_POSITIVE` (if not valid).

## Behavioral Rules

1. **Read-Only Access Only** -- The Auditor must **never** modify the target database.
   All connections use a read-only database user. Any SQL containing DML or DDL is
   rejected before execution.

2. **Called Synchronously** -- The orchestrator calls the Auditor as a blocking
   function call. The orchestrator waits for the result before proceeding.

3. **Per-Alert Verification** -- Each alert type has its own verification logic
   defined in `alerts/<alert_type>.md`. The Auditor loads and executes the
   appropriate verification steps.

4. **Tolerance Matching** -- Metrics are compared with a configurable tolerance
   (default: +/- 2%). An email reporting 92% and a database showing 93.5% is
   still considered a match.

5. **Confidence Scoring** -- The confidence score is calculated based on:
   - Metric match accuracy (higher match = higher confidence)
   - Alert source reliability (known monitoring tools score higher)
   - Time since alert (older alerts score lower -- the situation may have changed)

6. **Single Verification** -- Each Suggestion is verified exactly once. If
   verification fails, the workflow moves to `FALSE_POSITIVE` and is not retried
   automatically.

## Timeout

| Operation | Timeout | Action on Timeout |
|-----------|---------|-------------------|
| Full verification | **30 seconds** | Abort, mark workflow as `VERIFICATION_TIMEOUT`, escalate |
| Database connection | **10 seconds** | Fail verification, retry once, then escalate |
| Individual query | **15 seconds** | Abort query, log warning, fail verification |

## Verification Steps

For each Suggestion, the Auditor performs the following steps in order:

### Step 1: Connect to Target Database

Establish a read-only connection to the Oracle database identified in the Suggestion.
Use credentials from the `environment_registry` and `auracore.yaml` configuration.

### Step 2: Run Verification Query

Execute the verification SQL defined in the corresponding `alerts/<alert_type>.md`
policy file. Examples:

- **Tablespace Full**: `SELECT used_percent FROM dba_tablespace_usage_metrics WHERE tablespace_name = :tbs_name`
- **Archive Dest Full**: `SELECT percent_full FROM v$flash_recovery_area`
- **Temp Full**: `SELECT used_percent FROM v$temp_space_header JOIN dba_tablespace_usage_metrics ...`
- **Listener Down**: Attempt `tnsping` or query `v$instance` for connectivity
- **Archive Gap**: `SELECT * FROM v$archive_gap`

### Step 3: Compare Actual vs. Reported Metrics

Compare the values returned from the database against the values parsed from the
alert email. Apply the configured tolerance window (default 2%).

| Outcome | Action |
|---------|--------|
| Metrics match within tolerance | `valid=True`, continue |
| Metrics outside tolerance but issue still exists | `valid=True`, note deviation |
| Issue no longer exists (self-resolved) | `valid=False`, mark as `FALSE_POSITIVE` |

### Step 4: Check for Duplicate Active Workflows

Query the `workflows` table for any existing active workflow targeting the same
`database_id` and resource (e.g., same tablespace). If a duplicate exists:

- If the existing workflow is in `EXECUTING` state: mark new one as `DUPLICATE`, skip
- If the existing workflow is in `AWAITING_APPROVAL`: mark new one as `DUPLICATE`, skip
- If the existing workflow is stale (>2 hours old, stuck): allow new one to proceed

## Error Handling

- **Timeout (30s exceeded)**: Mark the workflow as `VERIFICATION_TIMEOUT`. The
  orchestrator will escalate this to a human DBA with all available context.

- **Connection failure**: Retry the connection **once** after a 2-second delay.
  If the retry also fails, mark the workflow as `VERIFICATION_FAILED` and escalate
  to a human DBA. Connection failure to a database is itself an alert-worthy event.

- **Query execution error**: Log the full error (ORA- code, message), mark the
  workflow as `VERIFICATION_FAILED`, and escalate. Do not retry query errors as
  they typically indicate a permissions or schema issue.

- **Unexpected data format**: If the query returns unexpected columns or data types,
  log at ERROR level, mark as `VERIFICATION_FAILED`, and escalate.
