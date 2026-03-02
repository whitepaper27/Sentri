#!/usr/bin/env python
"""Sentri Approval Workflow E2E Test Suite.

Tests the full approval lifecycle:
  1. Send alert email → Scout detects → Specialist processes → Safety Mesh
  2. REQUIRE_APPROVAL → approval email sent with [WF:xxxxxxxx]
  3. Reply APPROVED/DENIED via email or CLI
  4. Supervisor executes approved workflows
  5. Timeout escalation + notification

Usage:
    python tests/e2e/test_approval_flow.py                  # Run all tests
    python tests/e2e/test_approval_flow.py send              # Step 1: Send alert only
    python tests/e2e/test_approval_flow.py wait              # Step 2: Wait for approval email
    python tests/e2e/test_approval_flow.py approve <wf_id>   # Step 3: Approve via email
    python tests/e2e/test_approval_flow.py deny <wf_id>      # Step 3: Deny via email
    python tests/e2e/test_approval_flow.py status            # Check workflow status
    python tests/e2e/test_approval_flow.py audit <wf_id>     # Check audit trail
    python tests/e2e/test_approval_flow.py cli-approve <id>  # Approve via CLI
    python tests/e2e/test_approval_flow.py cli-deny <id>     # Deny via CLI
    python tests/e2e/test_approval_flow.py cli-resolve <id>  # Resolve via CLI
    python tests/e2e/test_approval_flow.py show <id>         # Show workflow detail (SQL display)

Prerequisites:
    1. Docker Oracle running on localhost:1521
    2. Sentri daemon running: python -m sentri start
    3. Email configured in config/sentri.yaml
"""

from __future__ import annotations

import json
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sentri.config.paths import DB_PATH
from sentri.config.settings import Settings
from sentri.db.audit_repo import AuditRepository
from sentri.db.connection import Database
from sentri.db.workflow_repo import WorkflowRepository

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"


# ── Email config ──────────────────────────────────────────────────────


def get_email_config():
    """Load email config from sentri.yaml."""
    settings = Settings.load()
    return {
        "smtp_server": settings.email.smtp_server,
        "smtp_port": settings.email.smtp_port,
        "username": settings.email.username,
        "password": settings.email.password,
        "use_tls": settings.email.use_tls,
    }


# ── Alert templates (matching alerts/*.md patterns) ───────────────────

ALERT_TEMPLATES = {
    "cpu_high": {
        "subject": "High CPU utilization 97% on sentri-dev",
        "body": "CPU utilization at 97% on sentri-dev. Top session SID 523.",
        "routes_to": "sql_tuning_agent",
    },
    "long_running_sql": {
        "subject": "Long running SQL session SID=912 running 4 hours on sentri-dev",
        "body": "Session SID 912 has been running for 4 hours on sentri-dev.",
        "routes_to": "sql_tuning_agent",
    },
    "session_blocker": {
        "subject": "Blocking session detected SID=847 on sentri-dev",
        "body": "Session SID 847 is blocking 12 other sessions on sentri-dev.",
        "routes_to": "rca_agent",
    },
    "tablespace_full": {
        "subject": "Tablespace USERS_TEST 92% full on sentri-dev",
        "body": "Tablespace USERS_TEST is 92% full on sentri-dev. Free: 40MB.",
        "routes_to": "storage_agent",
    },
    "high_undo_usage": {
        "subject": "High undo usage 91% on sentri-dev",
        "body": "Undo tablespace UNDOTBS1 at 91% on sentri-dev.",
        "routes_to": "storage_agent",
    },
}


# ── Database helpers ──────────────────────────────────────────────────


def get_repos():
    """Get workflow and audit repositories from live DB."""
    db = Database(DB_PATH)
    return WorkflowRepository(db), AuditRepository(db), db


def find_awaiting_approval(repo: WorkflowRepository) -> list:
    """Find all workflows in AWAITING_APPROVAL status."""
    all_wfs = repo.find_recent(limit=50)
    return [wf for wf in all_wfs if wf.status == "AWAITING_APPROVAL"]


