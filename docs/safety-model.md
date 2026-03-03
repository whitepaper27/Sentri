# Safety Model

Sentri is designed with the principle: **better no action than wrong action**. This document explains every safety mechanism and what Sentri cannot do.

---

## Safety as Architecture, Not Prompts

Most AI agent frameworks implement safety through prompt engineering — telling the LLM "don't do dangerous things." Sentri's safety is **structural**. The architecture itself prevents dangerous actions from reaching the database, regardless of what the LLM generates.

Even if the LLM hallucinates a `DROP TABLE` statement, it cannot reach execution. The Safety Mesh catches it structurally before it touches Oracle.

---

## The 5-Check Safety Mesh

Every fix from every specialist agent passes through the same Safety Mesh before execution. These are code-enforced checks, not LLM-evaluated guidelines.

### 1. Policy Gate

**Question**: Is this action allowed in this environment at this time?

Checks the autonomy level for the target database's environment:
- **DEV (AUTONOMOUS)**: Most actions auto-execute
- **UAT (SUPERVISED)**: Low-risk actions auto-execute, high-risk require approval
- **PROD (ADVISORY)**: All actions require human approval

Also enforces change windows and freeze periods if configured in `brain/global_policy.md`.

**Example**: A tablespace fix for PROD-DB-07 at confidence 0.95 → Policy Gate requires approval because environment is PROD.

### 2. Conflict Detection

**Question**: Is another fix already running on this database?

Prevents concurrent operations on the same database or resource. If another workflow is currently executing on PROD-DB-07, new fixes are queued — not executed simultaneously.

**Example**: Tablespace fix for USERS tablespace is executing. A second alert for TEMP tablespace arrives on the same DB → queued until the first completes.

### 3. Blast Radius Assessment

**Question**: How dangerous is this SQL?

Classifies the proposed SQL by blast radius:

| Classification | Examples | Treatment |
|---------------|----------|-----------|
| LOW | `ALTER TABLESPACE ADD DATAFILE`, `ALTER SYSTEM SET parameter` | Normal routing |
| MEDIUM | `ALTER INDEX REBUILD`, `DBMS_STATS.GATHER_TABLE_STATS` | Extra logging |
| HIGH | `ALTER TABLE MOVE`, DDL on large tables | Require approval regardless of environment |
| CRITICAL | `DROP`, `TRUNCATE`, `ALTER DATABASE` | Blocked. Escalate to DBA |

**Example**: LLM generates `DROP INDEX` as a candidate fix → classified as CRITICAL blast radius → blocked, never reaches execution.

### 4. Circuit Breaker

**Question**: Have too many recent fixes failed on this database?

If 3 or more fixes have failed on the same database in the last 24 hours, the circuit breaker trips. All new fixes for that database are blocked and escalated to a human DBA.

This prevents Sentri from repeatedly attempting fixes on a database that might have a deeper underlying problem.

**Example**: Two tablespace fixes and one stats gathering have failed on DEV-DB-01 today → circuit breaker trips → next alert is escalated instead of attempted.

### 5. Rollback Guarantee

**Question**: Can we undo this if it goes wrong?

Before executing any fix, the Safety Mesh verifies that a rollback action exists and is syntactically valid. For fixes classified as MEDIUM risk or above, if no rollback is available, execution is blocked.

LOW-risk actions (like adding a datafile) can proceed without rollback in DEV environments, since they're generally non-destructive.

**Example**: A fix to rebuild an index has a rollback action (rebuild with original parameters) → passes. A proposed fix with no rollback → blocked for anything above LOW risk.

---

## Confidence-Based Routing

The Auditor assigns a confidence score (0.0–1.0) to every verified alert. This score determines how much autonomy Sentri gets:

| Confidence | Behavior |
|------------|----------|
| Below 0.60 | **Escalated** — Sentri doesn't trust the alert enough to act. Human review required |
| 0.60–0.79 | **Cautious** — Pre-flight checks + approval required, even in DEV |
| 0.80–0.94 | **Normal** — Pre-flight checks, then routed by environment tier |
| 0.95+ | **High confidence** — Direct routing by environment tier |

