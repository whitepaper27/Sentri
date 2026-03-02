"""sentri profile / show-profile commands - Database profile management."""

from __future__ import annotations

import json

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.command("show-profile")
@click.argument("database_id")
def show_profile_cmd(database_id: str):
    """Show the database profile for a specific database."""
    from sentri.config.paths import DB_PATH
    from sentri.db.connection import Database
    from sentri.db.environment_repo import EnvironmentRepository

    if not DB_PATH.exists():
        console.print("[red]No database found. Run 'sentri init' first.[/red]")
        return

    db = Database(DB_PATH)
    env_repo = EnvironmentRepository(db)

    profile_json = env_repo.get_profile(database_id)
    if not profile_json:
        console.print(f"[yellow]No profile found for {database_id}[/yellow]")
        console.print("Run 'sentri start' to trigger profiling, or wait for the next cycle.")
        db.close()
        return

    try:
        profile = json.loads(profile_json)
    except json.JSONDecodeError:
        console.print("[red]Invalid profile data[/red]")
        db.close()
        return

    console.print(f"\n[bold blue]Database Profile: {database_id}[/bold blue]\n")

    # Basic info
    table = Table(title="Overview", show_header=False)
    table.add_column("Property", style="cyan", width=25)
    table.add_column("Value")

    table.add_row("Database ID", database_id)
    table.add_row("Type", profile.get("db_type", "Unknown"))
    table.add_row("Size (GB)", str(profile.get("db_size_gb", "?")))
    table.add_row("OMF Enabled", str(profile.get("omf_enabled", "?")))
    table.add_row("RAC", str(profile.get("is_rac", "?")))
    table.add_row("CDB", str(profile.get("is_cdb", "?")))
    table.add_row("Profiled At", profile.get("profiled_at", "?")[:19])
    table.add_row("Version", str(profile.get("version", "?")))

    console.print(table)

    # Critical parameters
    params = profile.get("critical_parameters", {})
    if params:
        console.print()
        ptable = Table(title="Critical Parameters")
        ptable.add_column("Parameter", style="cyan")
        ptable.add_column("Value")

        for name, value in sorted(params.items()):
            ptable.add_row(name, str(value))

        console.print(ptable)

    # Risk areas
    risks = profile.get("risk_areas", [])
    if risks:
        console.print()
        rtable = Table(title="Risk Areas")
        rtable.add_column("Tablespace", style="cyan")
        rtable.add_column("Usage %", justify="right")

        for risk in risks:
            if isinstance(risk, dict):
                rtable.add_row(
                    risk.get("tablespace_name", "?"),
                    str(risk.get("used_pct", "?")),
                )
            else:
                rtable.add_row(str(risk), "")

        console.print(rtable)

    # Non-standard parameters
    non_std = profile.get("non_standard_params", {})
    if non_std:
        console.print()
        nstable = Table(title="Non-Standard Parameters")
        nstable.add_column("Parameter", style="cyan")
        nstable.add_column("Value")

        for name, value in sorted(non_std.items()):
            nstable.add_row(name, str(value))

        console.print(nstable)

    db.close()


@click.command("profiles")
def profiles_cmd():
    """List all profiled databases."""
    from sentri.config.paths import DB_PATH
    from sentri.db.connection import Database
    from sentri.db.environment_repo import EnvironmentRepository

    if not DB_PATH.exists():
        console.print("[red]No database found. Run 'sentri init' first.[/red]")
        return

    db = Database(DB_PATH)
    _env_repo = EnvironmentRepository(db)

    rows = db.execute_read(
        """SELECT database_id, environment, database_profile, profile_version, profile_updated_at
           FROM environment_registry
           WHERE database_profile IS NOT NULL
           ORDER BY database_id"""
    )

    if not rows:
        console.print("[yellow]No databases profiled yet. Run 'sentri start' first.[/yellow]")
        db.close()
        return

    console.print("\n[bold blue]Profiled Databases[/bold blue]\n")

    table = Table()
    table.add_column("Database", style="cyan")
    table.add_column("Environment")
    table.add_column("Type")
    table.add_column("Size (GB)", justify="right")
    table.add_column("OMF")
    table.add_column("RAC")
    table.add_column("CDB")
    table.add_column("Version", justify="right")
    table.add_column("Profiled At")

    for row in rows:
        try:
            profile = json.loads(row["database_profile"])
        except (json.JSONDecodeError, TypeError):
            profile = {}

        table.add_row(
            row["database_id"],
            row["environment"] or "?",
            profile.get("db_type", "?"),
            str(profile.get("db_size_gb", "?")),
            str(profile.get("omf_enabled", "?")),
            str(profile.get("is_rac", "?")),
            str(profile.get("is_cdb", "?")),
            str(row["profile_version"] or "?"),
            (row["profile_updated_at"] or "?")[:19],
        )

    console.print(table)
    db.close()
