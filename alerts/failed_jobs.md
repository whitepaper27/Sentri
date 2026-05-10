---
type: alert_pattern
name: failed_jobs
severity: HIGH
action_type: RESTART_FAILED_JOB
version: "1.0"
---

# Failed Scheduler Job

Detects when a DBMS_SCHEDULER job has failed (status = 'FAILED') and remediates by re-enabling
and rerunning the job. Common causes: resource contention, temporary unavailability of a dependency,
or a transient error in the job's PL/SQL body.

## Email Pattern

```regex
(?i)(?:(?:scheduled?\s+)?job|dbms_scheduler)\s+(?:job\s+)?['\"]?(\S+?)['\"]?\s+(?:has\s+)?(?:failed|error|terminated)\s+.*?(?:on|database|db)\s+(\S+)
```

## Extracted Fields

- `job_name` = group(1) -- Name of the failed job (may include owner prefix, e.g. SENTRI_DEMO.NIGHTLY_STATS)
- `database_id` = group(2) -- Target database identifier

## Verification Query

```sql
SELECT j.owner,
       j.job_name,
       j.job_type,
       j.job_action,
       j.state,
       j.enabled,
       j.last_start_date,
       j.last_run_duration,
       j.failure_count,
       j.max_failures
  FROM dba_scheduler_jobs j
 WHERE (j.job_name = :job_name OR j.job_name = UPPER(:job_name))
    OR (j.owner || '.' || j.job_name = UPPER(:job_name));
```

Check recent run history for the failure details:

```sql
SELECT r.owner,
       r.job_name,
       r.status,
       r.error#,
       r.additional_info,
       r.actual_start_date,
       r.run_duration,
       r.cpu_used
  FROM dba_scheduler_job_run_details r
 WHERE (r.job_name = :job_name OR r.job_name = UPPER(:job_name))
 ORDER BY r.actual_start_date DESC
 FETCH FIRST 5 ROWS ONLY;
```

## Tolerance

- Job must exist in DBA_SCHEDULER_JOBS and have status = 'FAILED' or failure_count > 0.
- If the job is currently RUNNING or SUCCEEDED since the alert was sent, treat as self-resolved.
- If failure_count >= max_failures (job is broken/disabled by Oracle), proceed to re-enable.

## Pre-Flight Checks

- Job exists -- rowcount > 0

```sql
SELECT COUNT(*) FROM dba_scheduler_jobs WHERE job_name = UPPER(:job_name) OR job_name = :job_name;
```

- Job is not a SYS/SYSTEM owned critical job -- not SYS

```sql
SELECT owner FROM dba_scheduler_jobs WHERE job_name = UPPER(:job_name);
```

- Job failure count is not excessive (> 10 failures suggests a code bug, not transient) -- <= 10

```sql
SELECT failure_count FROM dba_scheduler_jobs WHERE job_name = UPPER(:job_name);
```

## Forward Action

Re-enable the job (if disabled due to max_failures breach) and run it immediately:

```sql
BEGIN
  DBMS_SCHEDULER.ENABLE(:job_name);
  DBMS_SCHEDULER.RUN_JOB(job_name => :job_name, use_current_session => FALSE);
END;
```

The `use_current_session => FALSE` runs the job asynchronously in a separate session, so this
call returns immediately without waiting for job completion.

**If the job name includes the owner** (e.g., 'SENTRI_DEMO.NIGHTLY_STATS'), pass it directly:
```sql
BEGIN
  DBMS_SCHEDULER.ENABLE('SENTRI_DEMO.NIGHTLY_STATS');
  DBMS_SCHEDULER.RUN_JOB(job_name => 'SENTRI_DEMO.NIGHTLY_STATS', use_current_session => FALSE);
END;
```

**Safety notes**:
- Only re-enable and restart jobs owned by non-SYS/SYSTEM schemas.
- If failure_count is very high (> 10), escalate to the DBA — the job may have a code bug
  that repeated restarts will not fix.
- Jobs that run DML should be idempotent (safe to re-run). If the job is not idempotent
  (e.g., generates a report file or sends emails), verify with the application team before restarting.

## Rollback Action

```sql
BEGIN
  DBMS_SCHEDULER.DISABLE(:job_name, force => TRUE);
END;
```

Disables the job. Use this if the re-enabled job causes unintended side effects.

## Validation Query

```sql
SELECT j.state,
       j.enabled,
       j.failure_count,
       j.last_start_date
  FROM dba_scheduler_jobs j
 WHERE j.job_name = UPPER(:job_name);
```

**Success criteria**: `state` should be 'SCHEDULED' or 'RUNNING' (not 'FAILED'), and `enabled` = TRUE.
If the job immediately fails again, escalate — the failure is likely a code or dependency issue.

## Risk Level

MEDIUM -- Re-enabling and rerunning a scheduler job can have side effects if the job is not
idempotent. DML-heavy jobs (bulk inserts, purge jobs) are safe to re-run if designed correctly.
Report generation and notification jobs may produce duplicate outputs. Pre-flight check on
failure_count prevents restarting persistently failing jobs.

## Expected Downtime

NONE -- DBMS_SCHEDULER operations do not affect other database sessions or transactions.

## Estimated Duration

~5 seconds for the enable/run call. The job itself may take seconds to hours depending on
its workload — validation checks the status asynchronously after a brief wait.
