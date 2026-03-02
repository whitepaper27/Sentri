---
type: workflow
name: approval_workflow
version: 1
---

# Approval Workflow

## Overview

This document defines the rules governing when, how, and from whom approval is
required before Sentri executes a database fix. The approval workflow is the
primary safety mechanism for production environments, ensuring that no automated
change reaches a live database without explicit human authorization.

## Approval Routing Rules

The approval path is determined by two factors: the **environment** of the target
database and the **risk level** of the proposed change.

### Environment-Based Routing

| Environment | Autonomy Level | Default Behavior |
|-------------|---------------|------------------|
| **DEV** | AUTONOMOUS | Auto-approve. No human approval needed. Execute immediately after verification. Notify DBA channel after execution. |
| **UAT** | SUPERVISED | Auto-approve for low-risk changes. Require single DBA approval for high-risk changes. |
| **PROD** | ADVISORY | Always require approval. No exceptions. Critical schemas require dual approval. |

### Risk-Based Routing (UAT only)

In UAT environments, the risk level determines whether approval is needed:

| Risk Level | Criteria | Approval |
|------------|----------|----------|
| **LOW** | Non-critical schema, no downtime, < 60s, no data dictionary changes | Auto-execute |
| **MEDIUM** | Non-critical schema but involves data dictionary or > 60s duration | Single DBA approval |
| **HIGH** | Critical schema, or requires downtime, or RAC coordination | Single DBA approval |

### Critical Schema Rules (PROD only)

Changes affecting critical schemas in PROD require **dual approval** from both
a DBA and the business owner of that schema:

| Schema | Approver 1 (Technical) | Approver 2 (Business) |
|--------|----------------------|---------------------|
| FINANCE | Senior DBA | CFO or Finance Director |
| HR | Senior DBA | CHRO or HR Director |
| CORE_APP | Senior DBA | Engineering Lead |
| PAYROLL | Senior DBA | CFO or Finance Director |
| AUDIT | Senior DBA | Compliance Officer |

Non-critical PROD schemas require single approval from the assigned Senior DBA.

### Routing Decision Tree

```
START
  |
  v
Is environment = DEV?
  YES -> Auto-approve -> Execute -> Notify -> END
  NO  -> Continue
  |
  v
Is environment = UAT?
  YES -> Is risk LOW?
           YES -> Auto-approve -> Execute -> Notify -> END
           NO  -> Request single DBA approval -> Wait -> Execute -> END
  NO  -> Continue
  |
  v
Is environment = PROD?
  YES -> Does change affect a critical schema?
           YES -> Request dual approval (DBA + Business Owner) -> Wait -> Execute -> END
           NO  -> Request single DBA approval -> Wait -> Execute -> END
```

## Approval Channels

Approval requests are delivered through multiple channels simultaneously to
maximize the chance of timely response.

### Slack (Primary Channel)

- **Destination**: `#dba-ops` channel (configurable per environment)
- **Format**: Rich message with inline approve/deny buttons
- **Callback**: Webhook endpoint processes button clicks
- **Threading**: Follow-up messages (approval, execution result) posted as replies
- **Timeout Warning**: A reminder is posted at 45 minutes if no response

Example Slack message:

```
[APPROVAL REQUIRED] Sentri - Tablespace Fix
Database: PROD-DB-07
Issue: Tablespace USERS at 92% capacity
Risk: LOW | Downtime: NONE | Est. Time: ~12s

[Approve]  [Deny]  [View Details]
```

### Email (Fallback Channel)

- **To**: Assigned DBA (from environment config `dba_owner`)
- **Subject**: `[URGENT] Sentri approval needed: {alert_type} on {database_id}`
- **Body**: Full approval package (see below)
- **Reply Actions**: Approve by replying with "APPROVED", deny with "DENIED"
- **Sent**: Simultaneously with Slack notification

### JIRA (Tracking)

- **Project**: DBA-OPS (configurable)
- **Issue Type**: Task
- **Priority**: Mapped from risk level (LOW->Medium, MEDIUM->High, HIGH->Urgent)
- **Auto-Created**: When approval is requested
- **Auto-Updated**: When approved, executed, or completed
- **Auto-Closed**: When workflow reaches terminal state
- **Linked**: Workflow ID is stored in JIRA and JIRA ticket number is stored in workflow

## Approval Package Contents

Every approval request includes a complete context package so the approver can
make an informed decision without needing to investigate independently.

```markdown
=== SENTRI APPROVAL REQUEST ===

Database:       PROD-DB-07 (PRODDB)
Environment:    PROD
Issue:          Tablespace USERS at 92% capacity
Alert Type:     tablespace_full
Detected:       2026-02-12 14:15:00 UTC
Workflow ID:    wf-20260212-001

--- Verification (Agent 2) ---
  - Tablespace confirmed at 92.3% (reported: 92%)
  - Disk space available: 45 GB free on ASM disk group DATA
  - No duplicate active workflows for this resource
  - Verification confidence: 98%

--- Proposed Action ---
  SQL: ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G AUTOEXTEND ON;
  Risk Level:     LOW
  Downtime:       NONE (online operation)
  Est. Duration:  ~12 seconds
  Affects Schema: None (tablespace-level operation)

--- Rollback Plan ---
  SQL: ALTER TABLESPACE USERS DROP DATAFILE '/u01/oradata/PRODDB/users_0047.dbf';
  Rollback Risk:  LOW
  Auto-Rollback:  Yes (triggered on validation failure)

--- Historical Context ---
  Similar past cases: 847 executions across all environments
  Success rate:        94.2%
  Avg. duration:       14 seconds
  Last execution:      2026-02-10 on PROD-DB-07 (SUCCESS)

--- Approver Action ---
  [Approve]    [Deny]    [View Full Details]
```

## Timeout Behavior

Approval requests have a finite lifetime. If no response is received within the
timeout period, the request is escalated.

### Timeout Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| **Approval Timeout** | 60 minutes | Time to wait for initial approval |
| **Reminder At** | 45 minutes | Send a reminder notification |
| **Escalation Timeout** | 90 minutes | Time before second-level escalation |

### Timeout Sequence

```
T+0m   - Approval request sent (Slack, Email, JIRA)
T+45m  - Reminder sent: "Approval still pending for {workflow_id}"
T+60m  - Timeout reached:
           - Escalate to DBA Manager
           - Update JIRA priority to Urgent
           - If severity = CRITICAL:
               - Send PagerDuty alert
               - Post to #dba-escalations channel
T+90m  - Second timeout reached:
           - Mark workflow as TIMEOUT
           - Require manual intervention
           - Page senior on-call DBA
T+120m - Final escalation:
           - Notify VP of Engineering
           - Workflow remains in TIMEOUT until manually resolved
```

### Timeout Exceptions

- **Self-resolving issues**: If the Auditor re-verifies and the issue has resolved
  itself (e.g., space freed by log rotation), the approval request is automatically
  cancelled and the workflow is marked as `SELF_RESOLVED`.

- **Worsening conditions**: If the issue severity increases during the approval
  wait (e.g., tablespace goes from 92% to 97%), a new urgent notification is
  sent alongside the existing approval request.
