"""sentri init - Initialize the runtime directory."""

import click
from rich.console import Console
from rich.tree import Tree

from sentri.config.initializer import initialize
from sentri.config.paths import SENTRI_HOME

console = Console()


@click.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing files")
def init_cmd(force: bool):
    """Initialize the Sentri runtime directory (~/.sentri/)."""
    console.print(f"\n[bold blue]Sentri[/bold blue] - Initializing at {SENTRI_HOME}\n")

    summary = initialize(force=force)

    tree = Tree(f"[bold]{SENTRI_HOME}[/bold]")

    if summary["directories"]:
        dirs_branch = tree.add("[green]Directories created[/green]")
        for d in summary["directories"]:
            dirs_branch.add(d.replace(str(SENTRI_HOME), "~/.sentri"))

    if summary["policies"]:
        pol_branch = tree.add("[cyan]Policy files installed[/cyan]")
        for p in summary["policies"]:
            pol_branch.add(p.replace(str(SENTRI_HOME), "~/.sentri"))

    if summary["config"]:
        cfg_branch = tree.add("[yellow]Configuration[/yellow]")
        for c in summary["config"]:
            cfg_branch.add(c.replace(str(SENTRI_HOME), "~/.sentri"))

    if summary["database"]:
        db_branch = tree.add("[magenta]Database[/magenta]")
        for d in summary["database"]:
            db_branch.add(d.replace(str(SENTRI_HOME), "~/.sentri"))

    console.print(tree)
    console.print("\n[bold green]Initialization complete.[/bold green]")
    console.print(
        "\nNext steps:\n"
        "  1. Edit [bold]~/.sentri/config/sentri.yaml[/bold] with your settings\n"
        "  2. Set environment variables for secrets (SENTRI_EMAIL_PASSWORD, etc.)\n"
        "  3. Run [bold]sentri start[/bold] to begin monitoring\n"
    )
