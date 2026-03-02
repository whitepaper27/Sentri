#!/usr/bin/env python
"""Sentri v5.0 End-to-End Test Runner.

Sends real alert emails, waits for Scout to pick them up,
and verifies the full pipeline processed them against Docker Oracle.

Usage:
    python tests/e2e/run_e2e_tests.py                  # Run all tests
    python tests/e2e/run_e2e_tests.py tablespace_full   # Run one test
    python tests/e2e/run_e2e_tests.py --status-only     # Just check workflow status

Prerequisites:
    1. Docker Oracle running on localhost:1521
    2. Sentri daemon running: python -m sentri start
    3. Email password set: SENTRI_EMAIL_PASSWORD env var
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sentri.config.paths import DB_PATH
from sentri.db.connection import Database
from sentri.db.workflow_repo import WorkflowRepository

# Import the email sender
sys.path.insert(0, str(PROJECT_ROOT))
from send_test_email import ALERT_TEMPLATES, send_alert

# How long to wait for Scout to poll (scout_poll_interval=60s + processing time)
SCOUT_WAIT = 90
# How long to wait for Supervisor to process (orchestrator_poll_interval=10s + agent time)
SUPERVISOR_WAIT = 30


# -- Expected routing (from brain/routing_rules.md) --
EXPECTED_ROUTING = {
    "tablespace_full": "storage_agent",
    "temp_full": "storage_agent",
    "archive_dest_full": "storage_agent",
    "high_undo_usage": "storage_agent",
    "long_running_sql": "sql_tuning_agent",
    "cpu_high": "sql_tuning_agent",
    "session_blocker": "rca_agent",
}

# Terminal workflow states
TERMINAL_STATES = {"COMPLETED", "FAILED", "ROLLED_BACK", "ESCALATED", "VERIFICATION_FAILED"}

# Color helpers
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"


def get_workflow_repo() -> WorkflowRepository:
    """Connect to the live Sentri SQLite database."""
    db = Database(DB_PATH)
    return WorkflowRepository(db)


def get_recent_workflows(repo: WorkflowRepository, minutes: int = 10) -> list:
    """Get workflows created in the last N minutes."""
    workflows = repo.find_recent(limit=50)
    cutoff = time.time() - (minutes * 60)
    recent = []
    for wf in workflows:
        # created_at is ISO format string
        if hasattr(wf, "created_at") and wf.created_at:
            try:
                ts = datetime.fromisoformat(wf.created_at.replace("Z", "+00:00"))
                if ts.timestamp() > cutoff:
                    recent.append(wf)
            except (ValueError, TypeError):
                recent.append(wf)  # Include if we can't parse
        else:
            recent.append(wf)
    return recent


def wait_for_workflow(
    repo: WorkflowRepository, alert_type: str, timeout: int = SCOUT_WAIT
) -> dict | None:
    """Wait for a workflow matching the alert_type to appear."""
    print(f"  Waiting up to {timeout}s for Scout to detect {alert_type}...", end="", flush=True)
    start = time.time()
    seen_ids = {wf.id for wf in repo.find_recent(limit=100)}

    while time.time() - start < timeout:
        time.sleep(10)
        print(".", end="", flush=True)
        workflows = repo.find_recent(limit=20)
        for wf in workflows:
            if wf.id not in seen_ids and wf.alert_type == alert_type:
                print(f" {GREEN}DETECTED{RESET} ({wf.id[:8]})")
                return wf
    print(f" {RED}TIMEOUT{RESET}")
    return None


def wait_for_processing(
    repo: WorkflowRepository, workflow_id: str, timeout: int = SUPERVISOR_WAIT
) -> dict | None:
    """Wait for a workflow to move past DETECTED status."""
    print(f"  Waiting up to {timeout}s for Supervisor to process...", end="", flush=True)
    start = time.time()

    while time.time() - start < timeout:
        time.sleep(5)
        print(".", end="", flush=True)
        wf = repo.get(workflow_id)
        if wf and wf.status != "DETECTED":
            print(f" {GREEN}{wf.status}{RESET}")
            return wf
    wf = repo.get(workflow_id)
    if wf:
        print(f" {YELLOW}{wf.status}{RESET}")
    return wf


def run_single_test(alert_type: str, repo: WorkflowRepository) -> dict:
    """Send one alert email and verify the pipeline processes it.

    Returns a result dict with status and details.
    """
    result = {
        "alert_type": alert_type,
        "email_sent": False,
        "workflow_detected": False,
        "workflow_processed": False,
        "final_status": None,
        "expected_agent": EXPECTED_ROUTING.get(alert_type, "unknown"),
        "error": None,
    }

    # Step 1: Send email
    print(f"\n{BOLD}[{alert_type}]{RESET}")
    print(f"  Expected routing: → {CYAN}{result['expected_agent']}{RESET}")

    ok = send_alert(alert_type)
    if not ok:
        result["error"] = "Failed to send email"
        return result
    result["email_sent"] = True

    # Step 2: Wait for Scout to detect
    wf = wait_for_workflow(repo, alert_type)
    if not wf:
        result["error"] = "Scout did not detect the email within timeout"
        return result
    result["workflow_detected"] = True

    # Step 3: Wait for Supervisor to process
    wf = wait_for_processing(repo, wf.id)
    if wf and wf.status != "DETECTED":
        result["workflow_processed"] = True
        result["final_status"] = wf.status
    elif wf:
        result["final_status"] = wf.status
        result["error"] = "Workflow stuck in DETECTED"
    else:
        result["error"] = "Workflow disappeared"

    return result


def print_summary(results: list[dict]):
    """Print a summary table of all test results."""
    print(f"\n{'=' * 70}")
    print(f"{BOLD}E2E TEST SUMMARY{RESET}")
    print(f"{'=' * 70}")
    print(f"{'Alert Type':<22} {'Email':>6} {'Detect':>7} {'Process':>8} {'Status':<20}")
    print(f"{'-' * 70}")

    passed = 0
    for r in results:
        email = f"{GREEN}OK{RESET}" if r["email_sent"] else f"{RED}FAIL{RESET}"
        detect = f"{GREEN}OK{RESET}" if r["workflow_detected"] else f"{RED}FAIL{RESET}"
        process = f"{GREEN}OK{RESET}" if r["workflow_processed"] else f"{RED}FAIL{RESET}"
        status = r["final_status"] or "N/A"

        if status == "COMPLETED":
            status_colored = f"{GREEN}{status}{RESET}"
        elif status in ("FAILED", "ROLLED_BACK"):
            status_colored = f"{RED}{status}{RESET}"
        elif status == "VERIFICATION_FAILED":
            status_colored = f"{YELLOW}{status}{RESET}"
        else:
            status_colored = f"{CYAN}{status}{RESET}"

        print(f"{r['alert_type']:<22} {email:>15} {detect:>16} {process:>17} {status_colored}")

        if r["workflow_detected"] and r["workflow_processed"]:
            passed += 1

    print(f"{'-' * 70}")
    total = len(results)
    print(f"Result: {passed}/{total} alerts processed through pipeline")
    if passed == total:
        print(f"{GREEN}{BOLD}ALL TESTS PASSED{RESET}")
    else:
        print(f"{YELLOW}Some tests need attention{RESET}")


def check_status_only(repo: WorkflowRepository):
    """Just show recent workflow status without sending emails."""
    print(f"\n{BOLD}Recent Workflows (last 30 minutes){RESET}")
    print(f"{'ID':<10} {'Alert Type':<22} {'Database':<15} {'Status':<20} {'Created'}")
    print("-" * 85)

    workflows = get_recent_workflows(repo, minutes=30)
    if not workflows:
        print("No workflows found in the last 30 minutes.")
        return

    for wf in workflows:
        wf_id = wf.id[:8] if wf.id else "?"
        created = getattr(wf, "created_at", "?")
        if isinstance(created, str) and len(created) > 19:
            created = created[:19]
        print(f"{wf_id:<10} {wf.alert_type:<22} {wf.database_id:<15} {wf.status:<20} {created}")


def main():
    args = sys.argv[1:]

    repo = get_workflow_repo()

    if "--status-only" in args:
        check_status_only(repo)
        return

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    # Determine which alerts to test
    if not args:
        # Default: test the 7 routable alert types
        test_types = list(EXPECTED_ROUTING.keys())
    elif "all" in args:
        test_types = list(ALERT_TEMPLATES.keys())
    else:
        test_types = [a for a in args if a in ALERT_TEMPLATES]
        if not test_types:
            print(f"Unknown alert type(s): {args}")
            print(f"Available: {', '.join(ALERT_TEMPLATES.keys())}")
            sys.exit(1)

    print(f"{BOLD}Sentri v5.0 E2E Test Runner{RESET}")
    print(f"Testing {len(test_types)} alert type(s): {', '.join(test_types)}")
    print("Make sure 'sentri start' is running in another terminal!")
    print()

    results = []
    for alert_type in test_types:
        result = run_single_test(alert_type, repo)
        results.append(result)

    print_summary(results)


if __name__ == "__main__":
    main()
