"""sentri audit - View audit log."""

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.command("audit")
@click.option("--last", default=50, help="Number of recent audit records")
@click.option("--database", default=None, help="Filter by database ID")
def audit_cmd(last: int, database: str | None):
    """View the audit log."""
    from sentri.config.paths import DB_PATH
    from sentri.db.audit_repo import AuditRepository
    from sentri.db.connection import Database

    if not DB_PATH.exists():
        console.print("[red]No database found. Run 'sentri init' first.[/red]")
        return

    db = Database(DB_PATH)
    repo = AuditRepository(db)

    if database:
        records = repo.find_by_database(database, last)
    else:
        records = repo.find_recent(last)

    if not records:
        console.print("\n[dim]No audit records found.[/dim]")
        db.close()
        return

    table = Table(title="Audit Log")
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Timestamp", style="dim", max_width=19)
    table.add_column("Workflow", style="dim", max_width=8)
    table.add_column("Action", style="cyan")
    table.add_column("Database", style="magenta")
    table.add_column("Env")
    table.add_column("Result")
    table.add_column("By", style="dim")

    for rec in records:
        result_style = "green" if rec.result == "SUCCESS" else "red"
        table.add_row(
            str(rec.id),
            (rec.timestamp or "")[:19],
            (rec.workflow_id or "")[:8],
            rec.action_type,
            rec.database_id,
            rec.environment,
            f"[{result_style}]{rec.result}[/{result_style}]",
            rec.executed_by,
        )

    console.print()
    console.print(table)
    db.close()
