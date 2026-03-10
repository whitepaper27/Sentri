# Architecture Overview

Sentri is built as an agent network — specialized agents that detect, investigate, and fix database problems, coordinated by a deterministic supervisor and protected by a structural safety layer.

---

## System Architecture

```
  ┌──────────┐   ┌──────────────┐
  │  SCOUT   │   │  PROACTIVE   │
  │ (email)  │   │  (scheduled) │
  └────┬─────┘   └──────┬───────┘
       └───────┬────────┘
               v
  ┌──────────────────────────┐
  │       SUPERVISOR         │  Deterministic router
  │  (correlate + route)     │  (not an LLM call)
  └──────────┬───────────────┘
             |
    ┌────────┼──────────┬──────────┐
    v        v          v          v
 Storage   SQL       RCA       Future
 Agent     Tuning    Agent     Agent
           Agent
    └────────┼──────────┼──────────┘
             v
  ┌──────────────────────────┐
  │      SAFETY MESH         │  5 structural checks
  │  (policy, blast radius,  │  (LLM cannot bypass)
  │   conflicts, rollback)   │
  └──────────┬───────────────┘
             v
  ┌──────────────────────────┐
  │      EXECUTOR            │  Pre/post metrics
  │  (execute + rollback)    │  Auto-rollback on failure
  └──────────────────────────┘
```

---

## The 6 Core Agents

### Agent 0: Profiler (Database Discovery)

Runs 16 discovery queries against each Oracle database at startup. Captures database identity, version, parameters, storage layout, tablespace usage, SGA/PGA configuration, redo logs, archive settings, CDB/PDB topology, and workload profile. This profile data is stored in SQLite and used by all other agents as context for investigations and fix generation.

### Agent 1: Scout (Email Parser)

Monitors your IMAP inbox for alert emails. When a new email arrives, Scout matches it against all alert patterns defined in `alerts/*.md` using regex. If a match is found, Scout extracts the relevant fields (tablespace name, database ID, usage percentage, etc.) and creates a new workflow in `DETECTED` status. The patterns are dynamic — Scout rescans the `alerts/` directory on every poll cycle, so new `.md` files are picked up automatically.

### Agent 2: Auditor (Verifier)

Connects to the target Oracle database (read-only, 30-second timeout) and runs the verification query from the alert's `.md` file. Compares the actual metric against the reported value with configurable tolerance. If the actual value doesn't match (e.g., tablespace is no longer full), the alert is marked as a false positive. Outputs a confidence score (0.0–1.0) that drives downstream routing.

### Agent 3: Researcher (LLM-Powered Investigation)

Generates remediation SQL using a three-level fallback:

1. **Agentic** — LLM with 12 DBA investigation tools. The LLM can query the database, check execution plans, inspect session waits, and examine table statistics before generating a fix. Most thorough but most expensive.
2. **One-shot** — LLM with database profile context but no tool access. Single call, lower cost.
3. **Template** — Uses the forward action SQL directly from the alert's `.md` file. Zero LLM cost, deterministic.

The Researcher also loads verified Oracle syntax docs (ground truth) and recent memory context (what was tried before, what failed) into the LLM prompt.

### Agent 4: Executor (Safe Runner)

Executes fixes against Oracle databases with full safety guardrails. The execution sequence: acquire lock, validate rollback SQL exists, check database health, capture pre-execution metrics, execute the fix, capture post-execution metrics, run validation query, auto-rollback if validation fails. Every action is recorded in an immutable audit trail.

### Agent 5: Analyst (Learning Engine)

Observes outcomes from completed workflows and proposes improvements to Sentri's `.md` policy files. Uses a multi-judge LLM consensus — multiple LLM providers independently evaluate proposed changes, and changes are only applied if enough judges agree. Backs up files before any modification.

---

## The 4 Specialist Agents

Specialist agents implement a Universal Agent Contract — a 7-step process that every specialist follows:

```
1. verify    → Is this problem real right now?
2. investigate → What's actually going on? (use DBA tools)
3. propose   → Generate N candidate fixes
4. argue     → Score each candidate against criteria
5. select    → Pick the best (highest score, lowest risk)
6. execute   → Run through Safety Mesh
7. learn     → Record what happened
```

