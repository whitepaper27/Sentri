"""sentri show - Show details of a specific workflow."""

import json

import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()


@click.command("show")
@click.argument("workflow_id")
def show_cmd(workflow_id: str):
    """Show detailed information about a workflow."""
    from sentri.config.paths import DB_PATH
    from sentri.db.audit_repo import AuditRepository
    from sentri.db.connection import Database
    from sentri.db.workflow_repo import WorkflowRepository

    if not DB_PATH.exists():
        console.print("[red]No database found. Run 'sentri init' first.[/red]")
        return

    db = Database(DB_PATH)
    repo = WorkflowRepository(db)
    audit_repo = AuditRepository(db)

    # Support partial ID matching
    wf = repo.get(workflow_id)
    if not wf:
        # Try prefix match
        all_wfs = repo.find_recent(100)
        matches = [w for w in all_wfs if w.id.startswith(workflow_id)]
        if len(matches) == 1:
            wf = matches[0]
        elif len(matches) > 1:
            console.print(f"[yellow]Multiple matches for '{workflow_id}':[/yellow]")
            for m in matches:
                console.print(f"  {m.id} ({m.alert_type} on {m.database_id})")
            db.close()
            return
        else:
            console.print(f"[red]Workflow '{workflow_id}' not found.[/red]")
            db.close()
            return

    # Display workflow details
    console.print()
    console.print(
        Panel(
            f"[bold]{wf.alert_type}[/bold] on [cyan]{wf.database_id}[/cyan] ({wf.environment})",
            title=f"Workflow {wf.id}",
            subtitle=wf.status,
        )
    )

    console.print(f"\n  [dim]Created:[/dim]  {wf.created_at}")
    console.print(f"  [dim]Updated:[/dim]  {wf.updated_at}")

    if wf.approved_by:
        console.print(f"  [dim]Approved by:[/dim] {wf.approved_by} at {wf.approved_at}")

    # Show suggestion
    if wf.suggestion:
        console.print("\n[bold]Suggestion (Agent 1):[/bold]")
        _print_json(wf.suggestion)

    # Show verification
    if wf.verification:
        console.print("\n[bold]Verification (Agent 2):[/bold]")
        _print_json(wf.verification)

    # Show execution plan with SQL blocks extracted
    if wf.execution_plan:
        console.print("\n[bold]Execution Plan:[/bold]")
        try:
            plan = json.loads(wf.execution_plan)
            # Show metadata fields
            for key in ("action_type", "risk_level"):
                if key in plan:
                    console.print(f"  [dim]{key}:[/dim] {plan[key]}")
            # Show SQL blocks separately with syntax highlighting + word wrap
            if plan.get("forward_sql"):
                console.print("\n  [bold]Proposed SQL:[/bold]")
                console.print(
                    Syntax(
                        plan["forward_sql"],
                        "sql",
                        theme="monokai",
                        line_numbers=False,
                        word_wrap=True,
                    )
                )
            if plan.get("rollback_sql"):
                console.print("\n  [bold]Rollback SQL:[/bold]")
                console.print(
                    Syntax(
                        plan["rollback_sql"],
                        "sql",
                        theme="monokai",
                        line_numbers=False,
                        word_wrap=True,
                    )
                )
        except json.JSONDecodeError:
            _print_json(wf.execution_plan)

    # Show execution result
    if wf.execution_result:
        console.print("\n[bold]Execution Result (Agent 4):[/bold]")
        _print_json(wf.execution_result)

    # Show audit records
    records = audit_repo.find_by_workflow(wf.id)
    if records:
        console.print("\n[bold]Audit Trail:[/bold]")
        for rec in records:
            style = "green" if rec.result == "SUCCESS" else "red"
            console.print(
                f"  [{style}]{rec.result}[/{style}] {rec.action_type} "
                f"by {rec.executed_by} at {rec.timestamp}"
            )

    # Show investigation analysis (if saved)
    try:
        from sentri.config.paths import INVESTIGATIONS_DIR
        from sentri.memory.investigation_store import InvestigationStore

        if INVESTIGATIONS_DIR.exists():
            inv_store = InvestigationStore(INVESTIGATIONS_DIR)
            inv = inv_store.load_for_workflow(wf.id)
            if inv:
                console.print("\n[bold]Investigation Analysis:[/bold]")
                console.print(f"  [dim]Agent:[/dim]      {inv.agent_name}")
                console.print(f"  [dim]Confidence:[/dim] {inv.confidence}")
                if inv.focus_area:
                    console.print(f"  [dim]Focus:[/dim]      {inv.focus_area}")
                if inv.selected_option_title:
                    console.print(f"  [dim]Selected:[/dim]   {inv.selected_option_title}")
                if inv.selected_option_reasoning:
                    console.print(f"  [dim]Reasoning:[/dim]  {inv.selected_option_reasoning[:200]}")
                console.print(f"  [dim]Outcome:[/dim]    {inv.outcome}")
                console.print(f"  [dim]Full report:[/dim] {inv.file_path}")
    except Exception:
        pass  # Investigation display is optional — never affects show_cmd

    db.close()


def _print_json(raw: str) -> None:
    try:
        parsed = json.loads(raw)
        formatted = json.dumps(parsed, indent=2)
        console.print(Syntax(formatted, "json", theme="monokai", line_numbers=False))
    except json.JSONDecodeError:
        console.print(f"  {raw[:500]}")
