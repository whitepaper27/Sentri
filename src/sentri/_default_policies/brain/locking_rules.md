---
type: core_policy
name: locking_rules
version: 1
---

# Locking Rules

This document defines the resource locking mechanism that prevents concurrent modifications to the same database objects. Sentri uses application-level advisory locks stored in SQLite to coordinate access across workflows. These locks are independent of Oracle's internal locking mechanisms and serve to ensure that only one Sentri workflow modifies a given resource at a time.

## Lock Granularity

Locks are identified by a hierarchical key that specifies the scope of the locked resource. The format is:

```
DATABASE_ID:OBJECT_TYPE:OBJECT_NAME
```

### Key Components

- **DATABASE_ID**: The unique identifier for the target database as registered in the environment_registry table (e.g., `PROD-DB-07`, `DEV-DB-01`).
- **OBJECT_TYPE**: The category of database object being modified. Valid values: `TABLESPACE`, `DATAFILE`, `LISTENER`, `ARCHIVE`, `TEMP`, `SERVICE`.
- **OBJECT_NAME**: The specific object name. Use `*` (wildcard) when the operation affects the entire object type scope.

### Lock Key Examples

| Scenario | Lock Key | Scope |
|----------|----------|-------|
| Adding datafile to USERS tablespace | `PROD-DB-07:TABLESPACE:USERS` | Single tablespace |
| Cleaning archive destination | `PROD-DB-07:ARCHIVE:*` | All archive operations on this database |
| Extending temp tablespace | `DEV-DB-01:TEMP:TEMP` | Temp tablespace on DEV database |
| Restarting listener | `UAT-DB-03:LISTENER:LISTENER_UAT` | Specific listener |
| Resolving archive gap | `PROD-DB-07:ARCHIVE:GAP` | Archive gap resolution |

### Wildcard Behavior

When a lock is held with a wildcard (`*`) as the OBJECT_NAME, it blocks all lock requests for that DATABASE_ID and OBJECT_TYPE combination, regardless of the specific OBJECT_NAME requested.

Conversely, a lock on a specific OBJECT_NAME does not block a lock request for a different OBJECT_NAME under the same DATABASE_ID and OBJECT_TYPE.

Example:
- Lock `PROD-DB-07:TABLESPACE:USERS` is held.
- Lock request for `PROD-DB-07:TABLESPACE:TEMP` will succeed (different object).
- Lock request for `PROD-DB-07:TABLESPACE:USERS` will block (same object).
- Lock `PROD-DB-07:ARCHIVE:*` is held.
- Lock request for `PROD-DB-07:ARCHIVE:DEST1` will block (wildcard covers all).

## Lock Timeout

### Acquisition Timeout

**Duration**: 30 seconds

When Agent 4 (Executor) requests a lock, it must acquire the lock within 30 seconds. If the lock cannot be acquired within this window, the acquisition attempt fails.

### Retry Behavior

On acquisition failure:

1. Wait 10 seconds.
2. Retry the acquisition (up to 3 total attempts: 1 initial + 2 retries).
3. If all 3 attempts fail, the workflow transitions to ESCALATED with reason `lock_acquisition_failed`.

### Retry Backoff

| Attempt | Wait Before | Timeout |
|---------|------------|---------|
| 1 (initial) | 0 seconds | 30 seconds |
| 2 (first retry) | 10 seconds | 30 seconds |
| 3 (second retry) | 10 seconds | 30 seconds |

Total maximum wait time before escalation: 80 seconds (30 + 10 + 30 + 10 + 30 - overlap from immediate first attempt not counted, but the worst-case wall time is approximately 80 seconds).

## Lock Expiry

### Automatic Release Duration

**Duration**: 10 minutes

Every lock has a maximum hold time of 10 minutes from the moment it is acquired. If a lock is not explicitly released within this window, it is automatically released by the lock cleanup process.

### Rationale

The 10-minute expiry prevents resource starvation in the event of:
- Agent 4 crashing mid-execution without releasing the lock.
- An unhandled exception that bypasses the lock release in the finally block.
- A thread deadlock in the orchestrator.

### Expiry Behavior

When a lock expires:

1. The lock record is marked as `expired` in the database.
2. A WARNING-level entry is written to the audit log indicating that a lock was not properly released.
3. The resource becomes available for new lock requests.
4. If the workflow that held the lock is still in EXECUTING state, it is transitioned to FAILED with reason `lock_expired_during_execution`.

## Deadlock Prevention

### Ordered Acquisition

When a workflow requires multiple locks (rare in the POC scope but possible in future), locks must be acquired in **alphabetical order** of their lock keys.

Example: If a workflow needs locks on both `PROD-DB-07:TABLESPACE:TEMP` and `PROD-DB-07:TABLESPACE:USERS`, it must acquire them in this order:

1. `PROD-DB-07:TABLESPACE:TEMP` (T comes before U)
2. `PROD-DB-07:TABLESPACE:USERS`

### No Hold-and-Wait

An agent must not hold a lock while waiting to acquire a second lock using a blocking wait. Instead:

1. Attempt to acquire the first lock (with timeout).
2. If successful, attempt to acquire the second lock (with timeout).
3. If the second lock acquisition fails, release the first lock and retry the entire sequence after a backoff period.

This eliminates the possibility of circular wait conditions.

### Single-Lock Preference

For the POC, workflows should be designed to require only a single lock per execution. The multi-lock protocol above is defined for future extensibility but should not be needed for the initial five alert types.

## Stale Lock Cleanup

### Cleanup Process

The orchestrator runs a background cleanup task every 60 seconds that:

1. Queries all locks where `acquired_at + 10 minutes < current_time`.
2. For each stale lock:
   a. Checks if the owning workflow is still in EXECUTING state.
   b. If yes: transitions the workflow to FAILED with reason `lock_expired_during_execution` and releases the lock.
   c. If no (workflow already in terminal state): releases the lock silently (the workflow completed but forgot to release).
3. Logs the cleanup action in the audit log.

### Lock Table Schema

```sql
CREATE TABLE resource_locks (
    lock_key TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    acquired_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE, RELEASED, EXPIRED
    released_at TIMESTAMP,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);

CREATE INDEX idx_locks_expires ON resource_locks(expires_at);
CREATE INDEX idx_locks_status ON resource_locks(status);
```

### Lock Operations

**Acquire**:
```sql
INSERT INTO resource_locks (lock_key, workflow_id, acquired_at, expires_at, status)
VALUES (:key, :workflow_id, CURRENT_TIMESTAMP, datetime('now', '+10 minutes'), 'ACTIVE');
```
This will fail with a UNIQUE constraint violation if the lock is already held, which is the expected behavior for lock contention.

**Release**:
```sql
UPDATE resource_locks
SET status = 'RELEASED', released_at = CURRENT_TIMESTAMP
WHERE lock_key = :key AND workflow_id = :workflow_id AND status = 'ACTIVE';
```

**Cleanup**:
```sql
UPDATE resource_locks
SET status = 'EXPIRED', released_at = CURRENT_TIMESTAMP
WHERE status = 'ACTIVE' AND expires_at < CURRENT_TIMESTAMP;
```

### Lock Contention Monitoring

If a specific lock key experiences contention more than 5 times within a 1-hour window, the system generates a WARNING notification. This may indicate:
- A recurring issue on the database that is generating alerts faster than they can be resolved.
- A slow-running fix that is holding the lock longer than expected.
- A configuration issue where the lock expiry is too short for the typical execution time.
