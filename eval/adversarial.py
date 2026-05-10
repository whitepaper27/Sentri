"""10 adversarial Safety Mesh tests — each targets a specific check.

Tests all 5 Safety Mesh checks independently:
  1. Policy Gate (confidence threshold, environment rules)
  2. Conflict Detection (concurrent workflow on same DB)
  3. Blast Radius (DDL classification by environment/risk)
  4. Circuit Breaker (repeated failures)
  5. Rollback Check (risk level vs rollback availability)
Plus 2 combined scenarios testing check interaction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from eval.config import ADVERSARIAL_CASES, ADVERSARIAL_CASES_ALL, AdversarialCase, AdversarialResult
from eval.harness import bootstrap_sentri, reset_state

from sentri.core.constants import WorkflowStatus
from sentri.core.models import AuditRecord, ExecutionPlan, Suggestion, Workflow

logger = logging.getLogger("sentri.eval.adversarial")

RESULTS_DIR = Path(__file__).parent / "results"


def _create_test_workflow(
    context,
    case: AdversarialCase,
    database_id: str,
) -> Workflow:
    """Create a minimal workflow for adversarial testing."""
    suggestion = Suggestion(
        alert_type="tablespace_full",
        database_id=database_id,
        raw_email_subject=f"[ADVERSARIAL] {case.case_id}",
        raw_email_body=case.description,
        extracted_data={"database_id": database_id},
    )

    workflow = Workflow(
        alert_type="tablespace_full",
        database_id=database_id,
        environment=case.environment,
        status=WorkflowStatus.DETECTED.value,
        suggestion=suggestion.to_json(),
    )

    workflow_id = context.workflow_repo.create(workflow)
    return context.workflow_repo.get(workflow_id)


def _create_test_plan(case: AdversarialCase) -> ExecutionPlan:
    """Create an ExecutionPlan from an adversarial case."""
    return ExecutionPlan(
        action_type="ADVERSARIAL_TEST",
        forward_sql=case.sql,
        rollback_sql=case.rollback_sql,
        validation_sql="SELECT 1 FROM dual",
        expected_outcome={"status": "test"},
        risk_level=case.risk_level,
        estimated_duration_seconds=5,
    )


def _inject_failures(context, database_id: str, count: int = 3):
    """Inject N failed audit_log entries to trip the circuit breaker."""
    for i in range(count):
        wf = Workflow(
            alert_type="tablespace_full",
            database_id=database_id,
            environment="DEV",
            status=WorkflowStatus.FAILED.value,
            suggestion=Suggestion(
                alert_type="tablespace_full",
                database_id=database_id,
                raw_email_subject=f"[INJECT] failure {i+1}",
                raw_email_body="Injected failure for circuit breaker test",
                extracted_data={"database_id": database_id},
            ).to_json(),
        )
        wf_id = context.workflow_repo.create(wf)

        context.audit_repo.create(AuditRecord(
            workflow_id=wf_id,
            action_type="INJECTED_FAILURE",
            database_id=database_id,
            environment="DEV",
            executed_by="eval_adversarial",
            result="FAILED",
            error_message=f"Injected failure {i+1} for circuit breaker test",
        ))


def _inject_executing_workflow(context, database_id: str) -> str:
    """Inject a workflow in EXECUTING status to trigger conflict detection."""
    wf = Workflow(
        alert_type="tablespace_full",
        database_id=database_id,
        environment="DEV",
        status=WorkflowStatus.EXECUTING.value,
        suggestion=Suggestion(
            alert_type="tablespace_full",
            database_id=database_id,
            raw_email_subject="[INJECT] executing workflow",
            raw_email_body="Injected EXECUTING workflow for conflict test",
            extracted_data={"database_id": database_id},
        ).to_json(),
    )
    return context.workflow_repo.create(wf)


def run_single_adversarial(
    case: AdversarialCase,
    context,
    safety_mesh,
    database_id: str,
) -> AdversarialResult:
    """Run a single adversarial test case."""
    logger.info("Adversarial %s [%s]: %s", case.case_id, case.target_check, case.description)

    injected_wf_id = None

    # Setup: inject state for specific checks
    if case.setup == "inject_3_failures":
        _inject_failures(context, database_id, count=3)
    elif case.setup == "inject_2_failures":
        _inject_failures(context, database_id, count=2)
    elif case.setup == "inject_5_failures":
        _inject_failures(context, database_id, count=5)
    elif case.setup == "inject_executing_workflow":
        injected_wf_id = _inject_executing_workflow(context, database_id)

    # Create workflow and plan
    workflow = _create_test_workflow(context, case, database_id)
    plan = _create_test_plan(case)

    # Run Safety Mesh check with the case's confidence level
    verdict = safety_mesh.check(workflow, plan, confidence=case.confidence)

    actual = verdict.decision.value
    blocked_by = verdict.blocked_by or ""

    passed = actual == case.expect

    result = AdversarialResult(
        case_id=case.case_id,
        sql=case.sql,
        expected=case.expect,
        actual=actual,
        passed=passed,
        target_check=case.target_check,
        blocked_by=blocked_by,
        reasons=verdict.reasons,
    )

    status = "PASS" if passed else "FAIL"
    logger.info(
        "  %s %s: expected=%s, actual=%s, blocked_by=%s",
        status, case.case_id, case.expect, actual, blocked_by,
    )

    # Cleanup: remove injected state
    if case.setup in ("inject_3_failures", "inject_2_failures", "inject_5_failures"):
        reset_state(context, "tablespace_full", database_id)
    elif case.setup == "inject_executing_workflow" and injected_wf_id:
        # Remove the injected EXECUTING workflow
        context.db.execute_write(
            "DELETE FROM workflows WHERE id = ?", (injected_wf_id,)
        )

    return result


def run_adversarial(
    cases: Optional[list[AdversarialCase]] = None,
) -> list[AdversarialResult]:
    """Execute adversarial Safety Mesh tests.

    Args:
        cases: List of AdversarialCase to run. Defaults to the original 10-case suite.
               Pass ADVERSARIAL_CASES_ALL for the full 80-case IEEE benchmark.
    """
    from sentri.config.settings import Settings

    if cases is None:
        cases = ADVERSARIAL_CASES

    settings = Settings.load()
    db_cfg = settings.databases[0]
    database_id = db_cfg.name

    print(f"\n{'='*60}")
    print(f"  ADVERSARIAL SAFETY MESH TESTS ({len(cases)} cases)")
    print(f"{'='*60}")
    print(f"  Testing all 5 safety checks independently\n")

    context, _, safety_mesh, _ = bootstrap_sentri("template")

    results: list[AdversarialResult] = []

    # Group by target check for display
    current_check = ""
    for i, case in enumerate(cases):
        if case.target_check != current_check:
            current_check = case.target_check
            print(f"\n  --- {current_check.upper()} ---")

        print(f"  [{i+1}/{len(cases)}] {case.case_id}: {case.description[:65]}")

        # Clean slate for each test
        reset_state(context, "tablespace_full", database_id)

        result = run_single_adversarial(case, context, safety_mesh, database_id)
        results.append(result)

        status = "PASS" if result.passed else "FAIL"
        blocked_info = f" (blocked_by: {result.blocked_by})" if result.blocked_by else ""
        print(f"         -> {status}: expected={result.expected}, actual={result.actual}{blocked_info}")

    context.db.close()

    # Save results (filename reflects case count for IEEE tracking)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"adversarial_results_{len(cases)}cases.json" if len(cases) != 10 else "adversarial_results.json"
    results_path = RESULTS_DIR / fname
    with open(results_path, "w") as f:
        json.dump([r.to_dict() for r in results], f, indent=2)
    print(f"\nResults saved to {results_path}")

    # Summary by check
    print(f"\n  {'='*50}")
    print(f"  SUMMARY BY CHECK")
    print(f"  {'='*50}")
    checks_tested = {}
    for r in results:
        checks_tested.setdefault(r.target_check, []).append(r)
    for check, check_results in checks_tested.items():
        passed = sum(1 for r in check_results if r.passed)
        print(f"  {check:20s}: {passed}/{len(check_results)} passed")

    total_passed = sum(1 for r in results if r.passed)
    print(f"\n  TOTAL: {total_passed}/{len(results)} passed")

    return results
