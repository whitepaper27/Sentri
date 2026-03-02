---
type: core_policy
name: state_machine
version: 1
---

# State Machine

This document defines every possible state in a Sentri workflow, the valid transitions between states, terminal conditions, and timeout behaviors. The orchestrator must enforce these rules strictly. Any attempt to perform an invalid state transition is a policy violation and must be logged and rejected.

## All States

### DETECTED

The initial state. Agent 1 (Scout) has parsed an incoming alert email and created a workflow record. The alert has been pattern-matched and metadata has been extracted, but no verification has been performed.

**Entry condition**: Agent 1 successfully parses an alert email and inserts a workflow record.
**Responsible agent**: Agent 1 (Scout).
**Next action**: Orchestrator picks up the workflow and initiates verification.

### VERIFYING

Agent 2 (Auditor) is actively querying the target database to confirm the alert is genuine. The workflow is locked to prevent duplicate verification attempts.

**Entry condition**: Orchestrator assigns the workflow to Agent 2.
**Responsible agent**: Agent 2 (Auditor).
**Timeout**: 30 seconds. If Agent 2 does not return within this window, the workflow transitions to VERIFICATION_FAILED.

### VERIFIED

Agent 2 has confirmed the alert is genuine. The verification report includes the actual metric values from the database, a confidence score, and a duplicate check result.

**Entry condition**: Agent 2 returns a VerificationReport with `valid=True` and `confidence >= 0.85`.
**Next action**: Orchestrator checks autonomy level and either auto-executes or requests approval.

### VERIFICATION_FAILED

Agent 2 could not confirm the alert. This may mean the alert is a false positive, the database is unreachable, the verification query timed out, or the reported metrics do not match actual conditions.

**Entry condition**: Agent 2 returns `valid=False`, confidence is below threshold, or verification times out.
**Next action**: Log the failure reason. If the failure is due to connectivity, retry once after 60 seconds. If the failure is due to metric mismatch (false positive), close the workflow. If retries are exhausted, escalate.

### AWAITING_APPROVAL

The workflow requires human approval before execution can proceed. An approval request has been sent via the configured channels (Slack, email, JIRA).

**Entry condition**: Autonomy level requires approval for this environment and alert type combination.
**Timeout**: Configurable per environment (default: 1 hour for PROD, 30 minutes for UAT).
**Next action**: Wait for approval response. Poll the workflow record every 10 seconds for status changes.

### APPROVED

A human operator has reviewed the execution plan and granted approval. The approver's identity and timestamp are recorded in the workflow.

**Entry condition**: An authorized approver updates the workflow status to APPROVED.
**Validation**: The approver must be in the authorized approvers list for the target environment. For critical schemas in PROD, dual approval is required (two distinct approvers).
**Next action**: Orchestrator initiates execution via Agent 4.

### DENIED

A human operator has reviewed the execution plan and rejected it. The workflow will not proceed to execution.

**Entry condition**: An authorized approver updates the workflow status to DENIED.
**Next action**: Log the denial reason. Close the workflow. Notify the DBA team of the denial for awareness. The underlying issue remains unresolved and may trigger a new alert if conditions persist.

### EXECUTING

Agent 4 (Executor) is actively running the fix on the target database. A resource lock is held for the target object. The rollback plan is loaded and ready.

**Entry condition**: Agent 4 acquires the resource lock and begins execution.
**Responsible agent**: Agent 4 (Executor).
**Timeout**: 5 minutes. If execution exceeds this window, the action is terminated, rollback is attempted, and the workflow transitions to FAILED.

### COMPLETED

The fix has been executed successfully. Post-execution validation confirms the issue is resolved. The resource lock has been released. An immutable audit record has been written.

**Entry condition**: Agent 4 reports successful execution and post-validation confirms the fix.
**Terminal**: Yes. No further transitions are possible from this state.
**Post-completion**: Send success notification. Update any external tracking systems (JIRA, etc.).

### FAILED

Execution was attempted but did not succeed. This may be due to a SQL error, a post-validation failure (the fix did not actually resolve the issue), or an execution timeout.

**Entry condition**: Agent 4 reports an error during execution or post-validation fails.
**Next action**: If a rollback is possible, attempt it automatically and transition to ROLLED_BACK. If rollback fails, escalate immediately. Log all error details.

### ROLLED_BACK

A failed execution has been successfully reversed. The database is back to its pre-execution state. Post-rollback validation confirms the rollback was effective.

**Entry condition**: Agent 4 successfully executes the rollback plan and post-rollback validation passes.
**Next action**: Log the rollback. Escalate to DBA on-call for root cause analysis. The workflow may be retried after human review.

### TIMEOUT

An approval request or other timed operation has exceeded its allowed window without a response.

