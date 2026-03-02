---
type: environment
database_id: DEV-DB-01
environment: DEV
autonomy_level: AUTONOMOUS
---

# DEV-DB-01: Development Database

## Database Details

| Property | Value |
|----------|-------|
| **Database Name** | DEVDB |
| **Database ID** | DEV-DB-01 |
| **Oracle Version** | 19c |
| **Architecture** | STANDALONE |
| **Environment** | DEV |
| **Connection** | `oracle://sentri_agent@dev-db-01:1521/DEVDB` |

## Schema Information

| Property | Value |
|----------|-------|
| **Critical Schemas** | none |
| **Protected Schemas** | none |
| **Total Schemas** | ~25 (development copies) |

## Ownership

| Role | Assignee |
|------|----------|
| **Business Owner** | Development Team |
| **DBA Owner** | DBA Team |
| **On-Call Rotation** | Standard DBA on-call |

## Autonomy Configuration

| Setting | Value |
|---------|-------|
| **Autonomy Level** | AUTONOMOUS |
| **Approval Required** | No -- all verified fixes auto-execute |
| **Notification** | Post-execution notification to `#dba-dev` channel |
| **Max Auto-Executions** | 5 per hour (safety throttle) |

## Operational Notes

- **Full autonomous mode**: All fixes are auto-executed after passing verification
  (Agent 2). No human approval is required for any alert type or risk level.

- **Used for testing**: This database is used by the development team for feature
  testing and integration. Downtime and brief performance impacts are acceptable.

- **Refresh schedule**: Database is refreshed from PROD snapshot every Sunday at
  00:00 UTC. Workflows in progress at refresh time will be marked as `CANCELLED`.

- **No maintenance window restrictions**: Fixes can be applied at any time of day.

- **Monitoring**: Standard OEM monitoring. Alert thresholds are intentionally
  lower than PROD to catch issues earlier in the development cycle.

- **Rollback policy**: Rollbacks are still required for all executions, even in
  DEV. This ensures the rollback mechanism is continuously tested.
