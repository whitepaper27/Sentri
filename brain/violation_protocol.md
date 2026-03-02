---
type: core_policy
name: violation_protocol
version: 1
---

# Violation Protocol

This document defines the types of policy violations that Sentri can detect, the automated response actions for each type, the notification chain, and the circuit breaker mechanism that halts all automated execution when repeated failures indicate a systemic problem.

## Violation Types

### unauthorized_access

An agent attempted to perform an operation outside its authority as defined in the Agent Authority Matrix (see global_policy.md).

**Examples**:
- Agent 1 (Scout) attempted to query a database.
- Agent 2 (Auditor) attempted to execute a DML statement.
- Agent 4 (Executor) attempted to access a database in an environment it was not assigned to.
- Any agent attempted to access credentials for a database not associated with the current workflow.

**Severity**: CRITICAL

**Detection method**: Pre-execution authorization check in the orchestrator. All database connection requests are routed through a connection broker that validates the requesting agent against the authority matrix.

### failed_rollback

Agent 4 (Executor) attempted to roll back a failed operation, but the rollback itself failed. The database may be in an inconsistent state.

**Examples**:
- The rollback SQL returned an error (e.g., datafile cannot be dropped because it contains data).
- The rollback SQL succeeded but post-rollback validation indicates the database is not in the expected state.
- The rollback SQL timed out.

**Severity**: CRITICAL

**Detection method**: Agent 4 reports rollback failure in the ExecutionResult. Post-rollback validation query returns unexpected results.

### timeout_breach

An operation exceeded its configured timeout and had to be forcibly terminated.

**Examples**:
- Agent 2 verification exceeded 30 seconds.
- Agent 4 execution exceeded 5 minutes.
- An approval request exceeded its configured timeout window.
- A database connection attempt exceeded 10 seconds.

**Severity**: WARNING (single occurrence), HIGH (repeated occurrences on same database)

**Detection method**: Orchestrator timeout monitoring for all timed operations.

### duplicate_execution

The system attempted to execute a fix for an issue that is already being handled by another active workflow, or that has already been resolved.

**Examples**:
- Two workflows targeting the same tablespace on the same database.
- A workflow attempting to fix an issue that another workflow already resolved (detected via post-verification).
- A race condition in the orchestrator created two workflows from the same alert email.

**Severity**: HIGH

**Detection method**: Lock acquisition check (if lock already held, another workflow is active). Duplicate detection query in Agent 2 verification (check for active workflows with same database_id and alert_type).

### policy_override_expired

A configured autonomy override has expired but was not removed or renewed.

**Examples**:
- A per-database override with an expiration date in the past.
- A per-alert-type override that was set temporarily and never cleaned up.

**Severity**: LOW

**Detection method**: Periodic policy validation scan (runs every hour).

### invalid_state_transition

An agent or the orchestrator attempted a state transition that is not permitted by the state machine definition.

**Examples**:
- Attempting to move a workflow from DETECTED directly to EXECUTING.
- Attempting to modify a workflow in a terminal state (COMPLETED, ESCALATED).
- Attempting to approve a workflow that is not in AWAITING_APPROVAL state.

**Severity**: HIGH

**Detection method**: State machine enforcement in the orchestrator. All state transitions pass through a validation function that checks the transition table.

## Response Actions

### unauthorized_access Response

1. **Block**: Immediately deny the operation. The agent's request is rejected.
2. **Log**: Write a CRITICAL-level violation record to the audit log with full context (requesting agent, target resource, attempted operation).
3. **Alert**: Send immediate notification to DBA On-Call and DBA Manager via Slack and email.
4. **Quarantine**: If the same agent triggers 2 unauthorized access violations within 1 hour, disable that agent until a human reviews and re-enables it.
5. **Workflow impact**: The current workflow transitions to ESCALATED.

### failed_rollback Response

1. **Halt**: Stop all automated operations on the affected database immediately.
2. **Log**: Write a CRITICAL-level violation record with the original action SQL, rollback SQL, error details, and current database state.
3. **Alert**: Send immediate PagerDuty alert to DBA On-Call. This is the highest priority notification.
4. **Preserve evidence**: Capture a snapshot of the relevant Oracle views (V$SESSION, DBA_TABLESPACE_USAGE_METRICS, etc.) and store in the audit log as evidence.
5. **Workflow impact**: The workflow transitions to ESCALATED with `escalation_reason=failed_rollback`.
6. **Database impact**: The affected database is placed in a "manual only" state. No automated workflows will target this database until a DBA explicitly clears the restriction.

### timeout_breach Response

