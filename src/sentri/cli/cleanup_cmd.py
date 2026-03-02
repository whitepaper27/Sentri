"""sentri cleanup - Clean up stuck workflows and cache without losing audit history."""

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.command("cleanup")
@click.option(
    "--stuck", is_flag=True, help="Remove workflows stuck in active states from crashed runs"
)
@click.option("--cache", is_flag=True, help="Clear email dedup cache (re-process old emails)")
@click.option(
    "--all",
    "clean_all",
    is_flag=True,
    help="Full reset: remove ALL data (keeps schema + env registry)",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def cleanup_cmd(stuck, cache, clean_all, yes):
    """Clean up stuck workflows and cache data.

    \b
    sentri cleanup --stuck   Remove workflows stuck in active states (from crashed runs)
    sentri cleanup --cache   Clear email dedup cache (re-process old emails)
    sentri cleanup --all     Full reset (keeps schema + environment registry)

    Note: FAILED/COMPLETED workflows are audit history and are NOT removed
    by --stuck. Use --all only for development/testing resets.
    """
    from sentri.config.paths import DB_PATH
    from sentri.db.connection import Database

    if not DB_PATH.exists():
        console.print("[red]No database found. Run 'sentri init' first.[/red]")
        return

    # Default: if no flag specified, show status
    if not clean_all and not stuck and not cache:
        db = Database(DB_PATH)
        try:
            _show_status(db)
        finally:
            db.close()
        return

    db = Database(DB_PATH)

    try:
        if clean_all:
            _do_full_reset(db, yes)
        elif stuck:
            _do_stuck_cleanup(db, yes)

        if cache and not clean_all:
            _do_cache_cleanup(db, yes)

    finally:
        db.close()


def _show_status(db):
    """Show current workflow counts by status."""
    rows = db.execute_read(
        "SELECT status, COUNT(*) as cnt FROM workflows GROUP BY status ORDER BY status"
    )
    if not rows:
        console.print("[green]No workflows in database.[/green]")
        return

    table = Table(title="Workflow Status Summary")
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Action", style="dim")

    active_states = {
        "DETECTED",
        "VERIFYING",
        "VERIFIED",
        "AWAITING_APPROVAL",
        "APPROVED",
        "EXECUTING",
        "PRE_FLIGHT",
    }

    for row in rows:
        status = row["status"]
        count = row["cnt"]
        if status in active_states:
            action = "cleanup --stuck removes these"
            style = "yellow"
        else:
            action = "audit history (preserved)"
            style = "green" if status == "COMPLETED" else "red"
        table.add_row(status, str(count), action, style=style)

    console.print(table)

    cache_rows = db.execute_read("SELECT COUNT(*) as cnt FROM cache")
    cache_count = cache_rows[0]["cnt"] if cache_rows else 0
    if cache_count:
        console.print(f"\n  Email cache: {cache_count} entries (cleanup --cache clears)")


def _do_stuck_cleanup(db, yes):
    """Remove workflows stuck in active (non-terminal) states."""
    active = (
        "DETECTED",
        "VERIFYING",
        "VERIFIED",
        "AWAITING_APPROVAL",
        "APPROVED",
        "EXECUTING",
        "PRE_FLIGHT",
    )
    placeholders = ", ".join("?" for _ in active)

    rows = db.execute_read(
        f"SELECT status, COUNT(*) as cnt FROM workflows WHERE status IN ({placeholders}) GROUP BY status",
        active,
    )

    if not rows:
        console.print("[green]No stuck workflows found.[/green]")
        return

    console.print("[yellow]Found stuck workflows:[/yellow]")
    total = 0
    for row in rows:
        console.print(f"  {row['status']}: {row['cnt']}")
        total += row["cnt"]

    if not yes and not click.confirm(f"\nRemove {total} stuck workflow(s)?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    db.execute_write(
        f"DELETE FROM workflows WHERE status IN ({placeholders})",
        active,
    )
    console.print(f"[green]Removed {total} stuck workflow(s). Audit history preserved.[/green]")


def _do_cache_cleanup(db, yes):
    """Clear the email dedup cache."""
    rows = db.execute_read("SELECT COUNT(*) as cnt FROM cache")
    count = rows[0]["cnt"] if rows else 0

    if count == 0:
        console.print("[green]Cache is already empty.[/green]")
        return

    console.print(f"[yellow]Found {count} cache entries (email dedup records).[/yellow]")
    if not yes and not click.confirm("Clear cache? Old emails will be re-processed."):
        console.print("[dim]Cancelled.[/dim]")
        return

    db.execute_write("DELETE FROM cache")
    console.print(f"[green]Cleared {count} cache entries.[/green]")


def _do_full_reset(db, yes):
    """Delete everything except schema and environment registry."""
    counts = {}
    for table in ["workflows", "audit_log", "cache"]:
        try:
            rows = db.execute_read(f"SELECT COUNT(*) as cnt FROM {table}")
            counts[table] = rows[0]["cnt"] if rows else 0
        except Exception:
            counts[table] = 0

    try:
        rows = db.execute_read("SELECT COUNT(*) as cnt FROM learning_observations")
        counts["learning_observations"] = rows[0]["cnt"] if rows else 0
    except Exception:
        counts["learning_observations"] = 0

    total = sum(counts.values())
    if total == 0:
        console.print("[green]Database is already clean.[/green]")
        return

    console.print("[yellow]Full reset will delete:[/yellow]")
    for table, count in counts.items():
        if count:
            console.print(f"  {table}: {count}")
    console.print("[dim]  (environment registry and schema are preserved)[/dim]")

    if not yes and not click.confirm("\nProceed with full reset?"):
        console.print("[dim]Cancelled.[/dim]")
        return

    db.execute_write("DELETE FROM workflows")
    db.execute_write("DELETE FROM audit_log")
    db.execute_write("DELETE FROM cache")
    try:
        db.execute_write("DELETE FROM learning_observations")
    except Exception:
        pass

    console.print("[green]Full reset complete. Schema and environment registry preserved.[/green]")