def find_workflow_by_short_id(repo: WorkflowRepository, short_id: str):
    """Find workflow by first 8 chars of ID."""
    all_wfs = repo.find_recent(limit=100)
    matches = [wf for wf in all_wfs if wf.id.startswith(short_id)]
    return matches[0] if len(matches) == 1 else None


# ── Step 1: Send alert email ─────────────────────────────────────────


def cmd_send(alert_type: str = "cpu_high"):
    """Send a test alert email to trigger the approval flow."""
    if alert_type not in ALERT_TEMPLATES:
        print(f"{RED}Unknown alert type: {alert_type}{RESET}")
        print(f"Available: {', '.join(ALERT_TEMPLATES.keys())}")
        return False

    cfg = get_email_config()
    template = ALERT_TEMPLATES[alert_type]

    msg = MIMEMultipart()
    msg["From"] = cfg["username"]
    msg["To"] = cfg["username"]
    msg["Subject"] = template["subject"]
    msg.attach(MIMEText(template["body"], "plain"))

    try:
        server = smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"])
        if cfg["use_tls"]:
            server.starttls()
        server.login(cfg["username"], cfg["password"])
        server.sendmail(cfg["username"], [cfg["username"]], msg.as_string())
        server.quit()
        print(f"{GREEN}[OK]{RESET} Sent {BOLD}{alert_type}{RESET} alert email")
        print(f"  Subject: {template['subject']}")
        print(f"  Routes to: {CYAN}{template['routes_to']}{RESET}")
        print(f"\n  {DIM}Wait ~60s for Scout to detect, then run:{RESET}")
        print(f"  {BOLD}python tests/e2e/test_approval_flow.py wait{RESET}")
        return True
    except Exception as e:
        print(f"{RED}[FAIL]{RESET} SMTP error: {e}")
        return False


# ── Step 2: Wait for approval email / check status ───────────────────


def cmd_wait(timeout: int = 120):
    """Wait for a workflow to reach AWAITING_APPROVAL status."""
    repo, _, db = get_repos()

    print(f"Waiting up to {timeout}s for AWAITING_APPROVAL workflow...")
    start = time.time()

    while time.time() - start < timeout:
        awaiting = find_awaiting_approval(repo)
        if awaiting:
            wf = awaiting[0]
            print(f"\n{GREEN}[OK]{RESET} Workflow {BOLD}{wf.id[:8]}{RESET}... is AWAITING_APPROVAL")
            print(f"  Alert:    {wf.alert_type}")
            print(f"  Database: {wf.database_id}")
            print(f"  Env:      {wf.environment}")

            # Check if execution plan has SQL
            if wf.execution_plan:
                try:
                    plan = json.loads(wf.execution_plan)
                    print(f"  Action:   {plan.get('action_type', '?')}")
                    print(f"  Risk:     {plan.get('risk_level', '?')}")
                    sql = plan.get("forward_sql", "")
                    if sql:
                        print(f"  SQL:      {sql[:80]}{'...' if len(sql) > 80 else ''}")
                except json.JSONDecodeError:
                    pass

            print(f"\n  {DIM}Next steps:{RESET}")
            print(
                f"  {BOLD}python tests/e2e/test_approval_flow.py approve {wf.id[:8]}{RESET}  # Approve via email reply"
            )
            print(
                f"  {BOLD}python tests/e2e/test_approval_flow.py deny {wf.id[:8]}{RESET}     # Deny via email reply"
            )
            print(
                f"  {BOLD}python tests/e2e/test_approval_flow.py cli-approve {wf.id[:8]}{RESET}  # Approve via CLI"
            )
            print(
                f"  {BOLD}python tests/e2e/test_approval_flow.py cli-deny {wf.id[:8]}{RESET}     # Deny via CLI"
            )
            db.close()
            return True

        time.sleep(10)
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] Checking...", flush=True)

    print(f"\n{YELLOW}[TIMEOUT]{RESET} No AWAITING_APPROVAL workflow found after {timeout}s")
    print(f"  {DIM}Run 'python tests/e2e/test_approval_flow.py status' to check workflows{RESET}")
    db.close()
    return False


