---
type: workflow
name: rollback_procedure
version: 1
---

# Rollback Procedure

## When to Rollback

- Validation query fails after execution (metrics did not improve)
- Execution returns an error from the database
- Metrics are worse after the fix than before
- Post-execution health check detects new issues

## Automatic Rollback

Agent 4 (Executor) handles automatic rollback when:

1. Forward SQL executes but validation fails
2. Forward SQL throws an Oracle error

The rollback is immediate and uses the rollback SQL defined in the alert policy file.

## Rollback Steps

1. Execute rollback SQL from the alert policy
2. Verify rollback succeeded (re-run validation query)
3. Write audit record with result = ROLLED_BACK
4. Update workflow status to ROLLED_BACK
5. Notify DBA team via Slack and email
6. Create incident ticket in JIRA (if configured)

## Post-Rollback Actions

- Mark workflow as ROLLED_BACK
- Escalate to DBA on-call for manual investigation
- Do NOT auto-retry the same action
- Log detailed evidence (before/after metrics, error messages)

## If Rollback Fails

This is a critical situation:

1. Mark workflow as ESCALATED immediately
2. Page DBA on-call via PagerDuty (if configured)
3. Send urgent Slack message to #dba-emergency
4. Halt ALL automated operations on the affected database
5. Require manual intervention to resume operations
6. Log everything: original error, rollback error, timestamps

## Rollback Limitations

Some operations cannot be fully rolled back:

- **Archive log deletion**: Deleted logs cannot be restored (but expired logs are safe to delete)
- **Listener restart**: Stopping a listener that was just started is safe but may disrupt connections
- **Datafile addition**: Can be dropped, but only if no extents have been allocated

Each alert policy file documents its specific rollback capabilities and limitations.

## Testing

- All rollback SQL should be tested in DEV before any PROD deployment
- Periodic rollback drills should be conducted to verify procedures
- Agent 5 (Analyst) will track rollback success rates (future)
