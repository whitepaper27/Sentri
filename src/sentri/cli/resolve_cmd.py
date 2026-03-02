"""sentri resolve - Manually resolve a workflow (DBA fixed it themselves)."""

from __future__ import annotations

import click
from rich.console import Console

console = Console()


@click.command("resolve")
@click.argument("workflow_id")
@click.option(
    "--reason", default="", help="Reason for resolution (e.g. 'Fixed tablespace manually')"
)
@click.option("--by", "resolved_by", default="", help="Who resolved it (e.g. 'John DBA')")
@click.option("--escalate", is_flag=True, default=False, help="Escalate instead of completing")
def resolve_cmd(workflow_id: str, reason: str, resolved_by: str, escalate: bool):
    """Manually resolve a workflow (mark as completed or escalated).

    Use this when the DBA fixed the issue themselves or wants to close
    a workflow that no longer needs automated action.

    WORKFLOW_ID can be a full ID or the first 8 characters.
    """
    from sentri.config.paths import DB_PATH, SENTRI_HOME
    from sentri.core.models import AuditRecord
    from sentri.db.audit_repo import AuditRepository
    from sentri.db.connection import Database
    from sentri.db.workflow_repo import WorkflowRepository
    from sentri.orchestrator.state_machine import StateMachine, is_terminal

    if not SENTRI_HOME.exists():
        console.print("[red]Sentri not initialized. Run 'sentri init' first.[/red]")
        raise SystemExit(1)

    db = Database(DB_PATH)
    workflow_repo = WorkflowRepository(db)
    audit_repo = AuditRepository(db)
    state_machine = StateMachine(workflow_repo)

    # Support partial ID matching (first 8 chars)
    wf = workflow_repo.get(workflow_id)
    if not wf:
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

    # Cannot resolve terminal workflows
    if is_terminal(wf.status):
        console.print(
            f"[yellow]Workflow {wf.id[:8]}... is already in terminal state "
            f"'{wf.status}'.[/yellow]"
        )
        db.close()
        raise SystemExit(1)

    resolver = resolved_by or "CLI user"
    target = "ESCALATED" if escalate else "COMPLETED"

    try:
        state_machine.transition(wf.id, target)
    except Exception as e:
        console.print(f"[red]Cannot resolve: {e}[/red]")
        db.close()
        raise SystemExit(1)

    # Create audit record
    evidence = "channel=cli"
    if reason:
        evidence += f",reason={reason}"
    audit_repo.create(
        AuditRecord(
            workflow_id=wf.id,
            action_type="MANUAL_RESOLUTION",
            database_id=wf.database_id,
            environment=wf.environment,
            executed_by="cli",
            approved_by=resolver,
            result=target,
            evidence=evidence,
        )
    )

    if escalate:
        console.print(f"[yellow]Workflow {wf.id[:8]}... ESCALATED by {resolver}[/yellow]")
    else:
        console.print(f"[green]Workflow {wf.id[:8]}... manually resolved by {resolver}[/green]")

    if reason:
        console.print(f"  [dim]Reason:[/dim] {reason}")
    console.print(f"  [dim]Previous status:[/dim] {wf.status}")
    console.print(f"  Alert:    {wf.alert_type}")
    console.print(f"  Database: {wf.database_id}")
    console.print(f"  Env:      {wf.environment}")

    db.close()
