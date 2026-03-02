"""sentri learning-* commands - Learning engine management."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _get_db():
    from sentri.config.paths import DB_PATH
    from sentri.db.connection import Database

    if not DB_PATH.exists():
        console.print("[red]No database found. Run 'sentri init' first.[/red]")
        raise SystemExit(1)
    return Database(DB_PATH)


@click.command("learning-summary")
def learning_summary_cmd():
    """Show learning engine status and observation summary."""
    from sentri.db.learning_repo import LearningRepository
    from sentri.db.md_version_repo import MdVersionRepository

    db = _get_db()
    learning_repo = LearningRepository(db)
    md_repo = MdVersionRepository(db)

    total = learning_repo.count_total()
    by_type = learning_repo.count_by_alert_type()
    tracked = md_repo.list_tracked_files()

    console.print("\n[bold blue]Learning Engine Summary[/bold blue]\n")
    console.print(f"  Total observations: [bold]{total}[/bold]")
    console.print(f"  Tracked policy files: [bold]{len(tracked)}[/bold]")
    console.print()

    if by_type:
        table = Table(title="Observations by Alert Type")
        table.add_column("Alert Type", style="cyan")
        table.add_column("Count", justify="right")

        for alert_type, count in sorted(by_type.items()):
            table.add_row(alert_type, str(count))

        console.print(table)

    if tracked:
        console.print()
        table = Table(title="Tracked Policy Files")
        table.add_column("File", style="cyan")
        table.add_column("Latest Version", justify="right")
        table.add_column("Changed By")
        table.add_column("Reason")

        for fp in tracked:
            latest = md_repo.get_latest_version(fp)
            if latest:
                table.add_row(
                    fp.split("/")[-1] if "/" in fp else fp.split("\\")[-1],
                    str(latest.get("version", "?")),
                    latest.get("changed_by", "?"),
                    (latest.get("change_reason", "") or "")[:50],
                )

        console.print(table)

    db.close()


@click.command("versions")
@click.argument("alert_type")
def versions_cmd(alert_type: str):
    """Show version history for an alert policy file."""
    from sentri.config.paths import ALERTS_DIR
    from sentri.db.md_version_repo import MdVersionRepository

    db = _get_db()
    md_repo = MdVersionRepository(db)

    file_path = str(ALERTS_DIR / f"{alert_type}.md")
    history = md_repo.get_history(file_path)

    if not history:
        console.print(f"[yellow]No version history for {alert_type}[/yellow]")
        db.close()
        return

    console.print(f"\n[bold blue]Version History: {alert_type}.md[/bold blue]\n")

    table = Table()
    table.add_column("Version", justify="right", style="cyan")
    table.add_column("Date")
    table.add_column("Changed By")
    table.add_column("Hash")
    table.add_column("Reason")

    for entry in history:
        table.add_row(
            str(entry.get("version", "?")),
            entry.get("created_at", "?")[:19],
            entry.get("changed_by", "?"),
            entry.get("content_hash", "?")[:12],
            (entry.get("change_reason", "") or "")[:40],
        )

    console.print(table)
    db.close()


@click.command("rollback-improvement")
@click.argument("alert_type")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
def rollback_improvement_cmd(alert_type: str, confirm: bool):
    """Rollback the last learning improvement for an alert type."""
    from sentri.agents.learning.applier import Applier
    from sentri.config.paths import ALERTS_DIR
    from sentri.db.md_version_repo import MdVersionRepository

    db = _get_db()
    md_repo = MdVersionRepository(db)

    file_path = str(ALERTS_DIR / f"{alert_type}.md")
    latest = md_repo.get_latest_version(file_path)

    if not latest:
        console.print(f"[yellow]No version history for {alert_type}[/yellow]")
        db.close()
        return

    console.print(
        f"Rolling back [bold]{alert_type}.md[/bold] "
        f"from version {latest['version']} "
        f"(backup: {latest.get('backup_path', 'N/A')})"
    )

    if not confirm:
        if not click.confirm("Proceed?"):
            console.print("[yellow]Aborted.[/yellow]")
            db.close()
            return

    applier = Applier(md_repo, ALERTS_DIR)
    result = applier.rollback(file_path)

    if result.get("rolled_back"):
        console.print(f"[green]Rolled back successfully from {result['restored_from']}[/green]")
    else:
        console.print(f"[red]Rollback failed: {result.get('error')}[/red]")

    db.close()
