"""sentri list - List recent workflows."""

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.command("list")
@click.option("--last", default=10, help="Number of recent workflows to show")
@click.option("--status", default=None, help="Filter by status")
def list_cmd(last: int, status: str | None):
    """List recent workflows."""
    from sentri.config.paths import DB_PATH
    from sentri.db.connection import Database
    from sentri.db.workflow_repo import WorkflowRepository

    if not DB_PATH.exists():
        console.print("[red]No database found. Run 'sentri init' first.[/red]")
        return

    db = Database(DB_PATH)
    repo = WorkflowRepository(db)

    if status:
        workflows = repo.find_by_status(status.upper())[:last]
    else:
        workflows = repo.find_recent(last)

    if not workflows:
        console.print("\n[dim]No workflows found.[/dim]")
        db.close()
        return

    table = Table(title=f"Recent Workflows (last {last})")
    table.add_column("ID", style="dim", max_width=8)
    table.add_column("Alert Type", style="cyan")
    table.add_column("Database", style="magenta")
    table.add_column("Env")
    table.add_column("Status")
    table.add_column("Created", style="dim")

    for wf in workflows:
        status_style = _status_style(wf.status)
        table.add_row(
            wf.id[:8],
            wf.alert_type,
            wf.database_id,
            wf.environment,
            f"[{status_style}]{wf.status}[/{status_style}]",
            (wf.created_at or "")[:19],
        )

    console.print()
    console.print(table)
    db.close()


def _status_style(status: str) -> str:
    if status == "COMPLETED":
        return "green"
    if status in ("FAILED", "ROLLED_BACK", "ESCALATED"):
        return "red"
    if status in ("AWAITING_APPROVAL", "TIMEOUT"):
        return "yellow"
    if status in ("EXECUTING", "VERIFYING"):
        return "blue"
    return "white"
