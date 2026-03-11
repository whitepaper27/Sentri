---
type: core_policy
name: execution_rules
version: 1
---

# Execution Rules

Hard safety rules enforced before any action. If a rule is violated, the workflow is blocked and escalated. DBAs edit this file to change behavior — no code changes needed.

## Environment Rules

| Environment | Behavior |
|-------------|----------|
| DEV | Auto-execute low-risk actions. Require approval for KILL_SESSION and START_LISTENER. |
| UAT | Auto-execute ADD_DATAFILE and ADD_TEMPFILE only. All other actions require approval. |
| PROD | ALL actions require DBA approval — no auto-execute, ever. |

## Action Rules

| Action Type | DEV | UAT | PROD | Notes |
|-------------|-----|-----|------|-------|
| ADD_DATAFILE | auto | auto | approval | Online operation, additive, safe rollback. |
| ADD_TEMPFILE | auto | auto | approval | Online operation, additive, safe rollback. |
| DELETE_ARCHIVES | auto | approval | approval | Destructive — deleted files cannot be recovered. |
| RESOLVE_GAP | auto | approval | approval | May trigger large network transfer (RMAN). |
| START_LISTENER | approval | approval | approval | Affects all connections on the host. Never auto-start. |
| KILL_SESSION | approval | approval | approval | Terminates active work. Never auto-kill on any environment. |
| TABLESPACE_FULL | auto | auto | approval | StorageAgent: online, additive. Maps to ADD_DATAFILE internally. |
| TEMP_FULL | auto | auto | approval | StorageAgent: online, additive. Maps to ADD_TEMPFILE internally. |
| ARCHIVE_DEST_FULL | auto | approval | approval | StorageAgent: destructive delete of old archives. |
| HIGH_UNDO_USAGE | auto | approval | approval | StorageAgent: may need parameter change or session action. |
| CPU_HIGH | approval | approval | approval | SQLTuningAgent: investigation-only, may alter sessions. |
| LONG_RUNNING_SQL | approval | approval | approval | SQLTuningAgent: investigation-only, may alter sessions. |
| SESSION_BLOCKER | approval | approval | approval | RCAAgent: investigation-only, may require session kills. |
| CHECK_FINDING | auto | auto | approval | ProactiveAgent: low-risk findings from scheduled health checks. |

## Protected Sessions — Never Kill

These Oracle sessions must never be killed by Sentri, regardless of environment or approval status. If the top offender is a protected session, escalate instead.

- SYS
- SYSTEM
- DBSNMP
- SYSBACKUP
- SYSDG
- SYSKM
- SYSRAC
- AUDSYS
- GSMADMIN_INTERNAL
- MDSYS
- CTXSYS

## Protected Schemas — Never Modify

Sentri must not execute DDL or DML that targets objects owned by these schemas:

- SYS
- SYSTEM
- OUTLN
- DBSNMP

## Protected Databases

Databases listed here have additional restrictions beyond their environment level.

| Database | Rule |
|----------|------|
| FINANCE-DB | Dual approval required (DBA + finance owner). No auto-execute ever. |
| HR-DB | Dual approval required (DBA + HR owner). No auto-execute ever. |

DBAs: add database names here to enforce stricter controls.

## Session Kill Rules

When action_type is KILL_SESSION (cpu_high, session_blocker, high_undo_usage, long_running_sql):

1. Always require approval — never auto-kill on any environment.
2. Never kill sessions from the Protected Sessions list above.
3. Never kill sessions where program contains: `oracle@`, `tnslsnr`, `pmon`, `smon`, `dbw`, `lgwr`, `ckpt`, `reco`, `mmon`, `mmnl`.
4. If the session belongs to a batch/scheduled job (program contains `DBMS_SCHEDULER` or `sqlplus`), escalate to DBA instead of killing.
5. Maximum 1 session kill per workflow. If multiple sessions need killing, escalate.
6. The SID and SERIAL# must come from live verification query — never from email text alone.

## Listener Rules

When action_type is START_LISTENER:

1. Always require approval — never auto-start on any environment.
2. In RAC environments, do NOT start SCAN listeners — they are managed by Oracle Clusterware. Escalate instead.
3. Verify listener.ora exists and is valid before starting.
4. After starting, wait 30 seconds and verify services are registered.
5. If listener was intentionally stopped (maintenance window), do not restart — escalate.

## Archive Rules

When action_type is DELETE_ARCHIVES or RESOLVE_GAP:

1. Never delete archives less than 24 hours old.
2. Verify RMAN backup exists before deleting any archive logs.
3. If Data Guard is configured, verify standby has received the logs before deletion.
4. Maximum deletion: 50GB per execution. If more space needed, escalate.

## Tablespace Rules

When action_type is ADD_DATAFILE or ADD_TEMPFILE:

1. Maximum single datafile size: 32GB.
2. Maximum auto-extend: MAXSIZE 32767M.
3. If tablespace is BIGFILE, use RESIZE instead of ADD DATAFILE.
4. If tablespace fills again within 24 hours of last fix, escalate — possible data leak or runaway process.
5. Never modify SYSTEM, SYSAUX, or UNDO tablespaces — escalate to DBA.

## Confidence Thresholds

| Confidence | Action |
|------------|--------|
| < 0.60 | Escalate to DBA. Do not execute. |
| 0.60 - 0.79 | Run pre-flight checks. Require approval regardless of environment. |
| 0.80 - 0.94 | Run pre-flight checks. Follow environment rules above. |
| >= 0.95 | Follow environment rules above. |

## Repeat Alert Rules

Repeat alerts are logged for observability but **do not block execution**.
DBA controls approval requirements via the Action Rules matrix above and
per-database autonomy in `environments/*.md`. The Circuit Breaker (below)
catches genuinely broken scenarios (repeated failures).

| Condition | Action |
|-----------|--------|
| Same alert on same DB within 6 hours | Logged as INFO. Proceed per action/environment policy. |
| Same alert 3+ times in 24 hours | Logged as INFO. Consider root cause investigation. |
| Same alert 5+ times in 7 days | Logged as INFO. Consider capacity planning review. |

### RCA Recommendation Thresholds

When the same alert fires repeatedly, Sentri includes an RCA recommendation in the
completion email telling the DBA to investigate root cause. Configurable below.

| Setting | Value | Description |
|---------|-------|-------------|
| rca_alert_count | 3 | Number of same alerts on same DB to trigger RCA recommendation |
| rca_window_hours | 24 | Time window for counting repeat alerts |

## Circuit Breaker

Blocks execution when the same database has too many **FAILED** executions
(ORA errors, rollbacks) in a time window. Configurable by DBA below.

| Setting | Value |
|---------|-------|
| failure_threshold | 3 |
| window_hours | 24 |

## Time Window Rules

| Window | Rule |
|--------|------|
| Business hours (Mon-Fri 08:00-18:00) | Normal rules apply. |
| After hours (18:00-08:00) | PROD: escalate all non-critical. DEV/UAT: normal rules. |
| Maintenance window (configured per DB) | Do not execute any actions. Queue and wait. |
| Change freeze (configured globally) | Block all executions across all environments. Escalate everything. |

## Escalation Triggers

Escalate immediately (skip normal execution) when any of these conditions are true:

1. Target database is not in the environment_registry.
2. Multiple alerts fire for the same database within 5 minutes (possible cascading failure).
3. Execution failed and rollback also failed.
4. Confidence score is below 0.60.
5. Action would affect a protected session or schema.
6. Database is in a maintenance window or change freeze.
