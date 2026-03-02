---
type: agent_config
name: analyst
version: 1
status: stub
---

# Agent 5: The Analyst (Learning Engine)

> **Status**: STUB for POC -- This agent performs minimal logging only.
> Active learning and confidence adjustment will be added in future phases.

## Purpose

Track the outcomes of all executed workflows, measure prediction accuracy, identify
trends, and progressively improve the system's confidence scores and decision-making.
The Analyst closes the feedback loop, turning Sentri from a static rule engine into
an adaptive system that learns from its own operational history.

For the POC, the Analyst is limited to recording basic outcome data. It does not
perform any analysis, scoring adjustments, or pattern recognition.

## POC Behavior

In the current POC implementation, the Analyst:

1. **Records outcome data** to the `audit_log` table after each workflow completes.
   Captures:
   - Whether the fix succeeded or failed
   - Whether a rollback was triggered
   - Time from detection to resolution
   - Whether the alert was a false positive

2. **Uses static confidence scores** -- All confidence values are hardcoded in the
   alert policy files (`alerts/*.md`) and do not change based on outcomes.

3. **No active learning** -- The Analyst does not analyze trends, adjust thresholds,
   or modify any policy files.

4. **Basic counters** -- Maintains simple counters in the `cache` table:
   - `analyst:total_workflows` -- Total workflows processed
   - `analyst:success_count` -- Successful resolutions
   - `analyst:failure_count` -- Failed resolutions
   - `analyst:rollback_count` -- Rollbacks triggered
   - `analyst:false_positive_count` -- False positives detected by Agent 2

These counters are used by the `sentri stats` CLI command to display summary
metrics.

## Future Scope

### Outcome Tracking (Month 3-4)

- Compare predicted outcome (from confidence score) against actual outcome
- Calculate per-alert-type success rates over rolling 30-day windows
- Track mean time to resolution (MTTR) per alert type and environment
- Identify alert types with declining success rates

### False Positive Analysis (Month 4-5)

- Track the false positive rate for each alert type
- Correlate false positives with specific conditions (time of day, database load,
  maintenance windows, recent changes)
- Recommend threshold adjustments to reduce false positive rates
- Generate weekly false positive reports for DBA review

### Confidence Score Adjustment (Month 5-7)

- Implement Bayesian confidence updates based on outcome history
- Increase confidence for alert patterns with high success rates
- Decrease confidence for patterns with frequent failures or rollbacks
- Apply environment-specific confidence modifiers (DEV fixes succeed more often
  than PROD fixes due to lower complexity)
- Publish confidence score changes to the audit trail

### Pattern Library (Month 7-9)

- Build a searchable library of resolution patterns from successful workflows
- Categorize patterns by alert type, database version, architecture, and environment
- Enable pattern reuse -- when a new alert matches a known pattern, suggest the
  proven resolution
- Track pattern effectiveness over time (some patterns degrade as environments evolve)

### Predictive Analytics (Month 9-12)

- Analyze historical data to predict upcoming issues before alerts fire
- Identify databases trending toward tablespace full, archive accumulation, etc.
- Generate proactive recommendations ("PROD-DB-07 USERS tablespace will reach 90%
  in approximately 14 days at current growth rate")
- Weekly trend reports delivered via email or Slack

### Anomaly Detection (Month 12+)

- Detect unusual patterns in alert frequency or timing
- Identify correlated failures across multiple databases
- Flag potential systemic issues (e.g., storage subsystem degradation causing
  multiple tablespace alerts)
- Integrate with change management to correlate alerts with recent deployments
