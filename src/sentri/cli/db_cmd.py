"""sentri db - Database configuration commands."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group("db")
def db_cmd():
    """Manage database configurations."""


@db_cmd.command("list")
def db_list():
    """List all configured databases from sentri.yaml."""
    from sentri.config.settings import Settings

    settings = Settings.load()

    if not settings.databases:
        console.print("[yellow]No databases configured in sentri.yaml[/yellow]")
        return

    table = Table(title="Configured Databases")
    table.add_column("Name", style="cyan")
    table.add_column("Environment", style="green")
    table.add_column("Username", style="blue")
    table.add_column("Connection String")
    table.add_column("Aliases", style="dim")
    table.add_column("Password Set", style="magenta")

    for db in settings.databases:
        # Username: from config, from URL, or default
        username = db.username
        if not username:
            from sentri.oracle.connection_pool import OracleConnectionPool

            username, _ = OracleConnectionPool._parse_connection_string(db.connection_string)
            username = f"{username} (from URL)"

        table.add_row(
            db.name,
            db.environment,
            username,
            db.connection_string,
            ", ".join(db.aliases) if db.aliases else "-",
            "Yes" if db.password else "No",
        )

    console.print(table)
    console.print("\n[dim]Password env var pattern: SENTRI_DB_<NAME>_PASSWORD[/dim]")


@db_cmd.command("test")
@click.option("--name", default=None, help="Test a specific database by name")
def db_test(name: str | None):
    """Test Oracle database connectivity."""
    from sentri.config.settings import Settings
    from sentri.oracle.connection_pool import OracleConnectionPool

    settings = Settings.load()
    pool = OracleConnectionPool()

    databases = settings.databases
    if name:
        db_cfg = settings.get_database(name)
        if not db_cfg:
            # Try alias resolution
            db_cfg = settings.resolve_database(name)
        if not db_cfg:
            console.print(f"[red]Database '{name}' not found in config[/red]")
            return
        databases = [db_cfg]

    if not databases:
        console.print("[yellow]No databases configured[/yellow]")
        return

    for db in databases:
        name_key = db.name.upper().replace("-", "_")
        if not db.password:
            console.print(
                f"  [yellow]{db.name}[/yellow]: No password set "
                f"(set SENTRI_DB_{name_key}_PASSWORD)"
            )
            continue

        try:
            conn = pool.get_connection(
                database_id=db.name,
                connection_string=db.connection_string,
                password=db.password,
                username=db.username or None,
                read_only=True,
            )
            conn.close()
            console.print(f"  [green]{db.name}[/green]: Connected successfully")
        except Exception as e:
            console.print(f"  [red]{db.name}[/red]: {e}")