Even at maximum confidence (1.0) on a PROD database, Sentri still requires human approval. Confidence routing only affects DEV and UAT behavior.

---

## Ground Truth SQL Validation

LLMs sometimes generate syntactically plausible but wrong Oracle SQL. Sentri prevents this with verified syntax docs:

- **Version-specific syntax**: Oracle 19c supports different ALTER TABLESPACE syntax than 12c. Sentri loads the correct reference for each database.
- **Hard rules**: BIGFILE tablespaces only support RESIZE (not ADD DATAFILE). OMF-managed databases don't use explicit file paths. CDB operations require container context.
- **Validation**: After the LLM generates SQL candidates, the SQLValidator checks each against the hard rules. Candidates that violate rules are silently dropped.

If all LLM-generated candidates fail validation, Sentri falls back to the template action from the alert `.md` file — or escalates if no template exists.

---

## Auto-Rollback

After executing a fix, Sentri runs the validation query from the alert `.md` file. If the validation shows the problem wasn't fixed (or got worse):

1. Sentri executes the pre-captured rollback SQL
2. The workflow is marked as `ROLLED_BACK`
3. An audit record is created
4. The DBA team is notified

This happens automatically — no human intervention required.

---

## Immutable Audit Trail

Every action Sentri takes is recorded in the `audit_log` table:

- **What** was proposed (the full SQL)
- **Why** it was proposed (investigation context, confidence score)
- **Whether** it was approved (and by whom)
- **What happened** (success, failure, rollback)
- **Before/after metrics** (pre-execution and post-execution database state)

The audit trail is append-only. Records are never modified or deleted. This supports regulatory compliance and post-incident review.

View the audit trail:
```bash
sentri audit
sentri audit --db PROD-DB-07
sentri audit --last 50
```

---

## Email Approval Flow

For fixes that require approval (all PROD fixes, high-risk UAT fixes, low-confidence fixes):

1. Sentri sends an email with the proposed fix, including the SQL, risk assessment, and investigation context
2. The email subject contains `[WF:xxxxxxxx]` — a unique workflow identifier
3. The DBA replies with `APPROVED` or `DENIED` (with optional reason)
4. Scout detects the reply and routes accordingly
5. An audit record captures who approved/denied and when

If no response is received within the configured timeout (default: 1 hour), the workflow is marked as `TIMEOUT` and the DBA team is notified.

You can also approve via CLI:
```bash
sentri approve <workflow_id>
sentri approve <workflow_id> --deny --reason "Not during batch window"
sentri resolve <workflow_id> --reason "Fixed manually by DBA"
```

---

## What Sentri Cannot Do

Being honest about limitations builds trust. Here's what Sentri does **not** handle:

| Limitation | Why |
|-----------|-----|
| **DDL on partitioned tables** | Too complex and version-specific. Escalated to DBA |
| **RAC-specific fixes** | Cross-instance coordination requires deep RAC expertise. Escalated |
| **Data Guard switchover/failover** | Too high risk for autonomous action. Escalated |
| **OS-level actions** | Sentri connects to Oracle, not the operating system. Can't restart listeners or move files |
| **Complex SQL rewrites** | Can suggest plan baselines or stats gathering, but won't rewrite application SQL |
| **Multi-database transactions** | Each fix targets a single database. Cross-database coordination is out of scope |
| **Encrypted/TDE tablespace operations** | Encryption key management requires DBA intervention |
| **Security/audit policy changes** | Too sensitive for autonomous action. Always escalated |

When Sentri encounters any of these, it escalates to the DBA team with its investigation findings — providing diagnostic value even when it can't fix the problem itself.

---

## Summary

| Layer | Protection |
|-------|-----------|
| **Ground Truth RAG** | Prevents wrong SQL from being generated |
| **SQLValidator** | Catches SQL that violates hard rules |
| **Safety Mesh** | 5 structural checks before any SQL reaches Oracle |
| **Confidence Routing** | Low-confidence alerts get extra scrutiny |
| **Auto-Rollback** | Failed fixes are automatically undone |
| **Circuit Breaker** | Repeated failures block further attempts |
| **Audit Trail** | Every action is permanently recorded |
| **Environment Tiers** | PROD always requires human approval |
