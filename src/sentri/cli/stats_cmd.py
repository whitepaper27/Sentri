"""sentri stats - Show system statistics."""

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.command("stats")
def stats_cmd():
    """Show Sentri workflow statistics."""
    from sentri.config.paths import DB_PATH
    from sentri.db.audit_repo import AuditRepository
    from sentri.db.connection import Database
    from sentri.db.workflow_repo import WorkflowRepository

    if not DB_PATH.exists():
        console.print("[red]No database found. Run 'sentri init' first.[/red]")
        return

    db = Database(DB_PATH)
    workflow_repo = WorkflowRepository(db)
    audit_repo = AuditRepository(db)

    total = workflow_repo.count_total()
    by_status = workflow_repo.count_by_status()
    audit_results = audit_repo.count_by_result()

    console.print("\n[bold blue]Sentri Statistics[/bold blue]\n")

    # Summary
    success_count = audit_results.get("SUCCESS", 0)
    failed_count = audit_results.get("FAILED", 0)
    total_executions = success_count + failed_count + audit_results.get("ROLLED_BACK", 0)
    success_rate = (success_count / total_executions * 100) if total_executions > 0 else 0

    console.print(f"  Total workflows: [bold]{total}[/bold]")
    console.print(f"  Total executions: [bold]{total_executions}[/bold]")
    console.print(f"  Success rate: [bold]{success_rate:.1f}%[/bold]")
    console.print()

    # Status breakdown
    if by_status:
        table = Table(title="Workflows by Status")
        table.add_column("Status", style="cyan")
        table.add_column("Count", justify="right")

        for status, count in sorted(by_status.items()):
            style = "green" if status == "COMPLETED" else "yellow" if "AWAIT" in status else "white"
            table.add_row(status, str(count), style=style)

        console.print(table)

    # Audit results
    if audit_results:
        console.print()
        table = Table(title="Execution Results")
        table.add_column("Result", style="cyan")
        table.add_column("Count", justify="right")

        for result, count in sorted(audit_results.items()):
            style = "green" if result == "SUCCESS" else "red"
            table.add_row(result, str(count), style=style)

        console.print(table)

    # v2.0: Profile summary
    try:
        profile_rows = db.execute_read(
            """SELECT COUNT(*) as cnt FROM environment_registry
               WHERE database_profile IS NOT NULL"""
        )
        profiled_count = profile_rows[0]["cnt"] if profile_rows else 0
        if profiled_count > 0:
            console.print(f"\n  Profiled databases: [bold]{profiled_count}[/bold]")
    except Exception:
        pass

    # v2.0: Learning summary
    try:
        from sentri.db.learning_repo import LearningRepository

        learning_repo = LearningRepository(db)
        obs_total = learning_repo.count_total()
        if obs_total > 0:
            obs_by_type = learning_repo.count_by_alert_type()
            console.print(f"  Learning observations: [bold]{obs_total}[/bold]")
            for atype, cnt in sorted(obs_by_type.items()):
                console.print(f"    {atype}: {cnt}")
    except Exception:
        pass

    db.close()
