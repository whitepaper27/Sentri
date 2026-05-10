# Reproducibility Package — Sentri IEEE Access Paper

This document describes how to reproduce the evaluation results reported in:

> **"Sentri: A Safety-Mesh Architecture for Autonomous Database Remediation"**  
> IEEE Access, 2026 — Submission ID: [TBD]

---

## Quick Start

All published results are pre-computed in `eval/results/`. To verify the paper numbers without re-running the full evaluation:

```bash
python eval/aggregate_results.py
```

This regenerates `eval/results/ieee_summary.md` and all three IEEE table `.txt` files from the raw run data.

---

## Pre-Computed Results (Instant Verification)

| File | Contents | Paper Table |
|------|----------|-------------|
| `eval/results/raw_results.csv` | 108 runs (3 providers × 36 each) with per-run outcomes, cost, latency | Table I |
| `eval/results/known_novel_results.csv` | Claude runs broken down by known vs novel alert families | Table II |
| `eval/results/ablation_results.csv` | 216 rows — full system + 5 ablation conditions × 36 runs each | Table III |
| `eval/results/adversarial_cases.csv` | All 82 adversarial cases with expected/actual/blocked_by fields | Table IV |

CSV column meanings:
- `outcome`: `COMPLETED` (auto-executed), `NEEDS_APPROVAL` (routed to human), `FAILED`, `UNKNOWN` (harness error)
- `safety_mesh_verdict`: raw Safety Mesh decision before environment routing
- `unsafe_sql_reached_exec`: 1 if dangerous DDL auto-executed without approval (0 for all Sentri full-system runs)
- `remediation_accuracy` = (COMPLETED + NEEDS_APPROVAL) / total

---

## Full Re-Run (Requires API Keys + Oracle)

### 1. Start Oracle Database

```bash
docker compose -f docker/docker-compose.yml up -d
# Wait ~60 seconds for Oracle to initialize
docker compose -f docker/docker-compose.yml ps   # verify "healthy"
```

### 2. Install Dependencies

```bash
pip install -e ".[dev]"
```

### 3. Initialize Sentri

```bash
sentri init
```

### 4. Set API Keys

```bash
export ANTHROPIC_API_KEY=<your-key>
export OPENAI_API_KEY=<your-key>
export GEMINI_API_KEY=<your-key>
```

### 5. Run the Full Evaluation

```bash
# Block 1: Scale evaluation (108 runs across 3 providers)
python eval/scale_eval.py --provider claude
python eval/scale_eval.py --provider openai
python eval/scale_eval.py --provider gemini

# Block 2B: Template baseline
python eval/scale_eval.py --template

# Block 3: Ablation study (all 5 conditions, Claude only)
python eval/scale_eval.py --all-ablations

# Block 4: Adversarial benchmark (82 cases)
python eval/run_eval.py --part adversarial --extended --seed 42

# Aggregate all results → regenerate IEEE tables
python eval/aggregate_results.py
```

Or run everything with the unified runner:

```bash
python eval/run_eval.py --part all --extended --seed 42
```

### 6. Verify Against Paper

```bash
python eval/aggregate_results.py --verbose
```

Expected output matches:
- Table I: Claude 81% / GPT-4o 100% / Gemini 61% remediation accuracy, 0% unsafe SQL for all
- Table III: All 5 ablation conditions 89% accuracy, Safety Mesh ablation 0% unsafe → rest 0%
- Table IV: 82/82 adversarial cases passed (100%)

---

## Alert Families Tested

| Family | Category | Environments | Runs |
|--------|----------|-------------|------|
| `tablespace_full` | Known (in arXiv v1) | DEV / UAT / PROD + repeat | 4 |
| `temp_full` | Known | DEV / UAT / PROD + repeat | 4 |
| `archive_dest_full` | Known | DEV / UAT / PROD | 3 |
| `high_undo_usage` | Known | DEV / UAT / PROD | 3 |
| `session_blocker` | Novel | DEV / UAT / PROD | 3 |
| `long_running_sql` | Novel | DEV / UAT / PROD | 3 |
| `stale_stats` | Novel (proactive) | DEV / UAT / PROD | 3 |
| `invalid_objects` | Novel | DEV / UAT / PROD | 3 |
| `failed_jobs` | Novel (harness-limited) | DEV / UAT / PROD | 3 |

Harness-limited note: `failed_jobs` in PROD/UAT uses mock job failure injection; the Oracle scheduler fix SQL is exercised but actual job failure outcome depends on workload.

---

## Adversarial Benchmark

The 82 adversarial cases are designed to test each Safety Mesh check independently.

| Safety Check | Cases | Decisions Tested |
|---|---|---|
| `blast_radius` | 36 | ALLOW, REQUIRE_APPROVAL |
| `policy_gate` | 15 | ALLOW, BLOCK, REQUIRE_APPROVAL |
| `rollback_check` | 13 | ALLOW, BLOCK, REQUIRE_APPROVAL |
| `conflict_detect` | 10 | ALLOW, QUEUE, REQUIRE_APPROVAL |
| `circuit_breaker` | 6 | ALLOW, BLOCK |
| `combined` | 2 | BLOCK |

All 82 cases are deterministic (no LLM call) — Safety Mesh enforcement is structural. Re-running produces identical results on any commit at or after `v5.0-ieee-access`.

---

## Seed and Randomness

- Adversarial cases: fully deterministic (no randomness)
- Scale evaluation: LLM temperature = 0 for all providers; run order is fixed by `scale_eval.py` scenario list
- `--seed 42` passed to `run_eval.py` affects any sampling in the harness (not Safety Mesh decisions)

---

## Environment

| Component | Version |
|---|---|
| Python | 3.12 |
| Oracle | XE 21c (via `gvenzl/oracle-xe:21-slim`) |
| Claude | `claude-opus-4` (Anthropic API) |
| GPT-4o | `gpt-4o-2024-08-06` (OpenAI API) |
| Gemini | `gemini-1.5-pro-latest` (Google AI API) |
| Sentri | `v5.0-ieee-access` (this tag) |

---

## File Layout

```
eval/
├── run_eval.py              # Unified evaluation runner
├── scale_eval.py            # Block 1/2/3 scale + ablation harness
├── adversarial.py           # Block 4 adversarial harness
├── harness.py               # Core test harness
├── runner.py                # Per-scenario runner
├── formatter.py             # Output formatting
├── aggregate_results.py     # Aggregate raw JSON → IEEE tables
└── results/
    ├── raw_results.csv              # 108 run records (Table I source)
    ├── known_novel_results.csv      # Claude only, known vs novel (Table II)
    ├── ablation_results.csv         # 6 conditions × 36 runs (Table III)
    ├── adversarial_cases.csv        # 82 cases (Table IV)
    ├── scale_claude_none.json       # Raw: Claude full system (36 runs)
    ├── scale_openai_none.json       # Raw: GPT-4o full system (36 runs)
    ├── scale_gemini_none.json       # Raw: Gemini full system (36 runs)
    ├── scale_claude_no_*.json       # Raw: ablation runs (5 files)
    ├── adversarial_results_82cases.json  # Raw adversarial results
    └── ieee_summary.md              # Aggregated paper-ready tables

docker/
└── docker-compose.yml       # Oracle XE test database
```

---

## Contact

For questions about reproducing results, open an issue on GitHub or email the corresponding author.
