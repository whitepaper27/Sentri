---
type: environment
database_id: PROD-DB-07
environment: PROD
autonomy_level: ADVISORY
---

# PROD-DB-07: Production Database

## Database Details

| Property | Value |
|----------|-------|
| **Database Name** | PRODDB |
| **Database ID** | PROD-DB-07 |
| **Oracle Version** | 19c |
| **Architecture** | RAC (2-node Real Application Cluster) |
| **Environment** | PROD |
| **Connection** | `oracle://sentri_agent@prod-scan:1521/PRODDB` |

## Schema Information

| Property | Value |
|----------|-------|
| **Critical Schemas** | `["FINANCE", "HR", "CORE_APP"]` |
| **Protected Schemas** | `["PAYROLL", "AUDIT", "COMPLIANCE", "CUSTOMER_DATA"]` |
| **Total Schemas** | ~85 (production workloads) |

## Ownership

| Role | Assignee |
|------|----------|
| **Business Owner** | CTO Office |
| **DBA Owner** | Senior DBA Team |
| **On-Call Rotation** | Senior DBA on-call (24/7) |
| **Escalation Contact** | DBA Manager |

## Autonomy Configuration

| Setting | Value |
|---------|-------|
| **Autonomy Level** | ADVISORY |
| **Approval Required** | Yes -- ALL changes require explicit approval |
| **Critical Schema Approval** | Dual approval required (DBA + Business Owner) |
| **Notification** | Pre-execution approval request via Slack, Email, and JIRA |
| **Approval Timeout** | 1 hour (escalates to DBA Manager) |

## Dual Approval Matrix

Changes affecting critical schemas require approval from **two** parties:

| Schema | Approver 1 | Approver 2 |
|--------|-----------|-----------|
| **FINANCE** | Senior DBA | CFO or Finance Director |
| **HR** | Senior DBA | CHRO or HR Director |
| **CORE_APP** | Senior DBA | Engineering Lead |
| **PAYROLL** | Senior DBA | CFO or Finance Director |
| **AUDIT** | Senior DBA | Compliance Officer |
| **COMPLIANCE** | Senior DBA | Compliance Officer |
| **CUSTOMER_DATA** | Senior DBA | Data Protection Officer |

Non-critical schemas require single approval from the assigned Senior DBA.

## RAC Configuration

| Property | Value |
|----------|-------|
| **Node 1** | `prod-rac-01` (Active) |
| **Node 2** | `prod-rac-02` (Active) |
| **SCAN Listener** | `prod-scan:1521` |
| **Cluster Interconnect** | 10Gbps dedicated |
| **Services** | `PRODDB_APP` (round-robin), `PRODDB_BATCH` (preferred node 2) |

When executing fixes on this RAC database, Sentri must:
- Use the SCAN listener for all connections (never connect to individual nodes)
- Verify both nodes are operational before tablespace operations
- Account for shared storage when calculating available disk space

## Operational Notes

- **Advisory only**: ALL changes require explicit human approval before execution.
  There are no exceptions. Even low-risk, routine operations (tablespace extension,
  archive cleanup) must be approved by a Senior DBA.

- **Critical schemas require dual approval**: Changes affecting FINANCE, HR, or
  CORE_APP schemas require approval from both the Senior DBA and the respective
  business owner. See the Dual Approval Matrix above.

- **Maintenance window**: Sunday 02:00-06:00 UTC. Non-urgent changes should be
  scheduled within this window. Urgent changes (e.g., tablespace at 98%+) may
  be approved for immediate execution outside the maintenance window.

- **RAC awareness**: This is a 2-node RAC cluster. Sentri must use the SCAN
  listener and be aware that tablespace operations affect shared storage visible
  to both nodes. Listener-related alerts may affect individual nodes independently.

- **Change management**: All PROD changes generate a JIRA ticket in the DBA-OPS
  project. The ticket must be referenced in the audit log. Post-execution, the
  ticket is updated with results and closed.

- **Compliance requirements**: This database is subject to SOX compliance. All
  changes must have a complete audit trail including: who approved, what was
  executed, before/after metrics, and rollback capability.

- **Backup validation**: Before any structural change (tablespace add, datafile
  resize), verify that the most recent RMAN backup completed successfully.
  Do not proceed if the last backup is older than 24 hours.

- **Monitoring**: Enhanced OEM monitoring with 5-minute collection intervals.
  PagerDuty integration for critical alerts. All metrics forwarded to the
  enterprise monitoring dashboard.
