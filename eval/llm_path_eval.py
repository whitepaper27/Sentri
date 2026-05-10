"""
eval/llm_path_eval.py
---------------------
Forces the full argue/select LLM path by:
  1. Wiping workflow history for the 3 incident types (0 history → cost gate escalates to full)
  2. Confirming LLM provider is available (API key set)
  3. Running 4 incidents per type across DEV/UAT/PROD
  4. Capturing: tier fired, candidates, judge scores, selected fix, Safety Mesh verdict,
     wall-clock time, token cost

Why 0 history triggers full argue/select:
  _get_historical_success_rate() returns (0.0, 0) → neither template path (needs ≥5 history)
  nor one-shot path (needs ≥3 history) fires → falls through to argue() at line 369
  of specialist_base.py. With a real LLM available, argue() calls the judge. Without one
  it falls back to confidence-only (the bug in the original eval).

Usage:
    ANTHROPIC_API_KEY=<key> python eval/llm_path_eval.py
    SENTRI_GEMINI_API_KEY=<key> python eval/llm_path_eval.py   (if sentri.yaml uses gemini)

Output:
    eval/results/llm_path_results.json   -- raw structured results
    eval/results/llm_path_table.txt      -- human-readable table for paper
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import datetime
from pathlib import Path

# Ensure src/ is importable when run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval.harness import (
    InstrumentedLLMProvider,
    bootstrap_sentri,
    create_workflow,
    reset_state,
    setup_blocking_chain,
    teardown_blocking_chain,
)
from eval.config import RunConfig, RunResult, get_extracted_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sentri.eval.llm_path")

RESULTS_DIR = Path(__file__).parent / "results"

# 12 runs: 3 incident types × 4 environments (cycling DEV/UAT/PROD/DEV)
LLM_RUN_MATRIX: list[RunConfig] = [
    # tablespace_full — Storage Agent
    RunConfig("L01", "tablespace_full", "tablespace_full", "full", "storage_agent",   environment="DEV",  description="tablespace_full DEV run 1"),
    RunConfig("L02", "tablespace_full", "tablespace_full", "full", "storage_agent",   environment="UAT",  description="tablespace_full UAT run 2"),
    RunConfig("L03", "tablespace_full", "tablespace_full", "full", "storage_agent",   environment="PROD", description="tablespace_full PROD run 3"),
    RunConfig("L04", "tablespace_full", "tablespace_full", "full", "storage_agent",   environment="DEV",  description="tablespace_full DEV run 4"),
    # stale_stats — SQL Tuning Agent
    RunConfig("L05", "stale_stats",     "stale_stats",     "full", "sql_tuning_agent", environment="DEV",  description="stale_stats DEV run 1"),
    RunConfig("L06", "stale_stats",     "stale_stats",     "full", "sql_tuning_agent", environment="UAT",  description="stale_stats UAT run 2"),
    RunConfig("L07", "stale_stats",     "stale_stats",     "full", "sql_tuning_agent", environment="PROD", description="stale_stats PROD run 3"),
    RunConfig("L08", "stale_stats",     "stale_stats",     "full", "sql_tuning_agent", environment="DEV",  description="stale_stats DEV run 4"),
    # session_blocker — RCA Agent
    RunConfig("L09", "session_blocker", "session_blocker", "full", "rca_agent",        environment="DEV",  description="session_blocker DEV run 1", extracted_overrides={"sid": "999"}),
    RunConfig("L10", "session_blocker", "session_blocker", "full", "rca_agent",        environment="UAT",  description="session_blocker UAT run 2", extracted_overrides={"sid": "999"}),
    RunConfig("L11", "session_blocker", "session_blocker", "full", "rca_agent",        environment="PROD", description="session_blocker PROD run 3", extracted_overrides={"sid": "999"}),
    RunConfig("L12", "session_blocker", "session_blocker", "full", "rca_agent",        environment="DEV",  description="session_blocker DEV run 4", extracted_overrides={"sid": "999"}),
]

DATABASE_ID = "DEMO"


def check_llm_available() -> tuple[str, str]:
    """Abort early if no API key — prevents silent confidence-fallback repeating original eval bug.

    Returns (provider_name, api_key) for the first available key.
    provider_name uses Sentri's expected names ("claude"/"openai"/"gemini").
    """
    for env_var, provider in [
        ("SENTRI_CLAUDE_API_KEY",    "claude"),
        ("ANTHROPIC_API_KEY",        "claude"),
        ("SENTRI_ANTHROPIC_API_KEY", "claude"),
        ("SENTRI_OPENAI_API_KEY",    "openai"),
        ("OPENAI_API_KEY",           "openai"),
        ("SENTRI_GEMINI_API_KEY",    "gemini"),
        ("GOOGLE_API_KEY",           "gemini"),
    ]:
        key = os.environ.get(env_var, "")
        if key:
            logger.info("LLM API key found: %s → provider=%s", env_var, provider)
            return provider, key
    print(
        "ERROR: No LLM API key found.\n"
        "Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or SENTRI_GEMINI_API_KEY before running."
    )
    sys.exit(1)


def wipe_workflow_history(context, incident_types: list[str]) -> None:
    """Delete workflow rows for these alert types so cost gate sees 0 history → full argue/select."""
    for atype in incident_types:
        reset_state(context, atype, DATABASE_ID)
        logger.info("Cleared workflow history for alert_type=%s db=%s", atype, DATABASE_ID)


def run_llm_path_single(
    run_config: RunConfig,
    context,
    agents: dict,
    instrumented_llm: InstrumentedLLMProvider,
) -> dict:
    """Run one incident and capture full LLM-path telemetry."""
    logger.info("=== %s [%s / %s] ===", run_config.run_id, run_config.incident_type, run_config.environment)

    # Fresh history for each run → guarantees full argue/select
    reset_state(context, run_config.alert_type, DATABASE_ID)
    instrumented_llm.reset_metrics()

    extracted = get_extracted_data(
        run_config.incident_type,
        DATABASE_ID,
        overrides=run_config.extracted_overrides,
        sid=run_config.extracted_overrides.get("sid", "0") if run_config.extracted_overrides else "0",
    )

    workflow_id = create_workflow(
        context, run_config.alert_type, DATABASE_ID, run_config.environment, extracted
    )

    agent = agents[run_config.agent]
    start = time.monotonic()
    error_msg = None

    try:
        result = agent.process(workflow_id)
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        error_msg = str(e)
        logger.error("Run %s failed: %s", run_config.run_id, e)

    elapsed = time.monotonic() - start

    wf = context.workflow_repo.get(workflow_id)
    outcome = wf.status if wf else "UNKNOWN"
    if outcome == "AWAITING_APPROVAL":
        outcome = "NEEDS_APPROVAL"

    # Extract SQL and plan details
    sql_generated = ""
    num_candidates = 0
    selected_candidate = ""
    judge_scores: dict = {}
    argue_select_fired = False

    if wf and wf.execution_plan:
        try:
            plan_data = json.loads(wf.execution_plan)
            sql_generated = plan_data.get("forward_sql", "")
            # Specialist agents store argue/select metadata in the plan
            argue_meta = plan_data.get("argue_select_meta", {})
            if argue_meta:
                argue_select_fired = True
                num_candidates = argue_meta.get("num_candidates", 0)
                selected_candidate = argue_meta.get("selected_title", "")
                judge_scores = argue_meta.get("scores", {})
        except (json.JSONDecodeError, TypeError):
            sql_generated = str(wf.execution_plan)[:200]

    # Detect tier from agent result dict
    tier = "unknown"
    if isinstance(result, dict):
        tier = result.get("researcher_tier", "") or result.get("cost_gate_tier", "")
        if not tier:
            src = result.get("source", "")
            if "agentic" in src:
                tier = "agentic"
            elif src == "llm":
                tier = "one-shot"
            elif src == "template":
                tier = "template"

    # If instrumented LLM was called, we know argue/select fired
    if instrumented_llm.call_count > 0:
        argue_select_fired = True
        if tier == "unknown":
            tier = "full_argue_select"

    # Infer safety mesh verdict from workflow status
    if outcome == "COMPLETED":
        mesh_verdict = "APPROVED (auto-executed)"
    elif outcome == "NEEDS_APPROVAL":
        mesh_verdict = "REQUIRE_APPROVAL"
    elif outcome == "ESCALATED":
        mesh_verdict = "BLOCK (escalated)"
    else:
        mesh_verdict = outcome

    llm_calls = instrumented_llm.call_count
    tokens_in = instrumented_llm.total_input_tokens
    tokens_out = instrumented_llm.total_output_tokens
    # Cost estimate: $3/1M input, $15/1M output (Claude Sonnet)
    cost_usd = (tokens_in * 3.0 / 1_000_000) + (tokens_out * 15.0 / 1_000_000)

    record = {
        "run_id": run_config.run_id,
        "incident_type": run_config.incident_type,
        "environment": run_config.environment,
        "tier_fired": tier,
        "argue_select_fired": argue_select_fired,
        "num_candidates": num_candidates,
        "selected_candidate": selected_candidate,
        "judge_scores": judge_scores,
        "safety_mesh_verdict": mesh_verdict,
        "outcome": outcome,
        "wall_clock_seconds": round(elapsed, 2),
        "llm_calls": llm_calls,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "estimated_cost_usd": round(cost_usd, 4),
        "sql_generated": sql_generated[:200] if sql_generated else "",
        "error": error_msg,
    }

    logger.info(
        "%s: tier=%s verdict=%s llm_calls=%d time=%.1fs cost=$%.4f",
        run_config.run_id, tier, mesh_verdict, llm_calls, elapsed, cost_usd,
    )
    return record


def format_table(results: list[dict]) -> str:
    header = (
        f"{'ID':<5} {'Incident Type':<18} {'Env':<5} {'Tier':<20} "
        f"{'Cands':>5} {'Selected Fix':<25} {'Verdict':<22} {'Time':>6} {'Cost':>7}"
    )
    sep = "-" * len(header)
    rows = [header, sep]
    for r in results:
        if "error" in r and r["error"] and r.get("tier_fired", "unknown") == "unknown":
            rows.append(f"{r['run_id']:<5} ERROR: {r['error'][:60]}")
            continue
        sel = (r.get("selected_candidate") or "n/a")[:24]
        rows.append(
            f"{r['run_id']:<5} {r['incident_type']:<18} {r['environment']:<5} "
            f"{r.get('tier_fired','?'):<20} {r.get('num_candidates',0):>5} "
            f"{sel:<25} {r.get('safety_mesh_verdict','?'):<22} "
            f"{r.get('wall_clock_seconds',0):>5.1f}s "
            f"${r.get('estimated_cost_usd',0):>5.3f}"
        )
    rows.append(sep)

    total = len(results)
    llm_runs = [r for r in results if r.get("argue_select_fired")]
    total_cost = sum(r.get("estimated_cost_usd", 0) for r in results)
    avg_time = sum(r.get("wall_clock_seconds", 0) for r in results) / max(total, 1)
    avg_cands = sum(r.get("num_candidates", 0) for r in results) / max(total, 1)

    rows.append(f"\nSUMMARY: {len(llm_runs)}/{total} runs fired full argue/select LLM judge")
    rows.append(f"Avg candidates/run: {avg_cands:.1f}  |  Avg time: {avg_time:.1f}s  |  Total cost: ${total_cost:.4f}")

    non_llm = [r for r in results if not r.get("argue_select_fired") and not r.get("error")]
    if non_llm:
        rows.append(f"\nWARNING: {len(non_llm)} run(s) did NOT fire argue/select — check API key and logs:")
        for r in non_llm:
            rows.append(f"  {r['run_id']} {r['incident_type']} / {r['environment']} → tier={r.get('tier_fired')}")
    else:
        rows.append(f"\nASSERTION PASSED: All {len(llm_runs)} LLM-path runs confirmed.")

    return "\n".join(rows)


def main() -> None:
    provider, api_key = check_llm_available()

    logger.info("Bootstrapping Sentri in full (LLM) mode with provider=%s ...", provider)
    context, agents, _safety_mesh, instrumented_llm = bootstrap_sentri(
        "full", provider_override=provider, api_key_override=api_key
    )

    if instrumented_llm is None or not instrumented_llm.is_available():
        print(
            "ERROR: bootstrap_sentri returned no instrumented LLM.\n"
            "Check that sentri.yaml learning.llm_provider is set and the API key env var is present."
        )
        sys.exit(1)

    logger.info("LLM provider available: %s", instrumented_llm.name)

    # Wipe history so all runs hit 0 history → full argue/select branch
    alert_types = list({r.alert_type for r in LLM_RUN_MATRIX})
    wipe_workflow_history(context, alert_types)

    all_results: list[dict] = []
    for run_config in LLM_RUN_MATRIX:
        try:
            record = run_llm_path_single(run_config, context, agents, instrumented_llm)
        except Exception as e:
            logger.error("Run %s crashed: %s", run_config.run_id, e)
            record = {
                "run_id": run_config.run_id,
                "incident_type": run_config.incident_type,
                "environment": run_config.environment,
                "error": str(e),
                "tier_fired": "unknown",
            }
        all_results.append(record)

    RESULTS_DIR.mkdir(exist_ok=True)

    out_json = RESULTS_DIR / "llm_path_results.json"
    with open(out_json, "w") as f:
        json.dump(
            {
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "total_runs": len(all_results),
                "results": all_results,
            },
            f,
            indent=2,
        )
    logger.info("Raw results -> %s", out_json)

    table = format_table(all_results)
    out_table = RESULTS_DIR / "llm_path_table.txt"
    with open(out_table, "w", encoding="utf-8") as f:
        f.write(table)
    logger.info("Table -> %s", out_table)

    print("\n" + table)
    print(f"\nDone. Paste {out_json} back to Claude to update the paper tables.")  # noqa


if __name__ == "__main__":
    main()