# ── Step 3a: Approve via email reply ──────────────────────────────────


def cmd_approve_email(short_id: str):
    """Send an APPROVED reply email for a workflow."""
    repo, _, db = get_repos()
    wf = find_workflow_by_short_id(repo, short_id)
    if not wf:
        print(f"{RED}[FAIL]{RESET} No workflow found matching '{short_id}'")
        db.close()
        return False

    if wf.status != "AWAITING_APPROVAL":
        print(f"{YELLOW}[WARN]{RESET} Workflow {wf.id[:8]} is '{wf.status}', not AWAITING_APPROVAL")

    cfg = get_email_config()
    wf_tag = wf.id[:8]

    msg = MIMEText("APPROVED\n\nLooks good, proceed with execution.", "plain")
    msg["From"] = cfg["username"]
    msg["To"] = cfg["username"]
    msg[
        "Subject"
    ] = f"Re: [SENTRI] Approval needed: {wf.alert_type} on {wf.database_id} [WF:{wf_tag}]"

    try:
        server = smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"])
        if cfg["use_tls"]:
            server.starttls()
        server.login(cfg["username"], cfg["password"])
        server.sendmail(cfg["username"], [cfg["username"]], msg.as_string())
        server.quit()
        print(f"{GREEN}[OK]{RESET} Sent APPROVED reply for [WF:{wf_tag}]")
        print(f"  Subject: {msg['Subject']}")
        print(f"\n  {DIM}Wait ~60s for Scout to detect reply, then run:{RESET}")
        print(f"  {BOLD}python tests/e2e/test_approval_flow.py status{RESET}")
        print(f"  {BOLD}python tests/e2e/test_approval_flow.py audit {wf_tag}{RESET}")
        db.close()
        return True
    except Exception as e:
        print(f"{RED}[FAIL]{RESET} SMTP error: {e}")
        db.close()
        return False


# ── Step 3b: Deny via email reply ─────────────────────────────────────


def cmd_deny_email(short_id: str, reason: str = "Too risky during peak hours"):
    """Send a DENIED reply email for a workflow."""
    repo, _, db = get_repos()
    wf = find_workflow_by_short_id(repo, short_id)
    if not wf:
        print(f"{RED}[FAIL]{RESET} No workflow found matching '{short_id}'")
        db.close()
        return False

    cfg = get_email_config()
    wf_tag = wf.id[:8]

    msg = MIMEText(f"DENIED - {reason}", "plain")
    msg["From"] = cfg["username"]
    msg["To"] = cfg["username"]
    msg["Subject"] = f"Re: [SENTRI] [WF:{wf_tag}]"

    try:
        server = smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"])
        if cfg["use_tls"]:
            server.starttls()
        server.login(cfg["username"], cfg["password"])
        server.sendmail(cfg["username"], [cfg["username"]], msg.as_string())
        server.quit()
        print(f"{GREEN}[OK]{RESET} Sent DENIED reply for [WF:{wf_tag}]")
        print(f"  Reason: {reason}")
        print(f"\n  {DIM}Wait ~60s for Scout, then check:{RESET}")
        print(f"  {BOLD}python tests/e2e/test_approval_flow.py audit {wf_tag}{RESET}")
        db.close()
        return True
    except Exception as e:
        print(f"{RED}[FAIL]{RESET} SMTP error: {e}")
        db.close()
        return False


# ── Step 3c: Approve/Deny/Resolve via CLI ─────────────────────────────


