---
type: workflow
name: dev_auto_execution
version: 1
---

# DEV Auto-Execution Workflow

## Overview

In DEV environments (autonomy level: AUTONOMOUS), all verified alerts are automatically executed without human approval. This enables zero-touch resolution for development databases.

## Rules

- All verified alerts auto-execute in DEV
- No approval required for any risk level
- Verification must still pass before execution
- Rollback plan must still exist
- Maximum 5 auto-executions per hour per database (rate limit)

## Pre-Execution Checks

- Alert verification passed (Agent 2 confirmed)
- Rollback SQL is defined in alert policy
- No active lock on the target resource
- Rate limit not exceeded

## Execution Flow

1. Workflow reaches VERIFIED status
2. Orchestrator checks environment = DEV
3. Skip approval, transition directly to EXECUTING
4. Agent 4 acquires lock, executes fix, validates result
5. Transition to COMPLETED or ROLLED_BACK

## Post-Execution

- Send notification to DBA Slack channel with results
- Log audit record (even in DEV, for tracking)
- If execution fails, mark as FAILED (do not auto-retry)

## Guardrails

- Rate limit: max 5 executions per database per hour
- If rate limit exceeded, transition to AWAITING_APPROVAL
- Circuit breaker: 3 consecutive failures on same database halts auto-execution
- After circuit breaker trips, require manual intervention to re-enable

## Notifications

- Success: Post to #dba-dev channel with summary
- Failure: Post to #dba-dev channel AND email DBA on-call
- Rate limit hit: Post warning to #dba-ops
