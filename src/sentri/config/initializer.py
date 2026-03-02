"""Initialize the Sentri runtime directory (~/.sentri/)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sentri.config.paths import (
    ALL_DIRS,
    CONFIG_PATH,
    DB_PATH,
    SENTRI_HOME,
    get_default_policies_path,
)
from sentri.db.connection import Database

logger = logging.getLogger("sentri.config.init")

# Policy subdirectories to copy (flat — *.md only)
POLICY_DIRS = ["brain", "agents", "alerts", "checks", "environments", "workflows"]

# Doc directories to copy (recursive — preserves subfolder structure)
DOC_DIRS = ["docs/oracle"]


def initialize(force: bool = False) -> dict[str, list[str]]:
    """Create the full Sentri runtime directory structure.

    Returns a summary dict of what was created.
    """
    summary: dict[str, list[str]] = {
        "directories": [],
        "policies": [],
        "config": [],
        "database": [],
    }

    # 1. Create directories
    for d in ALL_DIRS:
        if not d.exists() or force:
            d.mkdir(parents=True, exist_ok=True)
            summary["directories"].append(str(d))

    # 2. Copy default policy files
    defaults_path = get_default_policies_path()
    if defaults_path.exists():
        for policy_dir in POLICY_DIRS:
            src_dir = defaults_path / policy_dir
            dst_dir = SENTRI_HOME / policy_dir
            if not src_dir.exists():
                continue
            dst_dir.mkdir(parents=True, exist_ok=True)
            # Recursive copy: preserves subdirectory structure (e.g., alerts/oracle/)
            for src_file in src_dir.rglob("*.md"):
                rel = src_file.relative_to(src_dir)
                dst_file = dst_dir / rel
                if not dst_file.exists() or force:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    summary["policies"].append(str(dst_file))
        # 2b. Copy doc directories (recursive — preserves version/topic structure)
        for doc_rel in DOC_DIRS:
            src_dir = defaults_path / doc_rel
            dst_dir = SENTRI_HOME / doc_rel
            if not src_dir.exists():
                continue
            for src_file in src_dir.rglob("*.md"):
                rel = src_file.relative_to(src_dir)
                dst_file = dst_dir / rel
                if not dst_file.exists() or force:
                    dst_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    summary["policies"].append(str(dst_file))
    else:
        logger.warning("Default policies not found at %s", defaults_path)

    # 3. Create default config file
    if not CONFIG_PATH.exists() or force:
        _create_default_config(CONFIG_PATH)
        summary["config"].append(str(CONFIG_PATH))

    # 4. Initialize SQLite database (creates tables + runs v2.0 migrations)
    is_new_db = not DB_PATH.exists()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = Database(DB_PATH)
    db.initialize_schema()  # Also runs pending migrations via connection.py
    db.close()
    if is_new_db or force:
        summary["database"].append(str(DB_PATH))

    return summary


def _create_default_config(path: Path) -> None:
    """Write a default sentri.yaml config template."""
    template = """\
# Sentri Configuration
# Secrets should be set via environment variables (SENTRI_*), not in this file.
# Passwords: SENTRI_DB_<NAME>_PASSWORD (e.g., SENTRI_DB_DEV_DB_01_PASSWORD)
# Usernames: SENTRI_DB_<NAME>_USERNAME (optional override via env var)

email:
  imap_server: imap.gmail.com
  imap_port: 993
  username: dba-alerts@company.com
  use_ssl: true
  # password: Set SENTRI_EMAIL_PASSWORD env var

databases:
  - name: DEV-DB-01
    connection_string: oracle://sentri_agent@dev-db-01:1521/DEVDB
    environment: DEV
    # username: sentri_agent           # Per-DB username (overrides URL)
    # aliases: [DEVDB, dev-db-01]      # Alternate names in alert emails
    # autonomy_level: AUTONOMOUS       # Defaults based on environment
    # oracle_version: "19c"
    # architecture: STANDALONE         # STANDALONE, CDB, RAC
    # critical_schemas: ""
    # business_owner: ""
    # dba_owner: ""
    # password: Set SENTRI_DB_DEV_DB_01_PASSWORD env var

  - name: UAT-DB-03
    connection_string: oracle://sentri_agent@uat-db-03:1521/UATDB
    environment: UAT
    # username: sentri_agent
    # aliases: [UATDB, uat-db-03]
    # password: Set SENTRI_DB_UAT_DB_03_PASSWORD env var

  - name: PROD-DB-07
    connection_string: oracle://sentri_agent@prod-scan:1521/PRODDB
    environment: PROD
    # username: sentri_ro              # Read-only user for PROD
    # aliases: [PRODDB, prod-db-07]
    # password: Set SENTRI_DB_PROD_DB_07_PASSWORD env var

approvals:
  slack_webhook_url: ""  # Set SENTRI_SLACK_WEBHOOK_URL env var
  approval_timeout: 3600  # 1 hour

monitoring:
  log_level: INFO
  scout_poll_interval: 60
  orchestrator_poll_interval: 10
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
