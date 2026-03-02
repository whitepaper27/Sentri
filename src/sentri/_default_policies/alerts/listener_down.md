---
type: alert_pattern
name: listener_down
severity: CRITICAL
action_type: START_LISTENER
version: "1.0"
---

# Listener Down

Detects when an Oracle TNS Listener process has stopped running. A down listener prevents all new client connections to the database. Remediates by restarting the listener via OS-level commands.

## Email Pattern

```regex
(?i)(?:TNS\s+)?listener\s+(\S+)\s+.*?(?:down|not\s+running|stopped|failed|unavailable).*?(?:on|host|server|database)\s+(\S+)
```

## Extracted Fields

- `listener_name` = group(1) -- Name of the listener (e.g., LISTENER, LISTENER_SCAN1)
- `database_id` = group(2) -- Target host or database identifier

## Verification Query

Verify the database instance is running (confirms the DB is up even if listener is down). If this query succeeds, the database is reachable via bequeath or another listener — the reported listener is the one that is down.

```sql
SELECT instance_name,
       status,
       database_status
  FROM v$instance;
```

**Additional context for LLM researcher** (OS-level commands, not executed as SQL):
- `tnsping :listener_name` — returns `OK (10 msec)` if listener is up, `TNS-12541` if down.
- `lsnrctl status :listener_name` — shows listener status and registered services.
- `ps -ef | grep tnslsnr` — checks if the listener process is running.

## Tolerance

- This is a binary check: the listener is either running or not.
- If `tnsping` succeeds at verification time, the alert is treated as self-resolved (the listener may have been restarted by another process or watchdog).

## Pre-Flight Checks

- Database instance is running -- OPEN

```sql
SELECT status FROM v$instance;
```

- ORACLE_HOME is set -- not empty

```sql
SELECT value FROM v$parameter WHERE name = 'db_name';
```

## Forward Action

```sql
-- OS COMMAND (not SQL)
lsnrctl start :listener_name
```

Starts the named listener process. If the listener name is omitted, the default `LISTENER` is started.

**Pre-execution checks**:
1. Verify the listener.ora configuration file exists and is valid.
2. Verify the ORACLE_HOME environment variable is set correctly.
3. Verify the oracle user has permission to start the listener.

**Post-execution**: Confirm the listener is running and services are registered:

```sql
-- OS COMMAND (not SQL)
lsnrctl status :listener_name
```

Expected output should show `status READY` and list registered services.

## Rollback Action

```sql
-- OS COMMAND (not SQL)
lsnrctl stop :listener_name
```

Stops the listener that was started by the forward action. This is only used if the listener start caused unexpected issues (e.g., started with wrong configuration).

**Note**: Rollback is rarely needed. Stopping a listener that was intentionally started to resolve a "listener down" alert would re-create the original problem. This rollback exists only for the case where the start action had unintended side effects.

## Validation Query

```sql
-- OS COMMAND (not SQL)
tnsping :listener_name
```

**Success criteria**: `tnsping` returns `OK` with a response time.

Additionally, verify that database services are properly registered:

```sql
-- OS COMMAND (not SQL)
lsnrctl services :listener_name
```

**Success criteria**: At least one database service is listed and shows `status READY`.

## Risk Level

MEDIUM -- Starting a listener is generally safe, but:
- If the listener.ora has been modified since last start, the new configuration will take effect.
- In RAC environments, starting a listener on the wrong node or with the wrong SCAN configuration can cause routing issues.
- A misconfigured listener could accept connections but route them incorrectly.

## Expected Downtime

BRIEF -- While the listener is down, no new connections can be established. Existing connections are not affected. Once the listener is started, new connections resume immediately. Applications with connection retry logic will reconnect automatically within seconds.

## Estimated Duration

~5 seconds -- Listener startup is nearly instantaneous. Service registration with the database may take an additional 10-60 seconds depending on the `LOCAL_LISTENER` and `REMOTE_LISTENER` parameter configuration.
