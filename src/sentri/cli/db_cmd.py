"""sentri db - Database configuration commands."""

from __future__ import annotations

import click
import yaml
from rich.console import Console
from rich.table import Table

from sentri.config.paths import CONFIG_PATH

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
                f"  [yellow]{db.name}[/yellow]: No password set (set SENTRI_DB_{name_key}_PASSWORD)"
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


def _load_config_yaml() -> dict:
    """Load the raw YAML config, returning empty dict if missing."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_config_yaml(data: dict) -> None:
    """Write config dict back to sentri.yaml."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8"
    )


@db_cmd.command("add")
@click.argument("name")
@click.option(
    "--connect", required=True, help="Connection string (oracle://user@host:port/service)"
)
@click.option(
    "--env",
    "environment",
    required=True,
    type=click.Choice(["DEV", "UAT", "PROD"], case_sensitive=False),
    help="Environment tier",
)
@click.option("--aliases", default="", help="Comma-separated alias names (e.g. PRODDB,prod-01)")
@click.option(
    "--engine",
    default="oracle",
    type=click.Choice(["oracle", "postgres", "snowflake", "sqlserver"]),
    help="Database engine",
)
def db_add(name: str, connect: str, environment: str, aliases: str, engine: str):
    """Add a database to sentri.yaml.

    Example: sentri db add PROD-01 --connect oracle://user@host:1521/SID --env PROD
    """
    raw = _load_config_yaml()
    databases = raw.get("databases", [])

    # Check for duplicate
    for db in databases:
        if db.get("name") == name:
            console.print(
                f"[red]Database '{name}' already exists. Use 'sentri db remove {name}' first.[/red]"
            )
            return

    entry: dict = {
        "name": name,
        "connection_string": connect,
        "environment": environment.upper(),
    }
    if engine != "oracle":
        entry["db_engine"] = engine
    if aliases:
        entry["aliases"] = [a.strip() for a in aliases.split(",") if a.strip()]

    databases.append(entry)
    raw["databases"] = databases
    _save_config_yaml(raw)

    name_key = name.upper().replace("-", "_")
    console.print(f"\n[green]Added database '{name}' ({environment.upper()})[/green]\n")
    console.print("Next steps:")
    console.print(
        f"  1. Set password:  [bold]export SENTRI_DB_{name_key}_PASSWORD=your-password[/bold]"
    )
    console.print(f"  2. Test:          [bold]sentri db test --name {name}[/bold]")


@db_cmd.command("remove")
@click.argument("name")
def db_remove(name: str):
    """Remove a database from sentri.yaml.

    Example: sentri db remove PROD-01
    """
    raw = _load_config_yaml()
    databases = raw.get("databases", [])

    original_count = len(databases)
    databases = [db for db in databases if db.get("name") != name]

    if len(databases) == original_count:
        console.print(f"[yellow]Database '{name}' not found in config[/yellow]")
        return

    raw["databases"] = databases
    _save_config_yaml(raw)
    console.print(f"[green]Removed database '{name}' from config[/green]")