### Storage Agent

Handles tablespace full, temp tablespace full, archive destination full, and high undo usage alerts. This is the original Sentri pipeline (Auditor → Researcher → Executor) wrapped as a specialist. Uses storage-focused investigation tools: tablespace info, datafile layout, ASM disk groups.

### SQL Tuning Agent

Handles long-running SQL, high CPU, and stale statistics findings. Investigation flow: pull execution plan → check if the plan changed recently → check table stats freshness → look for missing indexes → examine session waits. Generates candidates like gather stats, create plan baseline, add index, or create SQL profile. Each candidate is scored against configurable weights (root cause fix, reversibility, side effects, execution time).

### RCA Agent (Root Cause Analysis)

Handles correlated alerts (multiple alerts on the same database in a short window) and session blocker alerts. Uses a tiered investigation approach:

- **Tier 1: Quick Triage** (always runs) — active sessions, top waits, top SQL. If the cause is obvious, proceed directly.
- **Tier 2: Focused Deep-Dive** (if Tier 1 inconclusive) — targeted investigation in the area Tier 1 flagged.
- **Tier 3: Full Snapshot** (rare, requires DBA approval on PROD) — system-wide health capture.

Fixes are applied in theory-ranked order. After each fix, the RCA Agent re-verifies. If the root issue is resolved, it stops.


### SQL Tuning Agent — Strengths and Pitfalls (L3 DBA View)

**Strengths**
- Strong for repeatable tuning hygiene: stale stats, obvious plan regressions, missing-index candidates, and wait-profile driven triage.
- Candidate scoring (argue/select) reduces single-shot bad fixes by comparing options before execution.
- Safety Mesh + rollback requirements reduce blast radius of tuning actions.

**Pitfalls / Watch-outs**
- Not a replacement for deep application SQL redesign; it can optimize around symptoms but cannot fully re-architect workload patterns.
- Complex bind peeking / adaptive plan edge cases may still require manual DBA investigation.
- If historical success is high, the cost gate may favor template/one-shot paths; review periodically to ensure drift is not hidden.

### RCA Agent — Strengths and Pitfalls (L3 DBA View)

**Strengths**
- Good incident commander behavior for correlated alerts across a short window.
- Tiered triage model helps keep normal incidents fast while preserving a path to deeper analysis.
- Stops early when re-verification shows the root issue is resolved, reducing unnecessary extra actions.

**Pitfalls / Watch-outs**
- Correlation windows can be too narrow or too broad for your environment; tune rules to avoid false grouping.
- Tier escalation criteria need calibration per estate (OLTP vs batch-heavy systems).
- On RAC/Data Guard-heavy estates, human DBA review remains critical for cross-instance/root-cause validation.

### L3 DBA Daily Tasks Using Sentri

1. Check `sentri stats` for error spikes and success-rate drift.
2. Review `sentri list --status AWAITING_APPROVAL` and clear approval queue.
3. Sample `sentri audit --last 50` for policy misses or repeated rollback patterns.
4. Review top recurring alert types weekly and tune the corresponding `.md` alert/check files.
5. Validate that PROD autonomy and approval recipients still match current on-call rota.

### Proactive Agent

Runs scheduled health checks defined in `checks/*.md`. Instead of waiting for alert emails, it proactively queries databases on a schedule (every 6 hours, daily, or weekly) looking for emerging problems. Findings enter the Supervisor exactly like email alerts — same workflow, same state machine, same safety. Ships with 7 built-in checks: stale statistics, tablespace trends, index usage, redo log sizing, temp growth, password expiry, and backup freshness.

---

## Supervisor (Deterministic Router)

The Supervisor is not an LLM call. It's deterministic routing logic driven by `brain/routing_rules.md`:

- **Direct routing**: Each alert type maps to a specialist (`tablespace_full` → Storage Agent, `long_running_sql` → SQL Tuning Agent)
- **Correlation**: If 2+ alerts arrive from the same alert category on the same database within 5 minutes, they're grouped as a single incident and routed to the RCA Agent
- **Fallback**: Unknown alert types default to the Storage Agent

