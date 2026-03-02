---
type: environment
database_id: UAT-DB-03
environment: UAT
autonomy_level: SUPERVISED
---

# UAT-DB-03: User Acceptance Testing Database

## Database Details

| Property | Value |
|----------|-------|
| **Database Name** | UATDB |
| **Database ID** | UAT-DB-03 |
| **Oracle Version** | 19c |
| **Architecture** | CDB (Container Database) |
| **Environment** | UAT |
| **Connection** | `oracle://sentri_agent@uat-db-03:1521/UATDB` |

## Schema Information

| Property | Value |
|----------|-------|
| **Critical Schemas** | `["FINANCE_UAT", "HR_UAT"]` |
| **Protected Schemas** | `["PAYROLL_UAT", "AUDIT_UAT"]` |
| **Total Schemas** | ~40 (UAT copies of production schemas) |

## Ownership

| Role | Assignee |
|------|----------|
| **Business Owner** | QA Team |
| **DBA Owner** | DBA Team |
| **On-Call Rotation** | Standard DBA on-call |

## Autonomy Configuration

| Setting | Value |
|---------|-------|
| **Autonomy Level** | SUPERVISED |
| **Low-Risk Changes** | Auto-execute (e.g., tablespace extension, archive cleanup) |
| **High-Risk Changes** | Require DBA approval (e.g., changes affecting critical schemas) |
| **Notification** | Pre-execution for high-risk, post-execution for low-risk |
| **Max Auto-Executions** | 3 per hour (safety throttle) |

## Risk Classification

Changes are classified as high-risk or low-risk based on these criteria:

| Criteria | Low-Risk | High-Risk |
|----------|----------|-----------|
| Affects critical schema | No | Yes |
| Requires downtime | No | Yes |
| Modifies data dictionary | No | Yes |
| Involves RAC coordination | No | Yes |
| Estimated duration > 60s | No | Yes |

## Operational Notes

- **Supervised mode**: Low-risk fixes are auto-executed after verification.
  High-risk fixes require explicit DBA approval before execution.

- **Critical schemas**: Changes affecting `FINANCE_UAT` or `HR_UAT` schemas
  always require approval regardless of risk classification. These schemas
  mirror production data structures and are used for compliance testing.

- **CDB architecture**: This is a Container Database (CDB) with multiple
  Pluggable Databases (PDBs). Sentri must connect to the appropriate PDB
  for each operation. The connection string routes to the CDB root by default;
  PDB-specific connections use service names.

- **QA dependency**: UAT is actively used by QA for release validation.
  Coordinate with the QA calendar before scheduling non-urgent fixes during
  business hours (08:00-18:00 UTC, Mon-Fri).

- **Refresh schedule**: Database is refreshed from PROD on the first Saturday
  of each month at 02:00 UTC. Active workflows are cancelled before refresh.

- **Maintenance window**: Preferred maintenance window is Saturday 02:00-06:00
  UTC, but urgent fixes can be applied anytime with appropriate approval.
