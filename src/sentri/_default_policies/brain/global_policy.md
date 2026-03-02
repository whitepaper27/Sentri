---
type: core_policy
name: global_policy
version: 1
---

# Global Policy

This document defines the foundational policies, principles, and authority boundaries for the Sentri autonomous DBA agent system. All agents, workflows, and operational decisions must comply with the rules specified here. Any conflict between agent-level policies and this global policy must be resolved in favor of this document.

## Purpose

Sentri is an autonomous DBA agent system designed for Level 3 (L3) database operations. It monitors database alert channels, verifies issues in real time, and executes corrective actions with appropriate levels of human oversight.

The system targets five deterministic alert categories in its initial scope:

- Tablespace capacity alerts
- Archive destination full alerts
- Temporary tablespace exhaustion
- Listener availability failures
- Archive log gap detection

Sentri operates across three environment tiers (DEV, UAT, PROD) with graduated autonomy. In lower environments it resolves issues without human intervention. In production it enforces approval gates, dual-authorization for critical schemas, and complete audit trails for every action taken.

## Core Principles

### Safety First

Every operation must be reversible. No agent may execute a change without a validated rollback plan. If a rollback plan cannot be generated or verified, the workflow must halt and escalate to a human operator.

### Always Have Rollback

Every execution plan must include:

- A forward action (the fix)
- A rollback action (the undo)
- A post-execution validation query (confirm the fix worked)
- A rollback validation query (confirm the undo worked, if triggered)

Rollback plans are stored in the workflow record before execution begins. An execution without a persisted rollback plan is a policy violation.

### Audit Everything

Every state transition, every database query, every approval decision, and every execution outcome must be recorded in the immutable audit log. There are no exceptions. Silent failures are not permitted; if an action fails, the failure must be logged with full context including error messages, stack traces, and the state of the workflow at the time of failure.

### Least Privilege

Each agent operates with the minimum permissions required for its function:

- Agents that read data must not have write access.
- Agents that write data must not have DDL access beyond their scope.
- No agent may access credentials for environments outside its current workflow.
- Database connections must use dedicated service accounts with role-based access.

## Safety Rules

### Prohibited Actions

The following actions are unconditionally prohibited:

1. **DROP operations in PROD** without dual approval (DBA + business owner).
2. **Modification of system tablespaces** (SYSTEM, SYSAUX, UNDO) by any automated agent. These require manual DBA intervention.
3. **Execution without prior verification**. Agent 4 (Executor) must never receive a workflow that has not passed Agent 2 (Auditor) verification.
4. **Concurrent modifications** to the same database object. The locking system must prevent this; if a lock cannot be acquired, the workflow must wait or escalate.
5. **Credential exposure** in logs, audit records, or notification messages. Connection strings and passwords must be redacted in all output.

### Mandatory Preconditions

Before any execution, the following must be true:

- The target database is reachable (connection test passed within the last 60 seconds).
- The alert has been verified as genuine by Agent 2 with a confidence score >= 0.85.
- A resource lock has been acquired for the target object.
- The rollback plan has been persisted to the workflow record.
- The environment's autonomy level has been checked and any required approvals obtained.

### Execution Boundaries

- Maximum execution time per action: 5 minutes. If exceeded, the action is terminated and the workflow is escalated.
- Maximum retry count per workflow: 2 attempts. After the second failure, the workflow enters ESCALATED state.
- Maximum concurrent workflows per database: 3. Additional workflows are queued.

## Agent Authority Matrix

| Agent | Role | Read Email | Read DB | Write DB | Read Docs | Read Audit |
|-------|------|-----------|---------|----------|-----------|------------|
| Agent 1 - Scout | Email Parser | YES | NO | NO | NO | NO |
| Agent 2 - Auditor | Verifier | NO | YES (read-only) | NO | NO | YES |
| Agent 3 - Researcher | Doc Search | NO | NO | NO | YES | YES |
| Agent 4 - Executor | Safe Runner | NO | YES | YES (with safeguards) | NO | NO |
| Agent 5 - Analyst | Learning | NO | YES (read-only) | NO | NO | YES |

### Agent-Specific Constraints

- **Scout (Agent 1)**: May only connect to the configured IMAP server. Must mark emails as read after processing. Must not delete emails. Must not send emails.
- **Auditor (Agent 2)**: Database connections must use a read-only Oracle user (e.g., `SENTRI_READONLY`). Query timeout is 30 seconds. Must not execute any DML or DDL statements.
- **Researcher (Agent 3)**: Stub in current version. When implemented, must only access approved documentation sources. Must not make external API calls beyond configured endpoints.
- **Executor (Agent 4)**: Must use a privileged Oracle user (e.g., `SENTRI_EXECUTOR`). Must acquire locks before execution. Must validate rollback plan existence before proceeding. Must release locks after completion regardless of outcome.
- **Analyst (Agent 5)**: Stub in current version. When implemented, must only read from audit_log and workflow tables. Must not modify any data.

## Escalation Chain

When an issue cannot be resolved autonomously or a policy violation occurs, the following escalation chain applies:

| Level | Recipient | Trigger | Response Time |
|-------|-----------|---------|---------------|
| L1 | Sentri Agent System | Automated detection and resolution | Immediate |
| L2 | DBA On-Call | Agent failure, approval timeout, low-confidence verification | 15 minutes |
| L3 | DBA Manager | Repeated failures (3+ in 1 hour), critical schema involvement | 30 minutes |
| L4 | VP Infrastructure | System-wide outage, data integrity concern, security incident | 1 hour |

### Escalation Rules

- Each escalation must include: workflow ID, alert summary, actions attempted, failure reason, and recommended next steps.
- Escalation notifications are sent via Slack (primary) and email (fallback).
- An escalation does not cancel the workflow. The workflow enters ESCALATED state and awaits human intervention.
- Once a human resolves an escalated workflow, they must update the workflow status and provide a resolution note for the audit trail.