The Supervisor saves LLM budget for where it matters — inside the specialists' argue/select step.

---

## The Argue/Select Pattern

When a specialist investigates a problem, it doesn't generate one fix and run it. Instead:

1. The LLM generates 3–5 candidate fixes
2. A separate LLM judge scores each candidate against configurable criteria defined in the agent's `.md` file
3. The candidate with the best score (considering root cause fix, reversibility, side effect risk, execution time, and historical success) is selected

This prevents the "first answer wins" problem common in LLM systems.

### Cost Gate

The argue/select step is powerful but expensive. A cost gate checks historical success rate before invoking it:

| Success Rate (last 90 days) | Action | LLM Calls |
|------------------------------|--------|-----------|
| 95%+ for this alert type + database | Template (skip LLM) | 0 |
| 80–95% | One-shot LLM | 1 |
| Below 80% or novel (no history) | Full argue/select | 2+ |

Most alerts on a stable database estate hit the template fast path — zero LLM cost.

---

## 12 DBA Investigation Tools

Specialist agents investigate problems using these read-only tools (all with 10-second timeout, 50-row limit):

| Tool | What It Does |
|------|-------------|
| `get_tablespace_info` | Tablespace type (BIGFILE), status, usage%, datafiles |
| `get_db_parameters` | Any init parameter (OMF, undo, SGA, etc.) |
| `get_storage_info` | Datafile paths, sizes, autoextend settings |
| `get_instance_info` | Version, RAC, CDB/PDB, Data Guard role |
| `query_database` | Any SELECT query (DML/DDL blocked by safety guard) |
| `get_sql_plan` | Execution plan for a SQL_ID |
| `get_sql_stats` | Performance stats + per-child breakdown + bind capture |
| `get_table_stats` | Optimizer stats, staleness, partitioning info |
| `get_index_info` | Index definitions, columns, clustering factor, usage |
| `get_session_info` | Session diagnostics: current SQL, wait event, blocking chain |
| `get_top_sql` | Top N SQL by CPU, elapsed time, buffer gets, disk reads |
| `get_wait_events` | System-wide wait analysis + active session waits |

---

## Ground Truth RAG

LLMs sometimes generate wrong SQL (e.g., `ADD DATAFILE` on a bigfile tablespace, which only supports `RESIZE`). Sentri prevents this with verified Oracle syntax docs:

- `docs/oracle/{version}/{category}/*.md` — verified SQL patterns per Oracle version
- `docs/oracle/rules/*.md` — hard rules (BIGFILE = RESIZE only, OMF = no explicit paths, CDB = set container context)

The RagManager loads version-appropriate docs into the LLM prompt. After the LLM generates SQL, the SQLValidator checks each statement against hard rules. Violations are dropped before they reach execution.

This is not vector search. It's file read + string matching — simple, fast, and debuggable.

---

## Memory System

### Short-Term Memory (24 hours)

Sentri queries its audit log for recent actions on the same database and alert type. The LLM sees what was tried in the last 24 hours and avoids repeating the same fix. If the same alert fires twice in 24 hours, it suggests a larger action or escalation.

### Long-Term Memory (90 days)

Sentri queries 90 days of workflow history grouped by alert type, showing dates, days of week, and outcomes. The LLM can spot patterns like "this happens every Friday" or "every 14 days" and factor that into its investigation.

---

## Workflow State Machine

Every workflow (whether from an email alert or a proactive finding) follows the same 14-state lifecycle:

```
DETECTED → VERIFYING → VERIFIED → PRE_FLIGHT → EXECUTING → COMPLETED
                    ↘ VERIFICATION_FAILED           ↘ FAILED → ROLLED_BACK
              VERIFIED → AWAITING_APPROVAL → APPROVED → EXECUTING
                                          ↘ DENIED / TIMEOUT
              Any active state → ESCALATED (terminal)
```

### Confidence-Based Routing

| Confidence | Routing |
|------------|---------|
| Below 0.60 | Escalated to human review |
| 0.60–0.79 | Pre-flight + approval required (even in DEV) |
| 0.80–0.94 | Pre-flight, then routed by environment |
| 0.95+ | Direct routing by environment tier |
