# Reproducibility Guide — IEEE Access Submission

This document supports the reproducibility of results reported in:

> **Sentri: Structural Execution Control for LLM-Based Database Remediation**
> Sahil Soni, IEEE Access (submitted 2026)

## Quick Start

```bash
# Clone and set up
git clone https://github.com/whitepaper27/Sentri.git
cd Sentri
git checkout v5.0-ieee-access   # frozen submission tag

# Start Oracle XE 21c + Sentri
docker compose up -d

# Run all 108 evaluation runs (9 families × 3 envs × 3 backends)
python eval/run_eval.py --all

# Run 82-case adversarial benchmark
python eval/run_adversarial.py

# Run ablation study (Claude Opus 4, 36 runs per condition)
python eval/run_ablation.py
```

## Alert Families

All 9 alert families are defined as Markdown files in [`alerts/`](alerts/). Each file contains the alert definition, routing rules, and expected remediation pattern.

| Alert Family | File | How to Trigger | Expected Remediation |
|---|---|---|---|
| `tablespace_full` | `alerts/tablespace_full.md` | Seed script fills tablespace to >95% | `ALTER TABLESPACE ... ADD DATAFILE` |
| `temp_full` | `alerts/temp_full.md` | Seed script fills temp tablespace | Extend or add temp datafile |
| `archive_dest_full` | `alerts/archive_dest_full.md` | Archive log destination fills up | Manage archive logs / add space |
| `high_undo_usage` | `alerts/high_undo_usage.md` | Long-running transactions consume undo | Undo management / session analysis |
| `session_blocker` | `alerts/session_blocker.md` | Seed script creates blocking lock chain | Identify and resolve blocking sessions |
| `long_running_sql` | `alerts/long_running_sql.md` | Seed script runs expensive query | SQL tuning / plan analysis |
| `stale_stats` | `alerts/stale_stats.md` | Tables with outdated optimizer stats | `DBMS_STATS.GATHER_TABLE_STATS` |
| `invalid_objects` | `alerts/invalid_objects.md` | Seed script invalidates objects | `ALTER ... COMPILE` |
| `failed_jobs` | `alerts/failed_jobs.md` | Oracle Scheduler job failure | **0% across all methods** (Oracle XE does not seed Scheduler jobs — test harness limitation) |

## Seed Scripts

Each alert family has a corresponding seed script in `eval/seeds/` that creates the database condition that triggers the alert. These run automatically as part of `docker compose up`.

## Adversarial Benchmark

The 82 adversarial cases are in `eval/adversarial_cases/`. Each case is a JSON file specifying:
- Input alert content (crafted to probe a specific Safety Mesh check)
- Expected Safety Mesh check that should intercept
- Expected outcome (block / queue / suspend / escalate)

## Known vs. Novel Classification

For Table 8 in the paper (Known vs. Novel/Ambiguous incident results):

**Known/repetitive** (6 families, 18 runs): `tablespace_full`, `temp_full`, `archive_dest_full`, `stale_stats`, `invalid_objects`, `high_undo_usage`
— These have well-defined template-solvable remediation patterns.

**Novel/ambiguous** (2 families, 6 runs): `session_blocker`, `long_running_sql`
— These require contextual reasoning (blocking-chain analysis, SQL execution-plan interpretation) beyond template capabilities.

**Harness-limited** (1 family, 3 runs): `failed_jobs`
— 0% across all methods due to missing Oracle Scheduler job seeding in Docker XE.

## Raw Results

Raw results for each run are in `eval/results/`:
- `eval/results/full_108_runs.csv` — all 108 method-comparison runs
- `eval/results/ablation_36_runs.csv` — ablation study runs
- `eval/results/adversarial_82_cases.csv` — adversarial benchmark results
- `eval/results/known_novel_breakdown.csv` — per-family classification and counts

## Environment

- Oracle XE 21c (Docker: `gvenzl/oracle-xe:21-slim`)
- Python 3.10+
- LLM backends: Claude Opus 4 (`claude-opus-4`), GPT-4o (`gpt-4o-2024-11-20`), Gemini 1.5 Pro (`gemini-1.5-pro`)
- API keys required: set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`

## License

Apache 2.0. See [LICENSE](LICENSE).
