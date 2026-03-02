# Sentri

**AI-Powered L3 DBA Agent System** -- Autonomous Oracle database issue detection and resolution.

Sentri monitors your DBA alert emails, verifies issues against live Oracle databases, and automatically resolves them (DEV/UAT) or routes for human approval (PROD). Zero-touch remediation for common database alerts.

## How It Works

```
DBA Alert Email
     |
     v
 +-------+     +---------+     +--------------+     +----------+
 | Scout  | --> | Auditor | --> | Orchestrator | --> | Executor |
 | (parse)|    | (verify) |    |   (route)    |    |  (fix)   |
 +-------+     +---------+     +--------------+     +----------+
                                      |
                                      v
                              Approval Flow (PROD)
```

1. **Scout** monitors your alert inbox via IMAP, matches emails against known patterns
2. **Auditor** connects to the target Oracle DB (read-only) and verifies the issue is real
3. **Orchestrator** checks the environment tier and routes the workflow
4. **Executor** applies the fix with rollback capability, then validates the result

## Supported Alert Types

| Alert | Detection | Auto-Fix |
|-------|-----------|----------|
| Tablespace Full | ORA-01652, usage % alerts | `ALTER TABLESPACE ADD DATAFILE` |
| Archive Destination Full | ORA-19809, archive % alerts | Purge expired archivelogs via RMAN |
| Temp Tablespace Full | ORA-01652 (temp), temp usage % | `ALTER TABLESPACE ADD TEMPFILE` |
| Listener Down | TNS-12541, listener status alerts | `lsnrctl start` |
| Archive Gap (Data Guard) | SCN gap alerts | `ALTER DATABASE RECOVER` |

## Environment Tiers

| Environment | Autonomy | Behavior |
|-------------|----------|----------|
| **DEV** | AUTONOMOUS | Auto-execute all fixes, notify after |
| **UAT** | SUPERVISED | Auto-execute low-risk, require approval for high-risk |
| **PROD** | ADVISORY | Always require human approval before execution |

---

## Installation

```bash
pip install sentri
```

Or install from source:

```bash
git clone https://github.com/your-org/sentri.git
cd sentri
pip install -e .
```

**Requirements**: Python 3.10+

## Quick Start

### 1. Initialize

```bash
sentri init
```

This creates the `~/.sentri/` directory with default policy files, a SQLite database, and a config template.

### 2. Configure Databases

Edit `~/.sentri/config/sentri.yaml`:

```yaml
email:
  imap_server: imap.gmail.com
  imap_port: 993
  username: dba-alerts@company.com
  use_ssl: true

databases:
  - name: PROD-DB-07
    connection_string: oracle://sentri_agent@prod-scan:1521/PRODDB
    environment: PROD
    username: sentri_ro                    # Per-DB username (overrides URL)
    aliases: [PRODDB, prod-db-07, PROD07]  # Names this DB may appear as in emails

  - name: UAT-DB-03
    connection_string: oracle://sentri_agent@uat-db-03:1521/UATDB
    environment: UAT
    aliases: [UATDB, uat-db-03]

  - name: DEV-DB-01
    connection_string: oracle://sentri_agent@dev-db-01:1521/DEVDB
    environment: DEV
    username: sentri_admin                 # Full-access user for DEV
    aliases: [DEVDB, dev-db-01]

approvals:
  slack_webhook_url: ""  # Or set SENTRI_SLACK_WEBHOOK_URL env var
  approval_timeout: 3600

monitoring:
  log_level: INFO
  scout_poll_interval: 60
  orchestrator_poll_interval: 10
```

### 3. Set Credentials

Passwords and sensitive values are set via environment variables, never stored in YAML:

```bash
# Email password
export SENTRI_EMAIL_PASSWORD="your-imap-password"

# Database passwords (pattern: SENTRI_DB_<NAME>_PASSWORD)
# Replace hyphens with underscores, uppercase the name
export SENTRI_DB_PROD_DB_07_PASSWORD="prod-password"
export SENTRI_DB_UAT_DB_03_PASSWORD="uat-password"
export SENTRI_DB_DEV_DB_01_PASSWORD="dev-password"

# Optional: override username via env var
export SENTRI_DB_PROD_DB_07_USERNAME="sentri_readonly"

# Slack webhook (optional)
export SENTRI_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

### 4. Verify Connectivity

```bash
# List all configured databases
sentri db list

# Test Oracle connectivity
sentri db test

# Test a specific database
sentri db test --name PROD-DB-07
```

### 5. Start Monitoring

```bash
sentri start
```

Sentri runs in the foreground. It starts the Scout (email polling) and Orchestrator (workflow processing) in parallel.

Press `Ctrl+C` to stop gracefully.

---

## Database Configuration Reference

Each database entry in `sentri.yaml` supports these fields:

```yaml
databases:
  - name: PROD-DB-07                       # REQUIRED: Unique identifier
    connection_string: oracle://user@host:port/service  # REQUIRED: Oracle DSN
    environment: PROD                      # REQUIRED: DEV, UAT, or PROD
    # --- Optional fields below ---
    username: sentri_ro                    # Override user from URL
    aliases: [PRODDB, prod-db-07]          # Alternate names in alert emails
    autonomy_level: ADVISORY               # AUTONOMOUS, SUPERVISED, ADVISORY
    oracle_version: "19c"
    architecture: RAC                      # STANDALONE, CDB, RAC
    critical_schemas: "FINANCE,HR"         # Comma-separated
    business_owner: "Jane Smith"
    dba_owner: "John Doe"