def cmd_cli_approve(short_id: str):
    """Approve workflow via 'sentri approve' CLI."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "sentri", "approve", short_id, "--by", "E2E Test DBA"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode == 0:
        print(f"\n  {DIM}Check audit trail:{RESET}")
        print(f"  {BOLD}python tests/e2e/test_approval_flow.py audit {short_id}{RESET}")
    return result.returncode == 0


def cmd_cli_deny(short_id: str, reason: str = "Denied by E2E test"):
    """Deny workflow via 'sentri approve --deny' CLI."""
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sentri",
            "approve",
            short_id,
            "--deny",
            "--reason",
            reason,
            "--by",
            "E2E Test DBA",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.returncode == 0


def cmd_cli_resolve(short_id: str, reason: str = "Resolved manually by DBA"):
    """Resolve workflow via 'sentri resolve' CLI."""
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sentri",
            "resolve",
            short_id,
            "--reason",
            reason,
            "--by",
            "E2E Test DBA",
        ],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)
    return result.returncode == 0


# ── Status / Audit / Show ─────────────────────────────────────────────


def cmd_status():
    """Show recent workflows with their status."""
    repo, _, db = get_repos()
    workflows = repo.find_recent(limit=20)

    print(f"\n{BOLD}Recent Workflows{RESET}")
    print(f"{'ID':<10} {'Alert':<22} {'Database':<15} {'Status':<22} {'Approved By'}")
    print("-" * 90)

    for wf in workflows:
        status = wf.status
        if status == "COMPLETED":
            status_c = f"{GREEN}{status}{RESET}"
        elif status == "AWAITING_APPROVAL":
            status_c = f"{YELLOW}{status}{RESET}"
        elif status == "ESCALATED":
            status_c = f"{RED}{status}{RESET}"
        elif status == "APPROVED":
            status_c = f"{CYAN}{status}{RESET}"
        else:
            status_c = status

        approved = wf.approved_by or ""
        print(f"{wf.id[:8]:<10} {wf.alert_type:<22} {wf.database_id:<15} {status_c:<31} {approved}")

    db.close()


def cmd_audit(short_id: str):
    """Show audit trail for a workflow."""
    repo, audit_repo, db = get_repos()
    wf = find_workflow_by_short_id(repo, short_id)
    if not wf:
        print(f"{RED}No workflow found matching '{short_id}'{RESET}")
        db.close()
        return

    print(f"\n{BOLD}Audit Trail for {wf.id[:8]}...{RESET}")
    print(f"  Alert: {wf.alert_type} | DB: {wf.database_id} | Status: {wf.status}")
    if wf.approved_by:
        print(f"  Approved by: {CYAN}{wf.approved_by}{RESET} at {wf.approved_at}")
    print()

    records = audit_repo.find_by_workflow(wf.id)
    if not records:
        print(f"  {DIM}(no audit records){RESET}")
    else:
        for rec in records:
            color = (
                GREEN
                if rec.result in ("APPROVED", "SUCCESS")
                else RED
                if rec.result == "DENIED"
                else YELLOW
            )
            print(f"  [{color}{rec.result}{RESET}] {rec.action_type}")
            print(f"    by: {rec.approved_by or rec.executed_by} | via: {rec.evidence or 'N/A'}")
            if rec.action_sql:
                sql_preview = rec.action_sql[:100]
                print(
                    f"    sql: {DIM}{sql_preview}{'...' if len(rec.action_sql) > 100 else ''}{RESET}"
                )
            print(f"    at: {rec.timestamp}")
            print()

    db.close()


def cmd_show(short_id: str):
    """Show workflow detail via 'sentri show' CLI (tests SQL display fix)."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "sentri", "show", short_id],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)


# ── Full automated flow ──────────────────────────────────────────────


