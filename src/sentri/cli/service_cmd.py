"""sentri install-service - Install as system service."""

import sys

import click
from rich.console import Console

console = Console()

SYSTEMD_UNIT = """\
[Unit]
Description=Sentri L3 DBA Agent
After=network.target

[Service]
Type=simple
User={user}
ExecStart={python} -m sentri start
Restart=always
RestartSec=10

# Environment variables for secrets
# EnvironmentFile=/etc/sentri/env

[Install]
WantedBy=multi-user.target
"""


@click.command("install-service")
@click.option("--user", default="oracle", help="System user to run as")
def service_cmd(user: str):
    """Generate a systemd service unit file."""
    if sys.platform == "win32":
        console.print("\n[bold blue]Sentri Windows Service[/bold blue]\n")
        console.print("To run Sentri as a Windows service, use NSSM or Task Scheduler:")
        console.print(f'\n  nssm install Sentri "{sys.executable}" "-m sentri start"')
        console.print(f'  nssm set Sentri AppDirectory "{sys.prefix}"')
        console.print("  nssm start Sentri\n")
        return

    python_path = sys.executable
    unit_content = SYSTEMD_UNIT.format(user=user, python=python_path)

    console.print("\n[bold blue]Sentri Systemd Service[/bold blue]\n")
    console.print("Save the following to [bold]/etc/systemd/system/sentri.service[/bold]:\n")
    console.print(unit_content)
    console.print("Then run:")
    console.print("  sudo systemctl daemon-reload")
    console.print("  sudo systemctl enable sentri")
    console.print("  sudo systemctl start sentri")
    console.print()
