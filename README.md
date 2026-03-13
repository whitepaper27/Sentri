# Sentri — AI-Powered Autonomous DBA Agent

> Detects, diagnoses, and fixes Oracle database problems automatically.
> Drop a `.md` file to add new alert types — zero code changes.

[![CI](https://github.com/whitepaper27/sentri/actions/workflows/ci.yml/badge.svg)](https://github.com/whitepaper27/Sentri/actions)
[![PyPI](https://img.shields.io/pypi/v/sentri-dba)](https://pypi.org/project/sentri-dba/)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-Apache%202.0-green)
![Tests](https://img.shields.io/badge/tests-815%20passed-brightgreen)

<p align="center">
  <img src="Sentri_Full_Demo.gif" alt="Sentri Full Demo" width="720">
</p>

---

## What Sentri Does

Sentri monitors your DBA alert emails, verifies problems against real database state, investigates using 12 specialized DBA tools, generates multiple fix candidates, scores them against configurable criteria, and executes the best option — with full rollback guarantee and immutable audit trail.

**Works without an LLM API key** using template-based fixes. Add Claude, OpenAI, or Gemini for intelligent investigation and multi-candidate scoring.

### Who Is It For?

- DBA teams managing 1–50 Oracle databases
- Running OEM or monitoring that sends email alerts
- Want autonomous fixes for common issues (tablespace, temp, slow SQL, blocking sessions)
- Comfortable with Python, direct database connections, YAML config

---

## How It Works

```
  ┌──────────┐   ┌──────────────┐
  │  SCOUT   │   │  PROACTIVE   │
  │ (email)  │   │  (scheduled) │
  └────┬─────┘   └──────┬───────┘
       └───────┬────────┘
               v
  ┌──────────────────────────┐
  │       SUPERVISOR         │  Deterministic router
  │  (correlate + route)     │  (not an LLM call)
  └──────────┬───────────────┘
             |
    ┌────────┼──────────┬──────────┐
    v        v          v          v
 Storage   SQL       RCA       Future
 Agent     Tuning    Agent     Agent
           Agent
    └────────┼──────────┼──────────┘
             v
  ┌──────────────────────────┐
  │      SAFETY MESH         │  5 structural checks
  │  (policy, blast radius,  │  (LLM cannot bypass)
  │   conflicts, rollback)   │
  └──────────┬───────────────┘
             v
  ┌──────────────────────────┐
  │      EXECUTOR            │  Pre/post metrics
  │  (execute + rollback)    │  Auto-rollback on failure
  └──────────────────────────┘
```

1. **Scout** monitors your IMAP inbox for DBA alert emails
2. **Proactive Agent** runs scheduled health checks (stale stats, tablespace trends, etc.)
3. **Supervisor** correlates alerts on the same database and routes to the right specialist
4. **Specialist agents** investigate with 12 DBA tools, then generate multiple fix candidates
5. **Safety Mesh** enforces 5 structural checks before any SQL touches the database
6. **Executor** runs the fix with pre/post metrics, auto-rollback on failure
7. **Analyst** learns from outcomes to improve future decisions

---

## Quick Start

### 1. Install

```bash
pip install sentri-dba
```

Or from source:

```bash
git clone https://github.com/whitepaper27/sentri.git
cd sentri
pip install -e ".[dev,llm]"
```

**Requirements**: Python 3.10+

### 2. Initialize

```bash
sentri init
```

Creates the `~/.sentri/` directory with default policy files, SQLite database, and config template.

### 3. Configure

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
    username: sentri_ro
    aliases: [PRODDB, prod-db-07, PROD07]

  - name: DEV-DB-01
    connection_string: oracle://sentri_agent@dev-db-01:1521/DEVDB
    environment: DEV
    aliases: [DEVDB, dev-db-01]

approvals:
  email_enabled: true
  approval_recipients: ["dba-team@company.com"]
  approval_timeout: 3600

monitoring:
  log_level: INFO
  scout_poll_interval: 60
```

### 4. Set Credentials

Passwords are set via environment variables, never stored in config:

```bash
export SENTRI_EMAIL_PASSWORD="your-imap-password"

# Pattern: SENTRI_DB_<NAME>_PASSWORD (uppercase, hyphens → underscores)
export SENTRI_DB_PROD_DB_07_PASSWORD="prod-password"
export SENTRI_DB_DEV_DB_01_PASSWORD="dev-password"

# Optional: LLM provider
export ANTHROPIC_API_KEY="sk-..."   # or OPENAI_API_KEY or GOOGLE_API_KEY
```

### 5. Start

```bash
sentri db test          # Verify Oracle connectivity
sentri start            # Start monitoring
```

---

## Docker Demo (Try Without Installing)

See the full pipeline in action — no Oracle install, no email, no LLM key needed:

```bash
cd docker
docker-compose up --build
```

This starts Oracle XE + Sentri. Once Oracle is healthy, Sentri runs the demo automatically:

```
Sentri Demo
  Alert type: tablespace_full
  Database:   demo-oracle (DEV)

  [ok] DETECTED    Alert injected
  [ok] VERIFIED    Confirmed (confidence: 0.80)
  [ok] PRE_FLIGHT  Safety Mesh: 5/5 checks passed
  [ok] EXECUTING   ALTER TABLESPACE ADD DATAFILE SIZE 10G
  [ok] COMPLETED   Fix applied successfully

  Duration: 7.0s
```

Or run the demo locally if you already have an Oracle database:

```bash
sentri demo                          # Uses first DEV database
sentri demo --alert-type temp_full   # Try different alert types
sentri demo --dry-run                # See what would happen
```

---

## Add a New Alert Type (Zero Code)

Drop a `.md` file in `~/.sentri/alerts/`:

```markdown
---
alert_type: tablespace_full
severity: HIGH
risk_level: MEDIUM
action_type: ADD_DATAFILE
---

## Email Pattern
(?i)tablespace\s+(\S+)\s+.*?(\d+(?:\.\d+)?)\s*%.*?(?:on|database)\s+(\S+)

## Verification Query
SELECT tablespace_name, ROUND(used_percent,1) as used_percent
FROM dba_tablespace_usage_metrics
WHERE tablespace_name = :tablespace_name

## Forward Action
ALTER TABLESPACE :tablespace_name ADD DATAFILE SIZE 10G AUTOEXTEND ON NEXT 1G MAXSIZE 32G

## Rollback Action
ALTER TABLESPACE :tablespace_name DROP DATAFILE ':new_datafile_path'
```

No enum, no code change, no restart. Sentri picks it up on the next poll cycle.

---

## Features

### Intelligence

- **12 DBA investigation tools** — tablespace info, SQL plans, session diagnostics, wait events, table stats, index info, top SQL
- **Argue/select pattern** — generates 3–5 fix candidates, LLM judge scores each against configurable criteria
- **Ground truth RAG** — verified Oracle syntax docs prevent SQL hallucination
- **Short-term memory** (24h context) + **long-term patterns** (90-day history)
- **Three-level fallback** — Agentic (LLM + tools) → One-shot (LLM only) → Template (zero LLM cost)

### Safety

- **5-check Safety Mesh** — policy gate, conflict detection, blast radius, circuit breaker, rollback guarantee
- **Structural enforcement** — the architecture prevents dangerous SQL from reaching execution, not prompts
- **Confidence-based routing** — auto-execute in DEV, require approval in PROD
- **Auto-rollback** on post-execution validation failure
- **Immutable audit trail** for every action

### Autonomy

- **4 specialist agents** — Storage, SQL Tuning, RCA (root cause analysis), Proactive Health
- **Proactive health checks** — catch problems BEFORE they trigger alerts (stale stats, tablespace trends, etc.)
- **Cost gate** — historical success rate determines LLM depth (most alerts = zero LLM cost)
- **Email approval flow** — DBA replies APPROVED/DENIED to approval emails
- **Full notification coverage** — completion, escalation, timeout, and denial emails at every terminal workflow state
- **RCA recommendations** — repeat alerts trigger root cause investigation recommendations in completion emails (configurable threshold in `brain/rules.md`)

### Extensibility

- **`.md`-driven everything** — alerts, health checks, policies, agent behavior. All blocking decisions belong to DBAs via `.md` files — Sentri never imposes hardcoded restrictions
- **Multi-provider LLM** — Claude, OpenAI, Gemini, or no LLM at all
- **9 alert types** out of the box, add more by dropping a file
- **7 proactive health checks** included

---

## Supported Alert Types

| Alert | Status | Specialist |
|-------|--------|------------|
| Tablespace Full | Working | Storage Agent |
| Temp Tablespace Full | Ready | Storage Agent |
| Archive Destination Full | Ready | Storage Agent |
| High Undo Usage | Ready | Storage Agent |
| Long Running SQL | Ready | SQL Tuning Agent |
| CPU High | Ready | SQL Tuning Agent |
| Session Blocker | Ready | RCA Agent |
| Listener Down | Planned | — |
| Archive Gap (Data Guard) | Planned | — |

## Proactive Health Checks

| Check | Schedule | Routes To |
|-------|----------|-----------|
| Stale Statistics | Every 6 hours | SQL Tuning Agent |
| Tablespace Trend | Every 6 hours | Storage Agent |
| Index Usage | Daily | SQL Tuning Agent |
| Redo Log Sizing | Every 6 hours | Storage Agent |
| Temp Growth Trend | Every 6 hours | Storage Agent |
| Password Expiry | Daily | — (escalate) |
| Backup Freshness | Daily | — (escalate) |

---

## Environment Tiers

| Environment | Autonomy | Behavior |
|-------------|----------|----------|
| **DEV** | AUTONOMOUS | Auto-execute all fixes, notify after |
| **UAT** | SUPERVISED | Auto-execute low-risk, require approval for high-risk |
| **PROD** | ADVISORY | Always require human approval before execution |

---

## CLI Commands

```bash
# Setup
sentri init                              # Initialize ~/.sentri/ directory
sentri db list                           # List configured databases
sentri db test                           # Test Oracle connectivity

# Monitoring
sentri start                             # Start the monitoring daemon

# Workflows
sentri stats                             # Workflow statistics + success rate
sentri list                              # Recent workflows
sentri list --status AWAITING_APPROVAL   # Filter by status
sentri show <workflow_id>                # Full workflow detail with SQL

# Approvals
sentri approve <workflow_id>             # Approve a pending workflow
sentri approve <id> --deny --reason "..."  # Deny with reason
sentri resolve <id> --reason "Fixed manually"  # Manual DBA resolution

# Demo
sentri demo                              # Run demo (full pipeline, real Oracle)
sentri demo --alert-type temp_full       # Demo a different alert type
sentri demo --dry-run                    # Preview without executing

# Operations
sentri audit                             # View audit log
sentri show-profile <db>                 # Database profile
sentri profiles                          # All profiled databases
sentri install-service                   # Generate systemd service config
```

---

## Configuration Reference

Each database entry in `sentri.yaml` supports:

```yaml
databases:
  - name: PROD-DB-07                       # REQUIRED: Unique identifier
    connection_string: oracle://user@host:port/service  # REQUIRED
    environment: PROD                      # REQUIRED: DEV, UAT, or PROD
    # --- Optional ---
    username: sentri_ro                    # Override user from connection string
    aliases: [PRODDB, prod-db-07]          # Alternate names in alert emails
    autonomy_level: ADVISORY               # Per-DB override
    oracle_version: "19c"
    architecture: RAC                      # STANDALONE, CDB, RAC
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `SENTRI_EMAIL_PASSWORD` | IMAP email password |
| `SENTRI_DB_<NAME>_PASSWORD` | Per-database Oracle password |
| `SENTRI_DB_<NAME>_USERNAME` | Per-database Oracle username override |
| `SENTRI_SLACK_WEBHOOK_URL` | Slack notification webhook |
| `ANTHROPIC_API_KEY` | Claude API key (optional) |
| `OPENAI_API_KEY` | OpenAI API key (optional) |
| `GOOGLE_API_KEY` | Gemini API key (optional) |

The `<NAME>` pattern: uppercase the database name, replace hyphens with underscores.
`PROD-DB-07` becomes `SENTRI_DB_PROD_DB_07_PASSWORD`.

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | Install, configure, and start monitoring |
| [Configuration Reference](docs/configuration.md) | Every `sentri.yaml` field explained |
| [Adding Alerts & Checks](docs/adding-alerts.md) | Add new alert types by dropping a `.md` file |
| [Architecture Overview](docs/architecture.md) | How agents, routing, and safety work |
| [Safety Model](docs/safety-model.md) | How Sentri keeps your databases safe |
| [FAQ](docs/faq.md) | Common questions and answers |

---

## Customizing Policies

Sentri's behavior is driven by `.md` policy files that DBAs can edit without writing code.

### Alert Patterns (`~/.sentri/alerts/`)

Each `.md` file defines: email pattern (regex), verification query (SQL), forward action (fix SQL), rollback action (undo SQL), and validation query (confirm fix). See the [alerts/ directory](alerts/) for examples and the [Adding Alerts guide](docs/adding-alerts.md) for a step-by-step walkthrough.

### Health Checks (`~/.sentri/checks/`)

Same pattern as alerts — drop a `.md` file with a health query, threshold, and recommended action. See the [checks/ directory](checks/) for examples and the [Adding Alerts guide](docs/adding-alerts.md#adding-a-proactive-health-check) for details.

### Brain Policies (`~/.sentri/brain/`)

- `autonomy_levels.md` — Which environments auto-execute vs. require approval
- `routing_rules.md` — How alerts map to specialist agents
- `state_machine.md` — Valid workflow state transitions
- `locking_rules.md` — Prevent concurrent operations on the same resource
- `global_policy.md` — Safety constraints and override rules

Edit any `.md` file to change behavior. Changes take effect on next policy reload.

---

## Runtime Directory

```
~/.sentri/
├── brain/              # Core policies (autonomy, routing, safety)
├── agents/             # Agent behavior configurations
├── alerts/             # Alert patterns (drop .md to add new types)
├── checks/             # Health check definitions (drop .md to add)
├── environments/       # Database inventory
├── workflows/          # Workflow specifications
├── docs/oracle/        # Verified Oracle syntax (ground truth)
├── data/sentri.db      # SQLite database (WAL mode)
├── logs/sentri.log     # Application log
└── config/sentri.yaml  # Your configuration
```

---

## Running as a Service

### Linux (systemd)

```bash
sentri install-service
sudo cp sentri.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sentri
sudo systemctl start sentri
```

### Windows

```bash
sentri start
```

Or use NSSM for background service management.

---

## Workflow States

```
DETECTED --> VERIFYING --> VERIFIED --> EXECUTING --> COMPLETED
                |              |            |
                v              v            v
         VERIFICATION    AWAITING      ROLLED_BACK
            FAILED       APPROVAL          |
                           |    \          v
                           v     v       FAILED
                       APPROVED  DENIED
                                 TIMEOUT
```

---

## Development

```bash
# Install with all dependencies
pip install -e ".[dev,llm]"

# Run tests (815 tests)
python -m pytest tests/ -x -q --ignore=tests/integration --ignore=tests/e2e

# Lint
ruff check src/ tests/

# Format check
ruff format --check src/ tests/
```

CI runs automatically on push/PR via GitHub Actions (Python 3.10, 3.11, 3.12).

## Tech Stack

- **Python 3.10+**
- **SQLite** (WAL mode) — single-file persistence, zero setup
- **python-oracledb** — Oracle connectivity (thin mode, no Oracle Client needed)
- **Click** + **Rich** — CLI framework with formatted terminal output
- **Anthropic / OpenAI / Google AI** — optional LLM providers

## License

Apache License 2.0 — See [LICENSE](LICENSE)