**Entry condition**: The configured timeout period has elapsed without the expected response (approval, execution completion, etc.).
**Next action**: Escalate per the escalation chain. For approval timeouts, send a reminder at 75% of the timeout window, then escalate at 100%.

### ESCALATED

The workflow has been handed off to a human operator for manual resolution. This is a terminal state from the perspective of automated processing.

**Entry condition**: Any condition that exceeds the system's ability to resolve autonomously: repeated failures, rollback failures, timeout after escalation, policy violations.
**Terminal**: Yes. Only a human operator can update the workflow from this state (to COMPLETED or a custom resolution status).
**Required fields**: `escalation_reason`, `escalated_to`, `escalated_at`.

## Valid Transitions

| From | To | Trigger | Agent/Actor |
|------|----|---------|-------------|
| DETECTED | VERIFYING | Orchestrator assigns verification | Orchestrator |
| VERIFYING | VERIFIED | Verification succeeds (confidence >= 0.85) | Agent 2 |
| VERIFYING | VERIFICATION_FAILED | Verification fails or times out | Agent 2 / Orchestrator |
| VERIFIED | AWAITING_APPROVAL | Autonomy level requires approval | Orchestrator |
| VERIFIED | EXECUTING | Autonomy level allows auto-execution | Orchestrator |
| VERIFICATION_FAILED | VERIFYING | Retry after transient failure (max 1 retry) | Orchestrator |
| VERIFICATION_FAILED | ESCALATED | Retries exhausted or non-transient failure | Orchestrator |
| AWAITING_APPROVAL | APPROVED | Human approves execution plan | Human Operator |
| AWAITING_APPROVAL | DENIED | Human denies execution plan | Human Operator |
| AWAITING_APPROVAL | TIMEOUT | Approval window expires | Orchestrator |
| APPROVED | EXECUTING | Orchestrator initiates execution | Orchestrator |
| DENIED | (terminal) | Workflow closed | N/A |
| EXECUTING | COMPLETED | Execution succeeds and validation passes | Agent 4 |
| EXECUTING | FAILED | Execution error or validation failure | Agent 4 |
| FAILED | ROLLED_BACK | Automatic rollback succeeds | Agent 4 |
| FAILED | ESCALATED | Rollback fails or is not possible | Agent 4 / Orchestrator |
| ROLLED_BACK | ESCALATED | Requires human review after rollback | Orchestrator |
| TIMEOUT | ESCALATED | Escalation after timeout | Orchestrator |

### Invalid Transitions

Any transition not listed above is invalid. Examples of explicitly prohibited transitions:

- COMPLETED to any state (terminal, immutable)
- ESCALATED to any state via automated process (only humans can resolve)
- DETECTED directly to EXECUTING (verification must occur first)
- DENIED directly to EXECUTING (approval was refused)
- Any state directly to COMPLETED without passing through EXECUTING

## Terminal States

The following states are terminal. Once a workflow enters a terminal state, no further automated transitions occur.

| State | Resolution | Requires Human |
|-------|-----------|----------------|
| COMPLETED | Issue resolved successfully | No |
| DENIED | Human chose not to proceed | No (already decided) |
| ESCALATED | Beyond automated resolution | Yes |

### Terminal State Immutability

Once a workflow reaches a terminal state:

- The workflow record must not be modified by any agent.
- The audit log entries for this workflow must not be modified.
- A human may add resolution notes to an ESCALATED workflow, but the original data must remain intact.

## Timeout Behaviors

### Verification Timeout

- **Duration**: 30 seconds
- **Trigger**: Agent 2 does not return a VerificationReport within the window.
- **Action**: Transition to VERIFICATION_FAILED with reason `verification_timeout`.
- **Retry**: One automatic retry after 60 seconds. If the retry also times out, escalate.

### Approval Timeout

- **Duration**: Configurable. Defaults: PROD = 60 minutes, UAT = 30 minutes.
- **Warning**: At 75% of timeout (e.g., 45 minutes for PROD), send a reminder notification.
- **Action at 100%**: Transition to TIMEOUT, then immediately to ESCALATED.
- **Escalation target**: DBA Manager (bypasses on-call).

### Execution Timeout

- **Duration**: 5 minutes
- **Trigger**: Agent 4 does not complete execution within the window.
- **Action**: Terminate the running operation. Attempt rollback. If rollback succeeds, transition to ROLLED_BACK. If rollback fails, transition to ESCALATED.
- **Kill mechanism**: The orchestrator cancels the Agent 4 execution thread and issues a session kill on the Oracle connection if necessary.

### Lock Timeout

- **Duration**: 30 seconds (see locking_rules.md)
- **Trigger**: Agent 4 cannot acquire the resource lock within the window.
- **Action**: The workflow remains in its current state. Retry lock acquisition after 10 seconds, up to 3 attempts. If lock cannot be acquired, transition to ESCALATED with reason `lock_acquisition_failed`.
