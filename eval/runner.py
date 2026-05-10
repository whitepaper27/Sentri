"""24-run remediation orchestrator.

Runs each evaluation scenario through the appropriate specialist agent,
collects metrics including researcher tier (agentic/one-shot/template),
and saves results.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from eval.config import RUN_MATRIX, RunConfig, RunResult, get_extracted_data
from eval.harness import (
    InstrumentedLLMProvider,
    bootstrap_sentri,
    create_workflow,
    reset_state,
    setup_blocking_chain,
    teardown_blocking_chain,
)

logger = logging.getLogger("sentri.eval.runner")

RESULTS_DIR = Path(__file__).parent / "results"


def _detect_researcher_tier(context, workflow_id: str) -> str:
    """Detect which researcher tier was used from investigation store or workflow data."""
    wf = context.workflow_repo.get(workflow_id)
    if not wf or not wf.execution_plan:
        return "none"

    try:
        plan_data = json.loads(wf.execution_plan)
        # The source field in the plan indicates which tier produced it
        # Check the suggestion for research source markers
    except (json.JSONDecodeError, TypeError):
        pass

    # Check investigation files for LLM markers
    # If the investigation has tool calls, it was agentic
    # If it has LLM output but no tools, it was one-shot
    # If it has template markers, it was template

    # Fallback: check if LLM was called (instrumented provider)
    return "unknown"


def run_single(
    run_config: RunConfig,
    context,
    agents: dict,
    safety_mesh,
    instrumented_llm: Optional[InstrumentedLLMProvider],
    database_id: str,
    oracle_dsn: str = "",
    oracle_user: str = "",
    oracle_password: str = "",
) -> RunResult:
    """Execute a single evaluation run and collect metrics."""
    logger.info("=== Starting run %s [%s] ===", run_config.run_id, run_config.description)

    # 1. Reset state for this alert_type
    reset_state(context, run_config.alert_type, database_id)

    # 2. If P3 (session_blocker) and no SID override, set up blocking chain
    blocking_chain = None
    blocker_sid = "0"
    needs_chain = (
        run_config.incident_type == "session_blocker"
        and oracle_dsn
        and not (run_config.extracted_overrides and "sid" in run_config.extracted_overrides)
    )
    if needs_chain:
        try:
            conn_a, conn_b, thread, blocker_sid = setup_blocking_chain(
                oracle_dsn, oracle_user, oracle_password
            )
            blocking_chain = (conn_a, conn_b, thread)
            logger.info("Blocking chain created, blocker SID=%s", blocker_sid)
        except Exception as e:
            logger.warning("Could not create blocking chain: %s", e)

    # 3. Reset LLM metrics
    if instrumented_llm:
        instrumented_llm.reset_metrics()

    # 4. Create workflow with incident-specific extracted_data
    extracted = get_extracted_data(
        run_config.incident_type,
        database_id,
        overrides=run_config.extracted_overrides,
        sid=blocker_sid,
    )

    # Use the run's environment (DEV/UAT/PROD)
    workflow_id = create_workflow(
        context, run_config.alert_type, database_id,
        run_config.environment, extracted,
    )

    # 5. Run the agent
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

    # 6. Read workflow from DB for final status + execution plan
    wf = context.workflow_repo.get(workflow_id)
    outcome = wf.status if wf else "UNKNOWN"

    # Map to paper-friendly outcomes
    if outcome == "AWAITING_APPROVAL":
        outcome = "NEEDS_APPROVAL"

    sql_generated = ""
    mesh_result = ""
    researcher_tier = "template"  # default

    if wf and wf.execution_plan:
        try:
            plan_data = json.loads(wf.execution_plan)
            sql_generated = plan_data.get("forward_sql", "")
        except (json.JSONDecodeError, TypeError):
            sql_generated = str(wf.execution_plan)[:200]

    # Detect researcher tier from agent result
    if isinstance(result, dict):
        tier = result.get("researcher_tier", "")
        if not tier:
            # Infer from source
            source = result.get("source", "")
            if "agentic" in source:
                tier = "agentic"
            elif source == "llm":
                tier = "one-shot"
            else:
                tier = "template"
        researcher_tier = tier

    # Infer mesh result from workflow status
    if outcome in ("COMPLETED", "NEEDS_APPROVAL"):
        mesh_result = "ALLOW" if outcome == "COMPLETED" else "REQUIRE_APPROVAL"
    elif outcome == "ESCALATED":
        mesh_result = "BLOCK"
    else:
        mesh_result = result.get("safety_mesh", outcome) if isinstance(result, dict) else outcome

    # 7. Tear down blocking chain
    if blocking_chain:
        teardown_blocking_chain(*blocking_chain)

    # 8. Collect LLM metrics
    llm_calls = instrumented_llm.call_count if instrumented_llm else 0
    tokens_in = instrumented_llm.total_input_tokens if instrumented_llm else 0
    tokens_out = instrumented_llm.total_output_tokens if instrumented_llm else 0
    # Rough cost estimate: $0.50/1M input, $1.50/1M output (Gemini Flash)
    cost = (tokens_in * 0.50 / 1_000_000) + (tokens_out * 1.50 / 1_000_000)

    run_result = RunResult(
        run_id=run_config.run_id,
        incident_type=run_config.incident_type,
        mode=run_config.mode,
        outcome=outcome,
        resolution_time_s=elapsed,
        llm_calls=llm_calls,
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        cost_usd=cost,
        sql_generated=sql_generated[:500],
        safety_mesh_result=mesh_result,
        researcher_tier=researcher_tier,
        description=run_config.description,
        error=error_msg,
    )

    logger.info(
        "Run %s: outcome=%s, time=%.2fs, llm_calls=%d, tier=%s",
        run_config.run_id, outcome, elapsed, llm_calls, researcher_tier,
    )

    return run_result


def run_all(
    parts: str = "all",
) -> list[RunResult]:
    """Execute all 24 remediation runs.

    Args:
        parts: "all", "full", or "template"
    """
    from sentri.config.settings import Settings

    settings = Settings.load()
    db_cfg = settings.databases[0]
    database_id = db_cfg.name

    # Parse Oracle DSN for blocking chain
    conn_str = db_cfg.connection_string
    if conn_str.startswith("oracle://"):
        conn_str = conn_str[len("oracle://"):]
    if "@" in conn_str:
        user_part, oracle_dsn = conn_str.split("@", 1)
    else:
        user_part = db_cfg.username or "system"
        oracle_dsn = conn_str

    oracle_user = db_cfg.username or user_part
    oracle_password = db_cfg.password

    results: list[RunResult] = []

    # Filter runs by mode
    if parts == "full":
        matrix = [r for r in RUN_MATRIX if r.mode == "full"]
    elif parts == "template":
        matrix = [r for r in RUN_MATRIX if r.mode == "template"]
    else:
        matrix = list(RUN_MATRIX)

    # Group by mode
    full_runs = [r for r in matrix if r.mode == "full"]
    template_runs = [r for r in matrix if r.mode == "template"]

    if full_runs:
        # Check if LLM is available for full mode
        print(f"\n{'='*60}")
        print(f"  FULL MODE RUNS ({len(full_runs)} runs)")
        print(f"{'='*60}")

        context, agents, safety_mesh, instrumented_llm = bootstrap_sentri("full")

        if instrumented_llm and instrumented_llm.is_available():
            print(f"  LLM: {instrumented_llm.name} (instrumented)")
        else:
            print(f"  WARNING: LLM not available. Full mode will use template fallback.")
            print(f"  Set SENTRI_GEMINI_API_KEY to enable agentic/one-shot tiers.")
        print()

        for i, run_cfg in enumerate(full_runs):
            print(f"  [{i+1}/{len(full_runs)}] {run_cfg.run_id}: {run_cfg.description[:55]}")
            result = run_single(
                run_cfg, context, agents, safety_mesh, instrumented_llm,
                database_id, oracle_dsn, oracle_user, oracle_password,
            )
            results.append(result)
            tier_tag = f" [{result.researcher_tier}]" if result.researcher_tier else ""
            print(f"         -> {result.outcome} in {result.resolution_time_s:.2f}s{tier_tag}")

            if i < len(full_runs) - 1:
                time.sleep(2)

        context.db.close()

    if template_runs:
        print(f"\n{'='*60}")
        print(f"  TEMPLATE MODE RUNS ({len(template_runs)} runs)")
        print(f"{'='*60}\n")

        context, agents, safety_mesh, _ = bootstrap_sentri("template")

        for i, run_cfg in enumerate(template_runs):
            print(f"  [{i+1}/{len(template_runs)}] {run_cfg.run_id}: {run_cfg.description[:55]}")
            result = run_single(
                run_cfg, context, agents, safety_mesh, None,
                database_id, oracle_dsn, oracle_user, oracle_password,
            )
            results.append(result)
            print(f"         -> {result.outcome} in {result.resolution_time_s:.2f}s [template]")

            if i < len(template_runs) - 1:
                time.sleep(1)

        context.db.close()

    # Save raw results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / "raw_results.json"
    with open(results_path, "w") as f:
        json.dump([r.to_dict() for r in results], f, indent=2)
    print(f"\nResults saved to {results_path}")

    return results