```

### Connection String Format

```
oracle://username@hostname:port/service_name
```

The `username` in the URL is used by default. To override it per-database, set the `username` field or use the `SENTRI_DB_<NAME>_USERNAME` env var.

### Database Name Aliases

Alert emails may refer to a database by different names. The `aliases` field maps alternate names to the canonical config name:

```yaml
- name: PROD-DB-07                    # Canonical name in Sentri
  aliases: [PRODDB, prod-db-07, P07]  # Names that may appear in emails
```

When Scout parses an email containing "PRODDB", it resolves the alias to "PROD-DB-07" and routes the workflow correctly.

### Scaling to Many Databases

The YAML config scales to hundreds of databases. For large deployments:

```yaml
databases:
  # Minimal config per DB (3 required fields + password env var)
  - name: DB-001
    connection_string: oracle://sentri@db001:1521/SVC001
    environment: DEV

  - name: DB-002
    connection_string: oracle://sentri@db002:1521/SVC002
    environment: DEV

  # ... repeat for all databases
```

Set all passwords at once:

```bash
export SENTRI_DB_DB_001_PASSWORD="pass1"
export SENTRI_DB_DB_002_PASSWORD="pass2"
# ...
```

---

## CLI Commands

```bash
sentri init                              # Initialize ~/.sentri/ directory
sentri start                             # Start the monitoring daemon
sentri db list                           # List all configured databases
sentri db test                           # Test connectivity to all databases
sentri db test --name DEV-DB-01          # Test a specific database
sentri stats                             # Show workflow statistics
sentri list                              # List recent workflows
sentri list --status AWAITING_APPROVAL   # Filter by status
sentri show <workflow_id>                # Show workflow details
sentri audit                             # View audit log
sentri install-service                   # Generate systemd service file
```

## Runtime Directory

```
~/.sentri/
├── brain/              # Core policies (autonomy, state machine, locking)
├── agents/             # Agent behavior configurations
├── alerts/             # Alert patterns (regex, SQL, forward/rollback actions)
├── environments/       # Database inventory
├── workflows/          # Workflow specifications
├── data/sentri.db      # SQLite database (WAL mode)
├── logs/sentri.log     # Application log
└── config/sentri.yaml  # Your configuration
```

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `SENTRI_EMAIL_PASSWORD` | IMAP email password | |
| `SENTRI_DB_<NAME>_PASSWORD` | Per-database Oracle password | `SENTRI_DB_PROD_DB_07_PASSWORD` |
| `SENTRI_DB_<NAME>_USERNAME` | Per-database Oracle username override | `SENTRI_DB_PROD_DB_07_USERNAME` |
| `SENTRI_SLACK_WEBHOOK_URL` | Slack notification webhook | |
| `SENTRI_LOG_LEVEL` | Logging level (default: INFO) | `DEBUG`, `WARNING` |

The `<NAME>` pattern: uppercase the database name and replace hyphens with underscores.
`PROD-DB-07` becomes `PROD_DB_07`, so the env var is `SENTRI_DB_PROD_DB_07_PASSWORD`.

## Customizing Policies

Sentri's behavior is driven by `.md` policy files that DBAs can edit without writing code.

### Alert Patterns (`~/.sentri/alerts/`)

Each alert type is a markdown file containing:
- **Email Pattern**: Regex to match incoming alert emails
- **Extracted Fields**: What data to pull from the regex match
- **Verification Query**: SQL to confirm the issue on the live database
- **Tolerance**: How much drift is acceptable between email and reality
- **Forward Action**: SQL to fix the issue
- **Rollback Action**: SQL to undo the fix if it fails
- **Validation Query**: SQL to confirm the fix worked

Example from `alerts/tablespace_full.md`:

```markdown
## Email Pattern
(?i)tablespace\s+(\S+)\s+.*?(\d+(?:\.\d+)?)\s*%\s*(?:full|capacity|used).*?(?:on|database)\s+(\S+)

## Forward Action
ALTER TABLESPACE :tablespace_name ADD DATAFILE SIZE 10G AUTOEXTEND ON NEXT 1G MAXSIZE 32G;

## Rollback Action
ALTER TABLESPACE :tablespace_name DROP DATAFILE ':new_datafile_path';
```

### Brain Policies (`~/.sentri/brain/`)

- `autonomy_levels.md` -- Which environments auto-execute vs. require approval
- `state_machine.md` -- Valid workflow state transitions
- `locking_rules.md` -- Prevent concurrent operations on the same resource
- `global_policy.md` -- Safety constraints and override rules

Edit any `.md` file to change behavior. Changes take effect on next policy reload.

## Running as a Service

### Linux (systemd)

```bash
# Generate the service file
sentri install-service

# Install and start
sudo cp sentri.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sentri
sudo systemctl start sentri
```

### Windows

Run in the foreground or use a process manager like NSSM:

```bash
sentri start
```

## Workflow States

Every alert goes through a state machine:

```
DETECTED --> VERIFYING --> VERIFIED --> EXECUTING --> COMPLETED
                |              |            |
                v              v            v
         VERIFICATION    AWAITING      ROLLED_BACK
            FAILED       APPROVAL          |
                              |            v
                              v          FAILED
                          APPROVED
                          DENIED
                          TIMEOUT
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=sentri

# Lint
ruff check src/

# Type check
mypy src/sentri/
```

## Tech Stack

- **Python 3.10+**
- **SQLite** (WAL mode) -- single-file persistence, zero setup
- **python-oracledb** -- Oracle database connectivity (thin mode, no Oracle Client needed)
- **Click** -- CLI framework
- **Rich** -- Terminal formatting
- **PyYAML** -- Configuration

## License

Apache License 2.0 -- See [LICENSE](LICENSE)