1. **Terminate**: Kill the timed-out operation (cancel Agent 2 query, kill Agent 4 session).
2. **Log**: Write a WARNING-level violation record with the operation type, configured timeout, actual elapsed time, and target database.
3. **Retry**: For Agent 2 timeouts, allow one automatic retry after 60 seconds. For Agent 4 timeouts, attempt rollback and escalate.
4. **Alert**: If the same database experiences 3 timeout breaches within 1 hour, notify DBA On-Call (may indicate database performance issue).
5. **Workflow impact**: Depends on the timed-out operation. See state_machine.md for specific timeout transitions.

### duplicate_execution Response

1. **Block**: Prevent the duplicate workflow from proceeding to execution.
2. **Log**: Write a HIGH-level violation record linking both the original and duplicate workflow IDs.
3. **Deduplicate**: Close the newer workflow with status DENIED and reason `duplicate_detected`. The original workflow continues.
4. **Alert**: If duplicates are occurring frequently (more than 5 in 24 hours), notify DBA On-Call as it may indicate an email delivery issue or parsing bug.
5. **Workflow impact**: The duplicate workflow is closed. The original workflow is unaffected.

### policy_override_expired Response

1. **Revert**: The expired override is automatically removed. The environment reverts to its default autonomy level.
2. **Log**: Write an INFO-level record noting the expired override and the effective new autonomy level.
3. **Notify**: Send a Slack message to the DBA who created the override, informing them it has expired.
4. **No workflow impact**: Active workflows are not affected. Only future workflows will use the reverted autonomy level.

### invalid_state_transition Response

1. **Reject**: The state transition is not applied. The workflow remains in its current state.
2. **Log**: Write a HIGH-level violation record with the attempted transition (from state, to state), the requesting component, and the workflow context.
3. **Alert**: Notify DBA On-Call if the violation indicates a potential software bug.
4. **Workflow impact**: The workflow remains in its current state. The orchestrator must determine the correct next action.

## Notification Chain

Violation notifications are routed based on severity:

| Severity | Primary Channel | Secondary Channel | Recipients | Response Time |
|----------|----------------|-------------------|------------|---------------|
| CRITICAL | PagerDuty | Slack #dba-critical + Email | DBA On-Call + DBA Manager | 5 minutes |
| HIGH | Slack #dba-ops | Email | DBA On-Call | 15 minutes |
| WARNING | Slack #dba-ops | None | DBA Team | 1 hour |
| LOW | Slack #dba-info | None | DBA Team | Next business day |

### Notification Content

Every violation notification must include:

- **Violation type** and severity
- **Workflow ID** (if applicable)
- **Database** affected
- **Timestamp** of the violation
- **Description** of what happened
- **Impact** assessment (what is at risk)
- **Recommended action** for the human responder
- **Link** to the full audit trail

### Notification Deduplication

To prevent notification fatigue:

- The same violation type for the same database will only generate one notification per 15-minute window.
- A summary count is appended if multiple occurrences happen within the window (e.g., "timeout_breach on PROD-DB-07: 4 occurrences in the last 15 minutes").

## Circuit Breaker

The circuit breaker is a safety mechanism that halts all automated execution when repeated failures indicate a systemic problem that individual workflow handling cannot address.

### Trigger Condition

The circuit breaker activates when **3 or more consecutive execution failures occur within a 1-hour rolling window**, across any combination of databases and alert types.

A "consecutive execution failure" is defined as:
- A workflow that transitions from EXECUTING to FAILED.
- A workflow that transitions from FAILED to ROLLED_BACK (the rollback succeeded, but the original execution failed).
- A workflow that transitions from FAILED to ESCALATED (rollback also failed).

Workflows that fail during verification (VERIFICATION_FAILED) do not count toward the circuit breaker, as these represent false positives rather than execution problems.

### Circuit Breaker States

**CLOSED** (normal operation): All workflows proceed as normal. The failure counter is maintained but does not block execution.

**OPEN** (halted): No automated execution is permitted. Agent 4 will refuse all execution requests. Workflows that reach the EXECUTING stage will instead transition to ESCALATED with reason `circuit_breaker_open`. Verification (Agent 2) continues to operate so that alerts are still triaged.

**HALF-OPEN** (testing): After a human acknowledges the circuit breaker and authorizes a test, a single workflow is allowed to execute. If it succeeds, the circuit breaker returns to CLOSED. If it fails, the circuit breaker returns to OPEN.

### Reset Procedure

1. A DBA must investigate the root cause of the failures.
2. The DBA runs `sentri circuit-breaker reset` with a reason message.
3. The system enters HALF-OPEN state.
4. The next eligible workflow executes as a test.
5. If successful, normal operation resumes. If not, the circuit breaker re-opens and requires another human review.

### Circuit Breaker Logging

All circuit breaker state changes are logged to the audit log:
- Timestamp of activation/deactivation.
- The 3 (or more) workflow IDs that triggered the breaker.
- The DBA who reset the breaker and their stated reason.
- The test workflow ID and its outcome.
