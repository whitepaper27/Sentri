# Adoption Guide

A practical guide for L2/L3 Oracle DBAs rolling out Sentri — from first install to production monitoring.

This is not a marketing document. It covers phased rollout with concrete timelines, exit criteria at every gate, and honest answers to the objections you're already thinking of.

**Prerequisites**: You've read the [Getting Started](docs/getting-started.md) guide and have Sentri installed. For safety details, see the [Safety Model](docs/safety-model.md).

---

## Why This Matters to You (Not Your Manager)

**The 3 AM tablespace alert.** You wake up, VPN in, run the same `ALTER TABLESPACE ... ADD DATAFILE` you've run 200 times, go back to sleep. Sentri runs it in 7 seconds with a full audit trail.

**The recurring Friday archive_dest_full.** You've fixed this 6 Fridays in a row. Sentri detects the pattern after 3 occurrences and flags it for root cause investigation instead of applying the same band-aid.

**The stale stats nobody notices.** Tables go 90 days without `GATHER_TABLE_STATS`. Query plans degrade slowly. Nobody notices until the application team complains. Proactive health checks catch this on a schedule.

**What Sentri does NOT promise**: It will not replace you. It handles the repetitive, well-understood L1/L2 tasks — the ones that wake you up at 3 AM and take 2 minutes to fix but 20 minutes of VPN/login overhead. It explicitly escalates complex problems to you: RAC issues, Data Guard, partitioned DDL, anything with confidence below 0.60. See the [full limitations list](docs/safety-model.md#what-sentri-cannot-do).

**Your expertise drives it.** The `.md` policy files encode YOUR knowledge. You decide which alerts auto-execute, which need approval, what the rollback is. Sentri is the executor; you're the policy author.

| Before Sentri | After Sentri |
|---|---|
| Wake up for tablespace alerts | Review audit trail next morning |
| Run the same fix manually each time | Fix runs automatically, you approve PROD via email reply |
| No pattern visibility across weeks | 90-day pattern detection flags recurring issues |
| Stale stats discovered during outage | Proactive check catches them daily |
| Tribal knowledge in people's heads | Knowledge codified in `.md` files, version controlled |
| "What happened last night?" — check OEM logs | `sentri audit --last 20` shows every action, who approved, what happened |

---

## The Four-Phase Adoption Roadmap

### Phase 0: Shadow Mode (Weeks 1-2)

**Goal**: Sentri runs, observes, proposes — but executes nothing. Build confidence in detection and diagnosis accuracy.

**Setup**:
1. Install Sentri and run `sentri init`
2. Add 1-2 DEV databases to `config/sentri.yaml`
3. Configure IMAP email (point at the inbox that receives DBA alerts)
4. Override ALL databases to ADVISORY — this makes Sentri detect, verify, and propose, but never execute without your explicit approval

Per-database override — edit the environment file `environments/dev_db_01.md` and add an override to the YAML frontmatter:
```yaml
---
type: environment
database_id: DEV-DB-01
environment: DEV
autonomy_level: AUTONOMOUS
autonomy_override: ADVISORY
override_reason: "Shadow mode - evaluation period"
override_approved_by: your.name
override_expires: 2026-04-15
---
```
The override takes precedence over `autonomy_level`. It expires automatically after the date you set (max 90 days). When it expires, the database reverts to its base `autonomy_level`.

**What to watch**:
- `sentri list` — see detected workflows. Are all your alert emails being picked up?
- `sentri show <id>` — see the proposed SQL. Compare it to what you would have done.
- `sentri audit` — verify no unexpected database queries (all reads are through thin-mode, 30s timeout, 50-row max)
- `sentri show-profile <db>` — confirm the profiler correctly identified your database (version, CDB/PDB, OMF, bigfile)

**Exit criteria — metrics** (measurable via `sentri stats` and `sentri show`):
- [ ] Detection rate > 90% (alerts detected / alerts received via email)
- [ ] False positive rate < 10% (verification correctly confirms alerts are real)
- [ ] Proposed SQL matches your judgment for 80%+ of known alert types
- [ ] No unexpected activity in `sentri audit`

**Exit criteria — process gates**:
- [ ] Sentri Champion identified (owner of config and policies)
- [ ] Team has reviewed at least 10 proposed workflows via `sentri show`
- [ ] Alert regex tuning complete (no further email format mismatches)

**Abort criteria**: If Sentri can't parse your monitoring tool's email format, fix the regex in the alert `.md` file before proceeding. If detection rate is below 70%, check IMAP folder config and database alias mappings.

**Duration**: 1-2 weeks. Longer if your alert email format needs significant regex tuning.

---

### Phase 1: Supervised DEV (Weeks 3-4)

**Goal**: Let Sentri execute on DEV databases with you watching.

**Config change**: Remove the ADVISORY override on DEV databases. DEV reverts to its default AUTONOMOUS level. If you want to approve the first few manually, set to SUPERVISED instead.

**Start with the safest alert types.** Not all alerts carry the same risk. This table shows the recommended enablement order:

| Order | Alert Type | Risk | Why This Order |
|-------|-----------|------|----------------|
| 1 | `tablespace_full` | LOW | Online, additive (ADD DATAFILE), well-tested, simple rollback |
| 2 | `temp_full` | LOW | Same as above — ADD TEMPFILE is non-disruptive |
| 3 | `archive_dest_full` | MEDIUM | Involves deletion of archived logs — enable after you trust the archive rules |
| 4 | `high_undo_usage` | MEDIUM | May adjust undo retention parameter |
| 5 | `stale_stats` (proactive) | LOW | DBMS_STATS is non-destructive, no rollback needed |
| 6 | `long_running_sql` | MEDIUM | Investigation-only — always requires approval |
| 7 | `cpu_high` | MEDIUM | Investigation-only — always requires approval |
| 8 | `session_blocker` | HIGH | May kill sessions — always requires approval even in DEV |

**What to watch after each execution**:
- `sentri show <id>` — review the full workflow: proposed SQL, pre/post metrics, execution time, validation result
- Confirm rollback SQL was captured
- Confirm the safety checks logged all 5 gates passing
- Manually trigger a rollback at least once to verify it works on your environment

**Exit criteria**:
- [ ] 5+ successful automated executions on DEV
- [ ] 0 unplanned rollbacks
- [ ] Audit trail is clean and readable
- [ ] Team comfortable with `sentri show`, `sentri audit`, `sentri stats`
- [ ] At least `tablespace_full` and `temp_full` executing cleanly

**Rollback**: Set DEV back to ADVISORY at any time. One config change, no data loss, no impact.

---

### Phase 2: Supervised UAT (Weeks 5-8)

**Goal**: Extend to UAT databases. UAT data often mirrors production — this is where you build the trust that translates to PROD.

**Setup**:
1. Add UAT databases to `sentri.yaml`
2. Start the first week with `autonomy_override: ADVISORY` on all UAT databases
3. After one week, remove the override — UAT defaults to SUPERVISED (low-risk auto-executes, high-risk requires approval)

**Practice the approval workflow.** This is where the email approval flow gets exercised:
1. Sentri sends an approval email with the proposed SQL and `[WF:xxxxxxxx]` in the subject
2. Review the SQL, risk assessment, and investigation context
3. Reply APPROVED or DENIED (with optional reason)
4. Verify the workflow executed (or was denied) via `sentri show <id>`

Test both paths — approve some, deny some with a reason. Also try CLI approval:
```bash
sentri approve <id>
sentri approve <id> --deny --reason "Not during batch window"
```

**Enable proactive health checks**: Start with `stale_stats` and `tablespace_trend` from the `checks/` directory. These create findings (like alerts from email) but are triggered on a schedule instead.

**Exit criteria**:
- [ ] 10+ successful workflows on UAT (mix of auto-execute and approved)
- [ ] Email approval workflow works reliably (sent, received, processed)
- [ ] At least one DENIED workflow tested (with reason)
- [ ] Circuit breaker has not tripped (no cluster of failures)
- [ ] 2+ team members can operate `sentri list`, `sentri show`, `sentri approve`
- [ ] Approval rotation defined (who responds to approval emails)

**Duration**: 3-4 weeks. Intentionally longer — UAT is where you validate the approval workflow and team processes.

---

### Phase 3: PROD Advisory (Weeks 9-12+)

**Goal**: Sentri monitors PROD, proposes fixes, but always requires DBA approval.

**This is the default.** PROD environment is ADVISORY — every fix requires a human to reply APPROVED. Sentri never auto-executes on PROD. No override needed.

**Setup**:
1. Add PROD databases to `sentri.yaml` with `environment: PROD`
2. Configure change freeze periods in `brain/rules.md` (match your ITIL calendar)
3. Configure after-hours escalation rules (non-critical alerts escalate instead of requesting approval at 3 AM)
4. List protected databases if applicable (finance, HR — require dual approval)
5. Set approval email recipients to include your on-call DBA rotation

**What's different about PROD**:
- Memory lookback is longer (48h default vs 12h for DEV) — more context in investigations
- Change freeze periods block all executions (configurable in `brain/rules.md`)
- Circuit breaker is critical — 3 failures → all automation blocked → DBA investigates

**Start with `tablespace_full` and `temp_full` only.** These are the two alert types you've now validated through DEV and UAT. Add more after 4+ weeks of clean PROD operation.

**The approval is your safety net.** Every PROD fix shows you:
- The exact SQL to be executed
- The investigation context (what Sentri found when it queried the database)
- The confidence score and risk classification
- The rollback SQL (what happens if you need to undo)

You make the call. Sentri waits.

**Exit criteria for expanding to more alert types on PROD**:
- [ ] 20+ approved-and-executed workflows on PROD with zero rollbacks
- [ ] MTTR measurably improved (track time from alert email to fix applied)
- [ ] Team has defined on-call rotation for approval emails
- [ ] Audit trail reviewed weekly with no surprises
- [ ] Change freeze periods tested (verify Sentri blocks execution during freeze)

---

## Success Metrics

Track these per phase. All available via `sentri stats` and `sentri audit`.

| Metric | Phase 0 | Phase 1 | Phase 2 | Phase 3 |
|--------|---------|---------|---------|---------|
| Detection accuracy (alerts detected / received) | > 90% | > 95% | > 95% | > 95% |
| False positive rate (bad verifications / detections) | < 10% | < 5% | < 5% | < 3% |
| SQL correctness (proposed SQL matches DBA judgment) | > 80% | > 90% | > 90% | > 95% |
| MTTR (alert to fix applied) | N/A | < 5 min | < 15 min | Depends on approval response time* |
| Rollback rate (rollbacks / executions) | N/A | < 5% | < 3% | 0% |
| Circuit breaker trips per month | 0 | < 2 | < 1 | 0 |
| DBA hours saved per week | 0 | 1-2h | 3-5h | 5-10h |
| Alert coverage (types handled / total types received) | N/A | 2-3 | 4-5 | 5-7 |

*\*PROD MTTR = Sentri processing time (~1 min) + DBA approval response time. With a 1-hour approval timeout, realistic MTTR is 5-60 min depending on how fast the on-call DBA responds. Track your actual approval response times to set a meaningful target. If your team consistently responds within 10 min, set a 15 min MTTR target.*

**Custom reporting** — query `sentri.db` directly:

```sql
-- Success rate per alert type (last 30 days)
SELECT alert_type,
       COUNT(*) as total,
       SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as succeeded,
       ROUND(100.0 * SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) / COUNT(*), 1) as pct
FROM workflows
WHERE created_at > datetime('now', '-30 days')
GROUP BY alert_type;

-- Average resolution time
SELECT alert_type,
       ROUND(AVG((julianday(updated_at) - julianday(created_at)) * 1440), 1) as avg_minutes
FROM workflows
WHERE status = 'COMPLETED'
AND created_at > datetime('now', '-30 days')
GROUP BY alert_type;
```

---

## Team Structure

### Roles

**Sentri Champion** (1 person): Owns `sentri.yaml` config, policy `.md` files, and troubleshooting. First point of contact when Sentri behaves unexpectedly. During adoption, this is the person driving the pilot.

**Approval Rotation** (2+ people): DBAs on the email approval recipient list. First responder handles the approval; if no response within 30 min, second responder picks up. Sentri's timeout (default 1 hour, configurable) handles the case where nobody responds — workflow is escalated.

**Policy Authors** (any DBA): Anyone can edit `.md` files in `brain/`, `alerts/`, `checks/`. Changes are version-controlled via git. Recommend PR-based review for PROD policy changes. Track changes with `sentri versions <alert_type>`.

**Escalation Handlers**: DBAs who handle escalated workflows — cases Sentri couldn't resolve (low confidence, circuit breaker tripped, complex problem). Sentri provides its investigation findings even when it can't fix the issue.

### RACI

| Activity | Sentri Champion | On-Call DBA | DBA Manager |
|----------|:-:|:-:|:-:|
| Config changes (`sentri.yaml`) | R/A | C | I |
| Policy `.md` changes (`brain/`, `alerts/`) | R | C | A |
| PROD approval responses | I | R/A | I |
| Escalation handling | C | R/A | I |
| Weekly audit review | R | C | A |
| Phase gate decisions | R | C | A |

---

## Common Objections

### "What if it runs the wrong SQL on PROD?"

It cannot execute on PROD without your explicit approval. PROD is ADVISORY — every fix requires you to reply APPROVED to the email or run `sentri approve <id>`. The approval email shows the exact SQL.

Even after you approve, the Safety Mesh checks blast radius (DROP/TRUNCATE are blocked), circuit breaker (3 recent failures = blocked), and rollback availability. If post-execution validation fails, auto-rollback executes immediately.

### "Will this replace my job?"

Sentri handles the repetitive L1/L2 work: tablespace full at 3 AM, temp full during batch runs, stale stats nobody noticed. It explicitly escalates complex problems to you — RAC issues, Data Guard, partitioned DDL, anything with confidence below 0.60.

The fact that this adoption guide exists proves the point: your expertise is needed to configure policies, review proposals, approve PROD changes, handle escalations, and tune the system.

### "How do I know what it did?"

`sentri audit` shows every action with full SQL, before/after metrics, who approved, and outcome. Immutable, append-only. `sentri show <workflow_id>` gives the complete decision trace for any single workflow.

### "I don't trust LLMs writing SQL for my databases."

Valid concern. Sentri works without any LLM — template-based fixes use the SQL you wrote in your alert `.md` files. Start there.

When you enable an LLM, verified syntax rules check every generated SQL against known Oracle constraints: BIGFILE tablespaces = RESIZE not ADD DATAFILE, OMF-managed databases = no explicit file paths, CDB = correct container context. SQL that violates these rules is silently dropped. If all AI-generated candidates fail validation, Sentri falls back to your template.

The three-level fallback: AI investigation mode (queries your DB first, then generates SQL) → Quick AI mode (single-shot generation) → Your runbook SQL (the `.md` file you wrote). The AI is a bonus, not a dependency.

### "Our alert emails have a different format."

The regex patterns in `alerts/*.md` are fully customizable. Change the regex to match your OEM/Zabbix/Nagios/custom format. Phase 0 (Shadow Mode) is specifically designed to catch and fix these mismatches before anything executes.

### "What happens when Sentri itself goes down?"

Your databases are unaffected. Sentri only reacts to alerts — it doesn't generate them. If Sentri stops, alerts pile up in the IMAP inbox and your team handles them manually, same as before Sentri existed.

On restart, Sentri processes unread emails from where it left off. No alerts are lost (IMAP is the durable queue).

### "We have change management / ITIL processes."

PROD is ADVISORY by default — nothing executes without approval. The approval email is your change ticket. The audit trail provides evidence for post-change review.

Change freeze periods and maintenance windows are configurable in `brain/rules.md`. During a freeze, Sentri blocks all executions and escalates. After-hours rules can escalate non-critical alerts instead of generating approval requests.

---

## Day-2 Operations

### Monitoring Sentri Itself

| Check | How | Frequency |
|-------|-----|-----------|
| Process running | `systemctl status sentri` or process check | Continuous |
| Email polling active | Check `sentri.log` for Scout poll cycle entries | Daily |
| SQLite healthy | `sentri.db` file exists, WAL file not growing unbounded | Weekly |
| DB connections working | `sentri db test` | Weekly |
| LLM reachable (if configured) | Check cost_tracker table for recent usage | Weekly |

### Log Monitoring

Log location: `~/.sentri/logs/sentri.log` (rotating, 10MB x 5)

Key patterns to alert on:
- `ERROR` — unexpected failures
- `CIRCUIT_BREAKER_TRIPPED` — automation blocked on a database
- `ESCALATED` — Sentri couldn't handle something
- `ROLLBACK` — a fix was reversed

Forward Sentri logs to your existing aggregator (ELK, Splunk, etc.) for centralized visibility.

### Weekly Review Checklist

- [ ] Run `sentri stats` — check success rate trend
- [ ] Run `sentri audit --last 50` — scan for unexpected actions
- [ ] Check circuit breaker status — any databases blocked?
- [ ] Review escalated workflows — were they correctly escalated?
- [ ] Check `sentri.log` for ERROR entries
- [ ] Verify PROD approval emails are being processed (no timeouts piling up)
- [ ] Review proactive health check findings
- [ ] Confirm all configured databases are reachable (`sentri db test`)

### Troubleshooting

**Workflow stuck in EXECUTING**: `sentri cleanup --stuck` clears stale locks. Investigate why the Oracle connection timed out.

**High rollback rate**: Check the alert `.md` file. Is the forward action correct for your environment? Is the validation query too strict?

**Circuit breaker tripped**: Run `sentri audit --db <name>` to see the 3+ failures. Fix the underlying issue. Circuit breaker resets after 24h with no failures.

**Email parsing misses**: Check the regex in the alert `.md` file. Test by sending a sample email and running `sentri list` to see if it was detected.

### Capacity

- SQLite: ~1 MB per 1,000 workflows. `sentri cleanup --cache` purges old dedup cache.
- Memory: ~100-200 MB RSS typical. Spikes during LLM calls with large context.
- Network: IMAP poll + Oracle thin connections + optional LLM API calls. All outbound.

---

## What to Customize First

Ordered by impact:

| Priority | What | Where | When |
|:--------:|------|-------|------|
| 1 | Alert email regex patterns | `alerts/*.md` — Email Pattern section | Phase 0 — match your email format |
| 2 | Database aliases | `sentri.yaml` — `aliases` field per database | Phase 0 — match email references to DB config |
| 3 | Approval recipients and timeout | `sentri.yaml` — approvals section | Phase 2 — before enabling approval workflow |
| 4 | Action approval rules | `brain/rules.md` — which actions need approval per environment | Phase 2 — tune to your org's risk tolerance |
| 5 | Health check schedules | `checks/*.md` — `schedule` field | Phase 2 — match your monitoring cadence |
| 6 | Time windows and freeze periods | `brain/rules.md` — time window rules | Phase 3 — match your ITIL calendar |
| 7 | Org-specific alert types | New `.md` files in `alerts/` | After Phase 3 — see [Adding Alerts](docs/adding-alerts.md) |

### What Happens When You Edit a Policy File

Every `.md` file directly controls Sentri's behavior. Here's what actually changes when you edit the most common files:

| You Edit | What Changes | Example |
|----------|-------------|---------|
| `brain/autonomy_levels.md` — change PROD from ADVISORY to SUPERVISED | Low-risk PROD fixes (tablespace_full, temp_full) will **auto-execute without asking you**. High-risk fixes still require approval. | You stop getting 3 AM approval emails for tablespace adds — they just run. |
| `brain/rules.md` — add a change freeze window | All executions are **blocked** during that window. Sentri still detects and proposes, but queues everything until the freeze ends. | You add a freeze for `2026-03-15 to 2026-03-22` and no fixes run during your release week. |
| `brain/rules.md` — change `session_blocker` from `approval` to `auto` in DEV | Sentri will **kill blocking sessions on DEV without asking**. Be sure you want this. | A long-running dev query blocks 5 others — Sentri kills it immediately instead of emailing you. |
| `alerts/tablespace_full.md` — change the Email Pattern regex | Sentri matches **different email subjects**. If the regex is wrong, alerts stop being detected. | You switch from OEM to Zabbix alerts — update the regex to match Zabbix's subject format. |
| `alerts/tablespace_full.md` — change the Forward Action SQL | A **different SQL command runs** when this alert fires. The old rollback may not undo the new action. Update both together. | You change from ADD DATAFILE to RESIZE — update the rollback to match. |
| `checks/stale_stats.md` — change schedule from `daily` to `every_6_hours` | The check runs **4x more often**. More findings, more workflows, more potential executions. | You want faster stats freshness — but now Sentri may gather stats during peak hours. |
| `environments/PROD-DB-07.md` — add `autonomy_override: SUPERVISED` | That specific PROD database gets **relaxed approval** (low-risk = auto, high-risk = approval). Other PROD databases are unaffected. | Your least critical PROD database gets faster fixes while your finance DB stays fully gated. |
| Delete an alert `.md` file (e.g., `alerts/temp_full.md`) | Sentri **stops handling that alert type entirely**. Matching emails arrive but are ignored. Existing in-progress workflows for that type continue to completion. | You remove `temp_full.md` because you handle temp tablespace manually — Sentri no longer detects or proposes fixes for temp alerts. |
| Create a new `.md` file in `alerts/` | Sentri picks it up on the **next poll cycle** — no restart needed. If no routing entry exists in `brain/routing_rules.md`, it defaults to the storage handler. | You add `alerts/fra_full.md` for Flash Recovery Area alerts. It routes to storage_agent automatically. Add an explicit routing rule if you want a different specialist. |

**The safe workflow for editing policy files**:
1. Edit the `.md` file
2. Review your change with `git diff`
3. Test it manually: send a sample alert email to your monitored inbox and watch `sentri list` for the expected detection. For non-alert changes (brain/, checks/), check `sentri audit` after the next poll cycle to verify the new behavior.
4. Monitor `sentri list` and `sentri audit` over the next few poll cycles to confirm behavior matches your intent

Future (v5.2): `sentri validate` will check `.md` files for syntax errors, missing sections, and broken regex. `sentri test-alert <type> --email "subject line"` will verify regex matching without sending a real email.

---

## Rollout Checklists

### Phase 0: Shadow Mode

- [ ] Sentri installed and `sentri init` completed
- [ ] 1-2 DEV databases configured in `sentri.yaml`
- [ ] IMAP email configured and `sentri start` shows poll activity
- [ ] All databases set to ADVISORY override
- [ ] Ran for 1+ week with real alert emails
- [ ] Detection rate > 90%
- [ ] False positive rate < 10%
- [ ] Proposed SQL reviewed for 10+ workflows
- [ ] No unexpected queries in `sentri audit`
- [ ] Sentri Champion identified

### Phase 1: Supervised DEV

- [ ] DEV database overrides removed (AUTONOMOUS default active)
- [ ] `tablespace_full` enabled and executing
- [ ] 5+ successful executions with zero rollbacks
- [ ] Audit trail reviewed — pre/post metrics recorded correctly
- [ ] `temp_full` enabled (second alert type)
- [ ] Rollback manually triggered at least once to verify it works
- [ ] Team comfortable with `sentri show`, `sentri audit`, `sentri stats`

### Phase 2: Supervised UAT

- [ ] UAT databases added to `sentri.yaml`
- [ ] First week in ADVISORY override, then moved to SUPERVISED
- [ ] Email approval workflow tested — APPROVED reply
- [ ] Email approval workflow tested — DENIED reply with reason
- [ ] CLI approval tested (`sentri approve`, `sentri approve --deny`)
- [ ] Proactive health checks enabled (`stale_stats`, `tablespace_trend`)
- [ ] 10+ successful workflows on UAT
- [ ] Circuit breaker has not tripped
- [ ] 2+ team members can operate Sentri CLI
- [ ] Approval rotation defined

### Phase 3: PROD Advisory

- [ ] PROD databases added with `environment: PROD`
- [ ] Only `tablespace_full` and `temp_full` enabled initially
- [ ] Change freeze periods configured in `brain/rules.md`
- [ ] After-hours escalation rules configured
- [ ] Protected databases listed (if applicable)
- [ ] Approval email recipients include on-call rotation
- [ ] 20+ approved workflows with zero rollbacks
- [ ] Weekly audit review process established
- [ ] MTTR baseline established and improvement measured
- [ ] Day-2 monitoring in place (process, logs, DB connectivity)

---

## Notification Coverage

Sentri sends email notifications at every terminal workflow state so the DBA always knows what happened:

| Event | Email Content |
|-------|-------------|
| **Completion** (success) | Green banner, fix SQL executed, rollback SQL, confidence score, pre/post metrics |
| **Completion** (failure) | Red banner, what failed, rollback status |
| **Escalation** | Red banner, reasons for escalation (low confidence, circuit breaker, agent failure) |
| **Timeout** | Approval timed out, workflow escalated |
| **Denial** | DBA denied the workflow, reason included |

### RCA Recommendations in Completion Emails

When the same alert fires repeatedly on the same database, the completion email includes an amber "Recommendations" section suggesting root cause investigation. This is **informational only** — Sentri still executes the fix every time. The DBA decides whether to investigate root cause.

Default: 3 alerts in 24 hours triggers the recommendation. Configurable in `brain/rules.md`:

```markdown
| Setting | Value | Description |
|---------|-------|-------------|
| rca_alert_count | 3 | Number of same alerts on same DB to trigger RCA recommendation |
| rca_window_hours | 24 | Time window for counting repeat alerts |
```

### Design Philosophy: No Hardcoded Blocking

**Sentri never blocks execution based on hardcoded rules.** All policy decisions — which alerts need approval, cooldown periods, freeze windows — are controlled by DBAs via `.md` files in `brain/`.

If an alert fires 10 times in an hour, Sentri fixes it 10 times and tells the DBA "you might want to investigate root cause." It does NOT refuse to act, impose cooldowns, or second-guess the DBA's configuration. The DBA can add approval requirements in `brain/rules.md` if they want gating — that's their call, not Sentri's.

This is intentional: Sentri is the executor, the DBA is the policy author. An autonomous agent that refuses to follow its own policies is worse than one that faithfully executes them.

---

## What Comes After Phase 3

**More alert types on PROD.** After 4+ weeks of clean `tablespace_full` / `temp_full` operation, add `archive_dest_full`, then `high_undo_usage`, then proactive checks. Same trust-building cycle: shadow → DEV → UAT → PROD.

**UAT auto-execute for low-risk.** As confidence grows, let UAT auto-execute low-risk types without approval. The [Risk Matrix](brain/autonomy_levels.md) already defines which types are safe to auto-execute per environment.

**Custom alerts for your org.** Add `.md` files for your org-specific monitoring. See [Adding Alerts](docs/adding-alerts.md) for the full walkthrough.

**LLM enablement.** If you started with template-only mode, add an API key to enable intelligent investigation. The cost gate means most alerts still use templates (zero cost); the LLM is reserved for novel situations.

**Team scaling.** More databases, more alert types, more DBAs on the approval rotation. Add databases to `sentri.yaml` — Sentri profiles and monitors them automatically.

---

## How to Back Out Entirely

If you decide Sentri isn't right for your environment at any phase, the uninstall is clean:

1. **Stop the service**: `systemctl stop sentri` (Linux) or stop the NSSM service (Windows)
2. **Revoke Oracle privileges**: `REVOKE ALTER TABLESPACE, ALTER SYSTEM FROM sentri_agent;` — remove whatever execution privileges you granted
3. **Optionally remove the sentri_agent Oracle user**: `DROP USER sentri_agent;`
4. **Remove the Sentri directory**: `rm -rf ~/.sentri/` (config, SQLite, logs, policies)
5. **Uninstall the package**: `pip uninstall sentri-dba`

**Your databases are completely unaffected.** Sentri stores its own operational data in SQLite, not in your Oracle databases. It creates no tables, triggers, jobs, or objects in your databases. The only Oracle artifact is the `sentri_agent` user (if you created one) and the privileges you granted.

**The audit trail survives in `~/.sentri/data/sentri.db`** until you delete it. If you want to keep the history for compliance, copy `sentri.db` before removing the directory.

At any phase, you can also just pause without uninstalling: set all databases to ADVISORY override and stop the service. Sentri is inert — it only acts when running and only executes when allowed by your autonomy settings.

---

## Appendix A: Oracle Privileges Required

Create a dedicated `sentri_agent` user. Grant privileges incrementally as you enable alert types.

**Always required** (read-only investigation):
- `SELECT` on `V$` views (V$SESSION, V$SQL, V$TABLESPACE, V$SYSTEM_EVENT, etc.)
- `SELECT` on `DBA_` views (DBA_TABLESPACES, DBA_DATA_FILES, DBA_TABLES, DBA_INDEXES, etc.)

**Per alert type** (execution):

| Alert Type | Required Privilege | Notes |
|-----------|-------------------|-------|
| `tablespace_full` | `ALTER TABLESPACE` | ADD DATAFILE / RESIZE |
| `temp_full` | `ALTER TABLESPACE` | ADD TEMPFILE |
| `archive_dest_full` | `ALTER SYSTEM` | Archive log management |
| `high_undo_usage` | `ALTER SYSTEM` | Undo retention parameter |
| `stale_stats` | `EXECUTE ON DBMS_STATS` | Non-destructive |
| `session_blocker` | `ALTER SYSTEM` | Kill session |
| `cpu_high` | `ALTER SYSTEM` | Kill session (if high-CPU SQL identified) |
| `long_running_sql` | `ALTER SYSTEM` | Kill session (if approved) |
| `listener_down` | N/A — requires OS access | Sentri cannot fix this; investigation only. Escalated to DBA. |
| `archive_gap` | `EXECUTE ON DBMS_BACKUP_RESTORE` or RMAN catalog access | Complex multi-scenario; may need RMAN privileges |

Start with read-only privileges for Phase 0 (Shadow Mode). Add execution privileges as you enable each alert type. When in doubt, grant read-only and let Sentri investigate and propose — you execute manually until you're comfortable granting the privilege.

## Appendix B: Key Files Reference

| File | Controls | When to Edit |
|------|----------|-------------|
| `config/sentri.yaml` | Database connections, email, LLM, notifications | Initial setup |
| `brain/autonomy_levels.md` | DEV/UAT/PROD default behavior, per-database overrides | Phase 0 overrides, ongoing tuning |
| `brain/rules.md` | Which actions need approval, time windows, freeze periods | Phase 2-3, match org policy |
| `brain/global_policy.md` | Safety rules, execution boundaries, escalation chain | Rarely — review, don't change lightly |
| `brain/routing_rules.md` | Which specialist handles which alert type | When adding new alert types |
| `alerts/tablespace_full.md` | Email regex, verification SQL, fix SQL, rollback SQL | Phase 0 — match your email format |
| `checks/stale_stats.md` | Health check query, schedule, threshold | Phase 2 — adjust schedule |
