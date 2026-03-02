"""sentri approve - Approve or deny workflows awaiting approval."""

from __future__ import annotations

from datetime import datetime, timezone

import click
from rich.console import Console

console = Console()


@click.command("approve")
@click.argument("workflow_id")
@click.option("--deny", is_flag=True, default=False, help="Deny instead of approve")
@click.option("--by", "approved_by", default="", help="Who is approving (e.g. 'John DBA')")
@click.option("--reason", default="", help="Reason for denial (used with --deny)")
@click.option(
    "--escalate", is_flag=True, default=False, help="Deny and escalate (used with --deny)"
)
def approve_cmd(workflow_id: str, deny: bool, approved_by: str, reason: str, escalate: bool):
    """Approve or deny a workflow awaiting approval.

    WORKFLOW_ID can be a full ID or the first 8 characters.
    """
    from sentri.config.paths import DB_PATH, SENTRI_HOME
    from sentri.core.constants import WorkflowStatus
    from sentri.core.models import AuditRecord
    from sentri.db.audit_repo import AuditRepository
    from sentri.db.connection import Database
    from sentri.db.workflow_repo import WorkflowRepository

    if not SENTRI_HOME.exists():
        console.print("[red]Sentri not initialized. Run 'sentri init' first.[/red]")
        raise SystemExit(1)

    db = Database(DB_PATH)
    workflow_repo = WorkflowRepository(db)
    audit_repo = AuditRepository(db)

    # Support partial ID matching (first 8 chars)
    wf = workflow_repo.get(workflow_id)
    if not wf:
        # Try prefix match
        rows = db.execute_read(
            "SELECT id FROM workflows WHERE id LIKE ? LIMIT 5",
            (f"{workflow_id}%",),
        )
        if len(rows) == 1:
            wf = workflow_repo.get(rows[0]["id"])
        elif len(rows) > 1:
            console.print(f"[yellow]Multiple workflows match '{workflow_id}':[/yellow]")
            for row in rows:
                w = workflow_repo.get(row["id"])
                if w:
                    console.print(f"  {w.id}  {w.alert_type:20s}  {w.status}")
            console.print("\nPlease provide a more specific ID.")
            db.close()
            raise SystemExit(1)
        else:
            console.print(f"[red]No workflow found matching '{workflow_id}'[/red]")
            db.close()
            raise SystemExit(1)

    if wf.status != WorkflowStatus.AWAITING_APPROVAL.value:
        console.print(
            f"[yellow]Workflow {wf.id} is in status '{wf.status}', "
            f"not AWAITING_APPROVAL.[/yellow]"
        )
        db.close()
        raise SystemExit(1)

    action = "DENIED" if deny else "APPROVED"
    target_status = WorkflowStatus.DENIED if deny else WorkflowStatus.APPROVED
    approver = approved_by or "CLI user"
    now_iso = datetime.now(timezone.utc).isoformat()

    workflow_repo.update_status(
        wf.id,
        target_status.value,
        approved_by=approver,
        approved_at=now_iso,
    )
    console.print(f"[green]Workflow {wf.id[:8]}... {action} by {approver}[/green]")

    if deny:
        if escalate:
            # DENIED → ESCALATED (needs further attention)
            workflow_repo.update_status(wf.id, WorkflowStatus.ESCALATED.value)
            console.print("[yellow]Workflow ESCALATED for further attention.[/yellow]")
        else:
            # DENIED → COMPLETED (no action taken)
            workflow_repo.update_status(wf.id, WorkflowStatus.COMPLETED.value)
            console.print("[dim]Workflow marked as COMPLETED (no action taken).[/dim]")
        if reason:
            console.print(f"  [dim]Reason:[/dim] {reason}")
    else:
        console.print("[dim]Workflow will be executed on next Supervisor cycle.[/dim]")

    # Create audit record
    evidence = "channel=cli"
    if reason:
        evidence += f",denied_reason={reason}"
    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="APPROVAL_DECISION",
            action_sql=wf.execution_plan or "",
            database_id=wf.database_id,
            environment=wf.environment,
            executed_by="cli",
            approved_by=approver,
            result=action,
            evidence=evidence,
        )
    )

    # Show workflow details
    console.print(f"\n  Alert:    {wf.alert_type}")
    console.print(f"  Database: {wf.database_id}")
    console.print(f"  Env:      {wf.environment}")

    db.close()
