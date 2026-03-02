---
type: core_policy
name: autonomy_levels
version: 1
---

# Autonomy Levels

This document defines the autonomy levels that govern how Sentri handles workflows across different environments. Autonomy levels determine whether a workflow can execute automatically or requires human approval before proceeding.

## Level Definitions

### AUTONOMOUS

No human approval is required. The system detects the issue, verifies it, and executes the fix entirely on its own. A notification is sent after execution completes, but the workflow does not wait for human input at any stage.

**Applicable when**: The environment is low-risk, the alert type is well-understood, and the fix has a high historical success rate.

**Post-execution behavior**: Send notification to the DBA channel with execution summary and audit link.

### SUPERVISED

Human approval is required for high-risk changes. Low-risk changes may execute automatically. The classification of high-risk vs. low-risk is determined by the Risk Matrix defined below.

**Applicable when**: The environment has moderate criticality, or the alert type involves operations that could affect application availability.

**Approval flow**: An approval request is sent via Slack and email. The workflow pauses in AWAITING_APPROVAL state until a response is received or the timeout expires.

### ADVISORY

Human approval is always required, regardless of risk level. The system detects and verifies the issue, prepares the execution plan, and presents it for review. No execution occurs without explicit approval.

**Applicable when**: The environment is production or contains critical business data.

**Approval flow**: Full approval package is generated (see workflows/approval_workflow.md). For critical schemas, dual approval is required. The workflow will not proceed without all required approvals.

## Environment Mapping

| Environment | Default Autonomy Level | Rationale |
|-------------|----------------------|-----------|
| DEV | AUTONOMOUS | Low risk. Data is synthetic or easily recreated. Developer productivity is prioritized. |
| UAT | SUPERVISED | Moderate risk. Data may mirror production. Testing cycles can be disrupted by unplanned changes. |
| PROD | ADVISORY | High risk. Business-critical data. Regulatory and compliance requirements demand human oversight. |

### Environment Detection

The environment for a given database is determined by the `environment` field in the `environment_registry` table. This value is set during initial configuration via `sentri init` and can be updated by a DBA through the configuration file or CLI.

If a database is not found in the environment registry, the workflow must halt with an error. Sentri must never assume an environment for an unregistered database.

## Override Rules

DBAs can override the default autonomy level for specific databases or alert types through the configuration system.

### Per-Database Override

A DBA may escalate or relax the autonomy level for a specific database. This is configured in the environment definition files under `environments/`.

```yaml
# Example: Override DEV database to SUPERVISED
database_id: DEV-DB-01
environment: DEV
autonomy_override: SUPERVISED
override_reason: "Contains copy of production data for migration testing"
override_approved_by: john.smith
override_expires: 2026-03-15
```

### Per-Alert-Type Override

A DBA may change the autonomy behavior for a specific alert type across all environments.

```yaml
# Example: Always require approval for listener restarts
alert_type: listener_down
autonomy_override: ADVISORY
override_reason: "Listener restarts affect all applications on the host"
override_approved_by: jane.doe
```

### Override Hierarchy

When multiple overrides apply, the most restrictive level wins:

1. Per-database override (highest priority)
2. Per-alert-type override
3. Environment default (lowest priority)

AUTONOMOUS < SUPERVISED < ADVISORY (ADVISORY is most restrictive)

### Override Expiration

All overrides must have an expiration date (maximum 90 days). Expired overrides revert to the environment default. Sentri logs a warning when an override is within 7 days of expiration.

## Risk Matrix

This matrix determines the effective behavior for each combination of alert type and environment. Values indicate whether the action executes automatically (`auto`) or requires human approval (`approval`).

| Alert Type | DEV | UAT | PROD |
|------------|-----|-----|------|
| tablespace_full | auto | auto | approval |
| archive_dest_full | auto | auto | approval |
| temp_full | auto | auto | approval |
| listener_down | auto | approval | approval |
| archive_gap | auto | approval | approval |

### Risk Classification Criteria

**Low Risk (auto-eligible)**:
- The operation is online (no downtime required).
- The operation is additive (adding space, not removing objects).
- The rollback is simple and well-tested.
- Historical success rate exceeds 90%.

**High Risk (approval required)**:
- The operation may cause brief service interruption.
- The operation involves service restarts (e.g., listener).
- The operation affects replication or data guard configurations.
- Historical success rate is below 90% or insufficient data exists.

### Future Risk Classification

When Agent 5 (Analyst) is fully implemented, risk classification will incorporate dynamic confidence scores based on historical outcomes. An alert type that consistently succeeds in UAT may be reclassified from `approval` to `auto` after accumulating sufficient evidence (minimum 50 successful executions with zero rollbacks).