def cmd_full(alert_type: str = "cpu_high"):
    """Run the full approval flow: send → wait → show status.

    After this, user manually approves via email or CLI.
    """
    print(f"{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}Sentri Approval Flow E2E Test — {alert_type}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}\n")

    # Step 1: Send
    print(f"{BOLD}Step 1: Send alert email{RESET}")
    if not cmd_send(alert_type):
        return

    # Step 2: Wait for AWAITING_APPROVAL
    print(f"\n{BOLD}Step 2: Waiting for workflow to reach AWAITING_APPROVAL...{RESET}")
    if not cmd_wait(timeout=180):
        print(f"\n{YELLOW}The workflow may not need approval (DEV auto-executes low-risk).{RESET}")
        print(f"Check status: {BOLD}python tests/e2e/test_approval_flow.py status{RESET}")
        return

    print(f"\n{BOLD}Step 3: Choose an action{RESET}")
    print("  The approval email should be in your inbox with [WF:xxxxxxxx].")
    print("  Reply APPROVED or DENIED, OR use the CLI commands shown above.")
    print("\n  After approving, check result:")
    print(f"  {BOLD}python tests/e2e/test_approval_flow.py status{RESET}")
    print(f"  {BOLD}python tests/e2e/test_approval_flow.py audit <wf_id>{RESET}")


# ── CLI entry point ──────────────────────────────────────────────────

USAGE = """\
Usage: python tests/e2e/test_approval_flow.py <command> [args]

Commands:
  send [alert_type]      Send test alert email (default: cpu_high)
  wait                   Wait for AWAITING_APPROVAL workflow
  approve <wf_id>        Send APPROVED reply email
  deny <wf_id> [reason]  Send DENIED reply email
  cli-approve <wf_id>    Approve via sentri approve CLI
  cli-deny <wf_id>       Deny via sentri approve --deny CLI
  cli-resolve <wf_id>    Resolve via sentri resolve CLI
  status                 Show recent workflow status
  audit <wf_id>          Show audit trail for a workflow
  show <wf_id>           Show workflow detail (tests SQL display)
  full [alert_type]      Run full flow: send → wait → prompt

Alert types: cpu_high, long_running_sql, session_blocker, tablespace_full, high_undo_usage
"""


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        print(USAGE)
        return

    cmd = args[0]

    if cmd == "send":
        alert_type = args[1] if len(args) > 1 else "cpu_high"
        cmd_send(alert_type)

    elif cmd == "wait":
        cmd_wait()

    elif cmd == "approve":
        if len(args) < 2:
            print("Usage: approve <workflow_id>")
            return
        cmd_approve_email(args[1])

    elif cmd == "deny":
        if len(args) < 2:
            print("Usage: deny <workflow_id> [reason]")
            return
        reason = " ".join(args[2:]) if len(args) > 2 else "Too risky during peak hours"
        cmd_deny_email(args[1], reason)

    elif cmd == "cli-approve":
        if len(args) < 2:
            print("Usage: cli-approve <workflow_id>")
            return
        cmd_cli_approve(args[1])

    elif cmd == "cli-deny":
        if len(args) < 2:
            print("Usage: cli-deny <workflow_id>")
            return
        reason = " ".join(args[2:]) if len(args) > 2 else "Denied by E2E test"
        cmd_cli_deny(args[1], reason)

    elif cmd == "cli-resolve":
        if len(args) < 2:
            print("Usage: cli-resolve <workflow_id>")
            return
        reason = " ".join(args[2:]) if len(args) > 2 else "Resolved manually by DBA"
        cmd_cli_resolve(args[1], reason)

    elif cmd == "status":
        cmd_status()

    elif cmd == "audit":
        if len(args) < 2:
            print("Usage: audit <workflow_id>")
            return
        cmd_audit(args[1])

    elif cmd == "show":
        if len(args) < 2:
            print("Usage: show <workflow_id>")
            return
        cmd_show(args[1])

    elif cmd == "full":
        alert_type = args[1] if len(args) > 1 else "cpu_high"
        cmd_full(alert_type)

    else:
        print(f"Unknown command: {cmd}")
        print(USAGE)


if __name__ == "__main__":
    main()
