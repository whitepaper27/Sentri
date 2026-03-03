"""Sentri CLI entry point."""

from pathlib import Path

import click
from dotenv import load_dotenv

from sentri import __version__

# Load .env file if it exists
_env_file = Path.cwd() / ".env"
if _env_file.exists():
    load_dotenv(_env_file)


@click.group()
@click.version_option(version=__version__, prog_name="sentri")
def cli():
    """Sentri - AI-Powered L3 DBA Agent System."""


# Import and register subcommands
from sentri.cli.approve_cmd import approve_cmd  # noqa: E402
from sentri.cli.audit_cmd import audit_cmd  # noqa: E402
from sentri.cli.cleanup_cmd import cleanup_cmd  # noqa: E402
from sentri.cli.db_cmd import db_cmd  # noqa: E402
from sentri.cli.demo_cmd import demo_cmd  # noqa: E402
from sentri.cli.init_cmd import init_cmd  # noqa: E402
from sentri.cli.learning_cmd import (  # noqa: E402
    learning_summary_cmd,
    rollback_improvement_cmd,
    versions_cmd,
)
from sentri.cli.list_cmd import list_cmd  # noqa: E402
from sentri.cli.profile_cmd import profiles_cmd, show_profile_cmd  # noqa: E402
from sentri.cli.resolve_cmd import resolve_cmd  # noqa: E402
from sentri.cli.service_cmd import service_cmd  # noqa: E402
from sentri.cli.show_cmd import show_cmd  # noqa: E402
from sentri.cli.start_cmd import start_cmd  # noqa: E402
from sentri.cli.stats_cmd import stats_cmd  # noqa: E402

cli.add_command(init_cmd)
cli.add_command(start_cmd)
cli.add_command(stats_cmd)
cli.add_command(list_cmd)
cli.add_command(show_cmd)
cli.add_command(audit_cmd)
cli.add_command(service_cmd)
cli.add_command(db_cmd)
cli.add_command(show_profile_cmd)
cli.add_command(profiles_cmd)
cli.add_command(learning_summary_cmd)
cli.add_command(versions_cmd)
cli.add_command(rollback_improvement_cmd)
cli.add_command(cleanup_cmd)
cli.add_command(approve_cmd)
cli.add_command(resolve_cmd)
cli.add_command(demo_cmd)
