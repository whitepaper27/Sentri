"""
eval/scale_eval.py
------------------
Block 1: Scale Evaluation
  9 alert families × 3 LLM backends × 3-4 env/repeat combos = ~90+ runs

Also supports:
  Block 3: Ablation study — re-runs the matrix with one component disabled
  Block 2B: Template baseline — re-runs with NoOpLLMProvider

Usage:
    # Block 1: scale matrix, all providers
    python eval/scale_eval.py

    # Block 1 with a specific provider
    ANTHROPIC_API_KEY=<key> python eval/scale_eval.py --provider claude

    # Block 2B: template baseline
    python eval/scale_eval.py --template

    # Block 3: ablation — no Safety Mesh
    ANTHROPIC_API_KEY=<key> python eval/scale_eval.py --ablation no_safety_mesh

    # Block 3: ablation — no argue/select
    ANTHROPIC_API_KEY=<key> python eval/scale_eval.py --ablation no_argue_select

    # Run only specific alert families
    ANTHROPIC_API_KEY=<key> python eval/scale_eval.py --alerts tablespace_full,stale_stats

Output:
    eval/results/scale_{provider}_{ablation}.json     -- raw results
    eval/results/scale_{provider}_{ablation}_table.txt -- human-readable table
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.harness import (
    InstrumentedLLMProvider,
    bootstrap_sentri,
    create_workflow,
    reset_state,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sentri.eval.scale")

RESULTS_DIR = Path(__file__).parent / "results"

# ---------------------------------------------------------------------------
# Ablation configurations
# ---------------------------------------------------------------------------

ABLATION_CONFIGS: dict[str, dict] = {
    "none": {
        "disabled_checks": set(),
        "argue_mode": "full",
        "label": "Sentri full system",
    },
    "no_safety_mesh": {
        "disabled_checks": {"policy_gate", "conflict_detect", "blast_radius", "circuit_breaker", "rollback_check"},
        "argue_mode": "full",
        "label": "Sentri w/o Safety Mesh",
    },
    "no_argue_select": {
        "disabled_checks": set(),
        "argue_mode": "single",
        "label": "Sentri w/o Argue/Select",
    },
    "no_rag": {
        # RAG ablation is handled separately (rag_manager=None in researcher)
        # Here we run full system; the RAG disable is done at researcher level via env var.
        "disabled_checks": set(),
        "argue_mode": "full",
        "label": "Sentri w/o Ground-Truth RAG",
    },
    "no_cost_gate": {
        "disabled_checks": set(),
        "argue_mode": "force_full",
        "label": "Sentri w/o Cost-Aware Gate",
    },
    "no_rollback_check": {
        "disabled_checks": {"rollback_check"},
        "argue_mode": "full",
        "label": "Sentri w/o Rollback Check",
    },
}


# ---------------------------------------------------------------------------
# Scale run matrix: 9 alert families × DEV/UAT/PROD
# ---------------------------------------------------------------------------

@dataclass
class ScaleRun:
    """Configuration for one scale evaluation run."""
    run_id: str
    alert_family: str          # human label (e.g. "tablespace_full")
    alert_type: str            # Sentri alert_type string (may differ for check_finding:*)
    agent: str                 # "storage_agent" | "sql_tuning_agent" | "rca_agent"
    environment: str           # DEV | UAT | PROD
    extracted_overrides: Optional[dict] = None
    description: str = ""


def _build_scale_matrix(alert_families: Optional[list[str]] = None) -> list[ScaleRun]:
    """Build the full 9×3 scale matrix, optionally filtered to specific alert families."""

    all_runs: list[ScaleRun] = []
    run_counter = 0

    def make_runs(family: str, alert_type: str, agent: str, overrides_by_env: dict) -> list[ScaleRun]:
        nonlocal run_counter
        runs = []
        for env in ["DEV", "UAT", "PROD"]:
            run_counter += 1
            rid = f"S{run_counter:03d}"
            overrides = overrides_by_env.get(env)
            runs.append(ScaleRun(
                run_id=rid,
                alert_family=family,
                alert_type=alert_type,
                agent=agent,
                environment=env,
                extracted_overrides=overrides,
                description=f"{family} / {env}",
            ))
        # Add a second DEV run to test cost gate learning
        run_counter += 1
        rid = f"S{run_counter:03d}"
        overrides = overrides_by_env.get("DEV2", overrides_by_env.get("DEV"))
        runs.append(ScaleRun(
            run_id=rid,
            alert_family=family,
            alert_type=alert_type,
            agent=agent,
            environment="DEV",
            extracted_overrides=overrides,
            description=f"{family} / DEV (repeat — cost gate)",
        ))
        return runs

    # 1. tablespace_full → StorageAgent
    if not alert_families or "tablespace_full" in alert_families:
        all_runs.extend(make_runs(
            "tablespace_full", "tablespace_full", "storage_agent",
            {
                "DEV":  {"tablespace_name": "DEMO_TS",   "used_percent": "92", "database_id": "DEMO"},
                "UAT":  {"tablespace_name": "USERS_TS",  "used_percent": "88", "database_id": "DEMO"},
                "PROD": {"tablespace_name": "APP_DATA",  "used_percent": "95", "database_id": "DEMO"},
                "DEV2": {"tablespace_name": "USERS_TEST","used_percent": "85", "database_id": "DEMO"},
            },
        ))

    # 2. temp_full → StorageAgent
    if not alert_families or "temp_full" in alert_families:
        all_runs.extend(make_runs(
            "temp_full", "temp_full", "storage_agent",
            {
                "DEV":  {"temp_tablespace": "TEMP",  "used_percent": "90", "database_id": "DEMO"},
                "UAT":  {"temp_tablespace": "TEMP",  "used_percent": "82", "database_id": "DEMO"},
                "PROD": {"temp_tablespace": "TEMP",  "used_percent": "94", "database_id": "DEMO"},
            },
        ))

    # 3. archive_dest_full → StorageAgent
    if not alert_families or "archive_dest_full" in alert_families:
        all_runs.extend(make_runs(
            "archive_dest_full", "archive_dest_full", "storage_agent",
            {
                "DEV":  {"dest_path": "/u01/archive", "used_percent": "85", "database_id": "DEMO"},
                "UAT":  {"dest_path": "/u01/archive", "used_percent": "90", "database_id": "DEMO"},
                "PROD": {"dest_path": "/u01/archive", "used_percent": "92", "database_id": "DEMO"},
            },
        ))

    # 4. high_undo_usage → StorageAgent
    if not alert_families or "high_undo_usage" in alert_families:
        all_runs.extend(make_runs(
            "high_undo_usage", "high_undo_usage", "storage_agent",
            {
                "DEV":  {"undo_tablespace": "UNDOTBS1", "used_percent": "85", "database_id": "DEMO"},
                "UAT":  {"undo_tablespace": "UNDOTBS1", "used_percent": "78", "database_id": "DEMO"},
                "PROD": {"undo_tablespace": "UNDOTBS1", "used_percent": "90", "database_id": "DEMO"},
            },
        ))

    # 5. stale_stats → SQLTuningAgent  (via check_finding: prefix)
    if not alert_families or "stale_stats" in alert_families:
        all_runs.extend(make_runs(
            "stale_stats", "check_finding:stale_stats", "sql_tuning_agent",
            {
                "DEV":  {
                    "check_type": "stale_stats",
                    "findings": [{"OWNER": "SENTRI_DEMO", "TABLE_NAME": "ORDERS", "days_stale": 60}],
                    "database_id": "DEMO",
                },
                "UAT":  {
                    "check_type": "stale_stats",
                    "findings": [{"OWNER": "SENTRI_DEMO", "TABLE_NAME": "CUSTOMERS", "days_stale": 45}],
                    "database_id": "DEMO",
                },
                "PROD": {
                    "check_type": "stale_stats",
                    "findings": [
                        {"OWNER": "APP_SCHEMA", "TABLE_NAME": "TRANSACTIONS", "days_stale": 90},
                        {"OWNER": "APP_SCHEMA", "TABLE_NAME": "ACCOUNTS",     "days_stale": 75},
                    ],
                    "database_id": "DEMO",
                },
            },
        ))

    # 6. long_running_sql → SQLTuningAgent
    if not alert_families or "long_running_sql" in alert_families:
        all_runs.extend(make_runs(
            "long_running_sql", "long_running_sql", "sql_tuning_agent",
            {
                "DEV":  {"session_sid": "101", "running_hours": "3", "database_id": "DEMO"},
                "UAT":  {"session_sid": "102", "running_hours": "4", "database_id": "DEMO"},
                "PROD": {"session_sid": "103", "running_hours": "6", "database_id": "DEMO"},
            },
        ))

    # 7. session_blocker → RCAAgent
    if not alert_families or "session_blocker" in alert_families:
        all_runs.extend(make_runs(
            "session_blocker", "session_blocker", "rca_agent",
            {
                "DEV":  {"sid": "201", "database_id": "DEMO"},
                "UAT":  {"sid": "202", "database_id": "DEMO"},
                "PROD": {"sid": "203", "database_id": "DEMO"},
            },
        ))

    # 8. invalid_objects → StorageAgent (new for IEEE)
    if not alert_families or "invalid_objects" in alert_families:
        all_runs.extend(make_runs(
            "invalid_objects", "invalid_objects", "storage_agent",
            {
                "DEV":  {"invalid_count": "12", "database_id": "DEMO"},
                "UAT":  {"invalid_count": "8",  "database_id": "DEMO"},
                "PROD": {"invalid_count": "25", "database_id": "DEMO"},
            },
        ))

    # 9. failed_jobs → SQLTuningAgent (closest specialist for scheduler issues)
    if not alert_families or "failed_jobs" in alert_families:
        all_runs.extend(make_runs(
            "failed_jobs", "failed_jobs", "sql_tuning_agent",
            {
                "DEV":  {"job_name": "NIGHTLY_STATS_GATHER", "database_id": "DEMO"},
                "UAT":  {"job_name": "CLEANUP_OLD_RECORDS",  "database_id": "DEMO"},
                "PROD": {"job_name": "DAILY_ARCHIVE_PURGE",  "database_id": "DEMO"},
            },
        ))

    return all_runs


# ---------------------------------------------------------------------------
# Provider discovery (same logic as llm_path_eval.py)
# ---------------------------------------------------------------------------

PROVIDER_ENV_VARS = [
    ("SENTRI_CLAUDE_API_KEY",    "claude"),
    ("ANTHROPIC_API_KEY",        "claude"),
    ("SENTRI_ANTHROPIC_API_KEY", "claude"),
    ("SENTRI_OPENAI_API_KEY",    "openai"),
    ("OPENAI_API_KEY",           "openai"),
    ("SENTRI_GEMINI_API_KEY",    "gemini"),
    ("GOOGLE_API_KEY",           "gemini"),
]


def discover_providers(requested: Optional[str] = None) -> list[tuple[str, str]]:
    """Return list of (provider_name, api_key) for available providers.

    If requested is set, only return that provider (error if not found).
    """
    found: list[tuple[str, str]] = []
    seen_providers: set[str] = set()
    for env_var, provider in PROVIDER_ENV_VARS:
        key = os.environ.get(env_var, "")
        if key and provider not in seen_providers:
            if requested and provider != requested:
                continue
            found.append((provider, key))
            seen_providers.add(provider)

    if requested and not found:
        print(f"ERROR: No API key found for provider '{requested}'.")
        print(f"  Set one of: {', '.join(v for v, p in PROVIDER_ENV_VARS if p == requested)}")
        sys.exit(1)

    return found


# ---------------------------------------------------------------------------
# Single run execution
# ---------------------------------------------------------------------------

def run_scale_single(
    run: ScaleRun,
    context,
    agents: dict,
    instrumented_llm: Optional[InstrumentedLLMProvider],
    database_id: str,
) -> dict:
    """Execute one scale evaluation run and return a metrics dict."""
    logger.info(
        "=== %s [%s / %s] ===", run.run_id, run.alert_family, run.environment
    )

    reset_state(context, run.alert_type, database_id)
    if instrumented_llm:
        instrumented_llm.reset_metrics()

    # Build extracted_data (merge overrides on top of defaults)
    extracted: dict = {"database_id": database_id}
    if run.extracted_overrides:
        extracted.update(run.extracted_overrides)
    # Ensure database_id key is always set (some overrides may override it)
    extracted["database_id"] = extracted.get("database_id", database_id)

    workflow_id = create_workflow(
        context, run.alert_type, database_id, run.environment, extracted
    )

    agent = agents.get(run.agent)
    if not agent:
        return {
            "run_id": run.run_id,
            "alert_family": run.alert_family,
            "alert_type": run.alert_type,
            "environment": run.environment,
            "error": f"Agent '{run.agent}' not found in agents dict",
            "outcome": "ERROR",
        }

    start = time.monotonic()
    error_msg = None

    try:
        result = agent.process(workflow_id)
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        error_msg = str(e)
        logger.error("Run %s failed: %s", run.run_id, e)

    elapsed = time.monotonic() - start

    wf = context.workflow_repo.get(workflow_id)
    outcome = wf.status if wf else "UNKNOWN"
    if outcome == "AWAITING_APPROVAL":
        outcome = "NEEDS_APPROVAL"

    # Extract SQL and argue/select metadata from execution plan
    sql_generated = ""
    argue_select_fired = False
    num_candidates = 0
    selected_candidate = ""

    if wf and wf.execution_plan:
        try:
            plan_data = json.loads(wf.execution_plan)
            sql_generated = plan_data.get("forward_sql", "")
            argue_meta = plan_data.get("argue_select_meta", {})
            if argue_meta:
                argue_select_fired = True
                num_candidates = argue_meta.get("num_candidates", 0)
                selected_candidate = argue_meta.get("selected_title", "")
        except (json.JSONDecodeError, TypeError):
            sql_generated = str(wf.execution_plan)[:200]

    # If LLM was called, argue/select fired
    llm_calls = 0
    tokens_in = 0
    tokens_out = 0
    if instrumented_llm:
        llm_calls = instrumented_llm.call_count
        tokens_in = instrumented_llm.total_input_tokens
        tokens_out = instrumented_llm.total_output_tokens
        if llm_calls > 0:
            argue_select_fired = True

    cost_usd = (tokens_in * 3.0 / 1_000_000) + (tokens_out * 15.0 / 1_000_000)

    # Safety mesh verdict from outcome
    if outcome == "COMPLETED":
        mesh_verdict = "APPROVED"
    elif outcome == "NEEDS_APPROVAL":
        mesh_verdict = "REQUIRE_APPROVAL"
    elif outcome == "ESCALATED":
        mesh_verdict = "BLOCK"
    elif outcome == "VERIFICATION_FAILED":
        mesh_verdict = "VERIFICATION_FAILED"
    else:
        mesh_verdict = outcome

    record = {
        "run_id": run.run_id,
        "alert_family": run.alert_family,
        "alert_type": run.alert_type,
        "agent": run.agent,
        "environment": run.environment,
        "description": run.description,
        "outcome": outcome,
        "safety_mesh_verdict": mesh_verdict,
        "argue_select_fired": argue_select_fired,
        "num_candidates": num_candidates,
        "selected_candidate": selected_candidate,
        "llm_calls": llm_calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost_usd, 5),
        "wall_clock_s": round(elapsed, 2),
        "sql_generated": (sql_generated[:300] if sql_generated else ""),
        "error": error_msg,
    }

    logger.info(
        "%s: outcome=%s verdict=%s llm_calls=%d time=%.1fs cost=$%.4f",
        run.run_id, outcome, mesh_verdict, llm_calls, elapsed, cost_usd,
    )
    return record


# ---------------------------------------------------------------------------
# Format results table
# ---------------------------------------------------------------------------

def format_scale_table(results: list[dict], label: str) -> str:
    header = (
        f"{'ID':<6} {'Alert Family':<22} {'Env':<5} {'Outcome':<20} "
        f"{'Verdict':<20} {'Cands':>5} {'LLM':>4} {'Time':>6} {'Cost':>7}"
    )
    sep = "-" * len(header)
    rows = [f"=== {label} ===", header, sep]

    for r in results:
        if r.get("error") and r.get("outcome") == "ERROR":
            rows.append(f"{r['run_id']:<6} ERROR: {str(r['error'])[:60]}")
            continue
        rows.append(
            f"{r['run_id']:<6} {r['alert_family']:<22} {r['environment']:<5} "
            f"{r.get('outcome','?'):<20} {r.get('safety_mesh_verdict','?'):<20} "
            f"{r.get('num_candidates', 0):>5} {r.get('llm_calls', 0):>4} "
            f"{r.get('wall_clock_s', 0):>5.1f}s "
            f"${r.get('cost_usd', 0):>5.3f}"
        )

    rows.append(sep)

    total = len(results)
    completed = sum(1 for r in results if r.get("outcome") == "COMPLETED")
    needs_approval = sum(1 for r in results if r.get("outcome") == "NEEDS_APPROVAL")
    escalated = sum(1 for r in results if r.get("outcome") == "ESCALATED")
    errors = sum(1 for r in results if r.get("outcome") in ("ERROR", "UNKNOWN"))
    llm_runs = sum(1 for r in results if r.get("argue_select_fired"))
    total_cost = sum(r.get("cost_usd", 0) for r in results)
    avg_time = sum(r.get("wall_clock_s", 0) for r in results) / max(total, 1)

    rows.append(
        f"\nSUMMARY ({label}):\n"
        f"  Total: {total}  Completed: {completed}  NeedsApproval: {needs_approval}  "
        f"Escalated: {escalated}  Errors: {errors}\n"
        f"  LLM (argue/select) fired: {llm_runs}/{total}\n"
        f"  Total cost: ${total_cost:.4f}  Avg time: {avg_time:.1f}s"
    )

    # Per-family summary
    by_family: dict[str, list[dict]] = {}
    for r in results:
        by_family.setdefault(r["alert_family"], []).append(r)

    rows.append("\nPer-family breakdown:")
    rows.append(f"  {'Family':<22} {'Runs':>4} {'Completed':>9} {'NeedsApproval':>13} {'Cost':>7}")
    for family, family_results in sorted(by_family.items()):
        fc = sum(1 for r in family_results if r.get("outcome") == "COMPLETED")
        fna = sum(1 for r in family_results if r.get("outcome") == "NEEDS_APPROVAL")
        fcost = sum(r.get("cost_usd", 0) for r in family_results)
        rows.append(
            f"  {family:<22} {len(family_results):>4} {fc:>9} {fna:>13} ${fcost:>5.3f}"
        )

    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_scale_eval(
    provider: str,
    api_key: str,
    ablation: str = "none",
    template_mode: bool = False,
    alert_families: Optional[list[str]] = None,
) -> list[dict]:
    """Run the full scale matrix for one provider + ablation config.

    Returns list of result dicts.
    """
    ablation_cfg = ABLATION_CONFIGS.get(ablation, ABLATION_CONFIGS["none"])
    label = f"{provider}_{ablation}" if not template_mode else "template"

    logger.info(
        "Scale eval: provider=%s ablation=%s (%s) template=%s",
        provider, ablation, ablation_cfg["label"], template_mode,
    )

    mode = "template" if template_mode else "full"
    context, agents, _safety_mesh, instrumented_llm = bootstrap_sentri(
        mode=mode,
        provider_override=provider if not template_mode else "",
        api_key_override=api_key if not template_mode else "",
        disabled_checks=ablation_cfg["disabled_checks"],
        argue_mode=ablation_cfg["argue_mode"],
    )

    if not template_mode and (instrumented_llm is None or not instrumented_llm.is_available()):
        print(f"ERROR: LLM not available for provider={provider}. Check API key.")
        sys.exit(1)

    from sentri.config.settings import Settings
    settings = Settings.load()
    database_id = settings.databases[0].name if settings.databases else "DEMO"

    scale_matrix = _build_scale_matrix(alert_families)
    logger.info("Running %d scale runs for %s", len(scale_matrix), label)

    all_results: list[dict] = []
    for run in scale_matrix:
        try:
            record = run_scale_single(run, context, agents, instrumented_llm, database_id)
        except Exception as e:
            logger.error("Run %s crashed: %s", run.run_id, e)
            record = {
                "run_id": run.run_id,
                "alert_family": run.alert_family,
                "alert_type": run.alert_type,
                "environment": run.environment,
                "error": str(e),
                "outcome": "ERROR",
            }
        record["provider"] = provider
        record["ablation"] = ablation
        record["label"] = ablation_cfg["label"]
        all_results.append(record)

    context.db.close()

    # Save results
    RESULTS_DIR.mkdir(exist_ok=True)
    suffix = f"{label}"
    out_json = RESULTS_DIR / f"scale_{suffix}.json"
    with open(out_json, "w") as f:
        json.dump(
            {
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "provider": provider,
                "ablation": ablation,
                "label": ablation_cfg["label"],
                "total_runs": len(all_results),
                "results": all_results,
            },
            f,
            indent=2,
        )
    logger.info("Results -> %s", out_json)

    table = format_scale_table(all_results, ablation_cfg["label"])
    out_table = RESULTS_DIR / f"scale_{suffix}_table.txt"
    with open(out_table, "w", encoding="utf-8") as f:
        f.write(table)
    logger.info("Table -> %s", out_table)

    print("\n" + table)
    return all_results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Sentri Scale Evaluation (Block 1 + 3)")
    parser.add_argument("--provider", help="LLM provider: claude | openai | gemini (default: auto-detect)")
    parser.add_argument(
        "--ablation",
        default="none",
        choices=list(ABLATION_CONFIGS.keys()),
        help="Ablation condition (default: none = full system)",
    )
    parser.add_argument(
        "--all-ablations",
        action="store_true",
        help="Run all 5 ablation conditions sequentially (Block 3)",
    )
    parser.add_argument(
        "--template",
        action="store_true",
        help="Run in template-only mode (Baseline B, no LLM)",
    )
    parser.add_argument(
        "--alerts",
        help="Comma-separated alert families to run (default: all 9)",
    )
    parser.add_argument(
        "--all-providers",
        action="store_true",
        help="Run for all discovered providers (Block 1 full matrix)",
    )
    args = parser.parse_args()

    alert_families = args.alerts.split(",") if args.alerts else None

    if args.template:
        run_scale_eval("noop", "", ablation="none", template_mode=True, alert_families=alert_families)
        return

    providers = discover_providers(args.provider)
    if not providers:
        print("ERROR: No LLM API keys found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or SENTRI_GEMINI_API_KEY.")
        sys.exit(1)

    if not args.all_providers:
        # Use only the first discovered / requested provider
        providers = providers[:1]

    ablations_to_run: list[str]
    if args.all_ablations:
        ablations_to_run = list(ABLATION_CONFIGS.keys())
        logger.info("Running all %d ablation conditions", len(ablations_to_run))
    else:
        ablations_to_run = [args.ablation]

    for provider_name, api_key in providers:
        for ablation in ablations_to_run:
            logger.info("--- Provider: %s | Ablation: %s ---", provider_name, ablation)
            run_scale_eval(
                provider=provider_name,
                api_key=api_key,
                ablation=ablation,
                alert_families=alert_families,
            )

    print(f"\nAll runs complete. Results in {RESULTS_DIR}/")
    print("Run `python eval/aggregate_results.py` to generate final IEEE tables.")


if __name__ == "__main__":
    main()
