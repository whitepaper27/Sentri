"""Path resolution for the Sentri runtime directory.

By default, SENTRI_HOME is the project root (where pyproject.toml lives).
Override with SENTRI_HOME environment variable if needed.
"""

import os
from pathlib import Path


def _find_project_root() -> Path:
    """Find the project root by looking for pyproject.toml upward from this file."""
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    # Fallback: current working directory
    return Path.cwd()


# Base directory: project root (or SENTRI_HOME env var override)
SENTRI_HOME = Path(os.environ.get("SENTRI_HOME", str(_find_project_root())))

# Subdirectories
BRAIN_DIR = SENTRI_HOME / "brain"
AGENTS_DIR = SENTRI_HOME / "agents"
ALERTS_DIR = SENTRI_HOME / "alerts"
CHECKS_DIR = SENTRI_HOME / "checks"
ENVIRONMENTS_DIR = SENTRI_HOME / "environments"
WORKFLOWS_DIR = SENTRI_HOME / "workflows"
DATA_DIR = SENTRI_HOME / "data"
LOGS_DIR = SENTRI_HOME / "logs"
CONFIG_DIR = SENTRI_HOME / "config"

# v2.0 directories
LEARNING_DIR = DATA_DIR / "learning"
ALERTS_BACKUP_DIR = ALERTS_DIR / ".backup"
DOCS_DIR = SENTRI_HOME / "docs"

# v3.1 Ground Truth RAG docs
ORACLE_DOCS_DIR = DOCS_DIR / "oracle"

# v5.1b Multi-DB subdirectories (future-ready scaffolding)
ALERTS_ORACLE_DIR = ALERTS_DIR / "oracle"
ALERTS_POSTGRES_DIR = ALERTS_DIR / "postgres"
ALERTS_SNOWFLAKE_DIR = ALERTS_DIR / "snowflake"
ALERTS_SQLSERVER_DIR = ALERTS_DIR / "sqlserver"

CHECKS_ORACLE_DIR = CHECKS_DIR / "oracle"
CHECKS_POSTGRES_DIR = CHECKS_DIR / "postgres"
CHECKS_SNOWFLAKE_DIR = CHECKS_DIR / "snowflake"
CHECKS_SQLSERVER_DIR = CHECKS_DIR / "sqlserver"

POSTGRES_DOCS_DIR = DOCS_DIR / "postgres"
SNOWFLAKE_DOCS_DIR = DOCS_DIR / "snowflake"
SQLSERVER_DOCS_DIR = DOCS_DIR / "sqlserver"

# v5.x Investigation analysis files
INVESTIGATIONS_DIR = SENTRI_HOME / "investigations"

# Key files
DB_PATH = DATA_DIR / "sentri.db"
LOG_PATH = LOGS_DIR / "sentri.log"
CONFIG_PATH = CONFIG_DIR / "sentri.yaml"

# All directories that must exist
ALL_DIRS = [
    SENTRI_HOME,
    BRAIN_DIR,
    AGENTS_DIR,
    ALERTS_DIR,
    CHECKS_DIR,
    ENVIRONMENTS_DIR,
    WORKFLOWS_DIR,
    DATA_DIR,
    LOGS_DIR,
    CONFIG_DIR,
    LEARNING_DIR,
    ALERTS_BACKUP_DIR,
    DOCS_DIR,
    ORACLE_DOCS_DIR,
    INVESTIGATIONS_DIR,
    # v5.1b Multi-DB subdirectories
    ALERTS_ORACLE_DIR,
    ALERTS_POSTGRES_DIR,
    ALERTS_SNOWFLAKE_DIR,
    ALERTS_SQLSERVER_DIR,
    CHECKS_ORACLE_DIR,
    CHECKS_POSTGRES_DIR,
    CHECKS_SNOWFLAKE_DIR,
    CHECKS_SQLSERVER_DIR,
    POSTGRES_DOCS_DIR,
    SNOWFLAKE_DOCS_DIR,
    SQLSERVER_DOCS_DIR,
]


def get_default_policies_path() -> Path:
    """Return the path to bundled default policy files."""
    return Path(__file__).parent.parent / "_default_policies"


def ensure_dirs() -> None:
    """Create all required directories if they don't exist."""
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
