# Getting Started

This guide walks you through installing Sentri, connecting it to your Oracle databases, and starting autonomous monitoring.

---

## Prerequisites

Before you begin, make sure you have:

- **Python 3.10+** installed
- **Network access** to your Oracle databases (Sentri uses thin-mode connections — no Oracle Client needed)
- **An IMAP mailbox** that receives DBA alert emails (from OEM, Zabbix, Nagios, or any tool that sends email)
- **(Optional)** An LLM API key from Claude, OpenAI, or Gemini — Sentri works without one using template-based fixes

---

## 1. Install

```bash
pip install sentri-dba
```

Or with LLM support (Claude, OpenAI, Gemini):

```bash
pip install "sentri-dba[llm]"
```

Or from source:

```bash
git clone https://github.com/whitepaper27/sentri.git
cd sentri
pip install -e ".[dev,llm]"
```

---

## 2. Initialize

```bash
sentri init
```

This creates the `~/.sentri/` directory with:

- Default policy files (`brain/`, `agents/`, `alerts/`, `checks/`)
- SQLite database (`data/sentri.db`)
- Config template (`config/sentri.yaml`)
- Verified Oracle syntax docs (`docs/oracle/`)

You only run this once. Running it again is safe — it won't overwrite existing files.

---

## 3. Configure Your Databases

Edit `~/.sentri/config/sentri.yaml`:

```yaml
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
```

**Key fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique identifier for this database |
| `connection_string` | Yes | Oracle connection URL |
| `environment` | Yes | `DEV`, `UAT`, or `PROD` — controls autonomy level |
| `username` | No | Overrides the user in connection_string |
| `aliases` | No | Alternate names that appear in alert emails |
| `autonomy_level` | No | Override the default for this environment |
| `oracle_version` | No | Helps Sentri use version-specific syntax |
| `architecture` | No | `STANDALONE`, `CDB`, or `RAC` |

The `environment` field determines how much autonomy Sentri has:

| Environment | Behavior |
|-------------|----------|
| **DEV** | Auto-execute all fixes, notify after |
| **UAT** | Auto-execute low-risk, require approval for high-risk |
| **PROD** | Always require human approval before execution |

---

## 4. Configure Email

Sentri monitors an IMAP mailbox for alert emails and sends approval requests via SMTP.

```yaml
email:
  # Inbound (reading alerts)
  imap_server: imap.gmail.com
  imap_port: 993
  username: dba-alerts@company.com
  use_ssl: true
  folder: INBOX            # Or a folder like "DBA-Alerts"
  poll_interval: 60        # Check every 60 seconds
  mark_as_read: true

  # Outbound (approval emails)
  smtp_server: smtp.gmail.com
  smtp_port: 587
  use_tls: true
```

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833) — not your regular password.

---

## 5. Set Credentials

Passwords are **never** stored in config files. Set them via environment variables:

```bash
# Email password
export SENTRI_EMAIL_PASSWORD="your-imap-password"

# Database passwords — pattern: SENTRI_DB_<NAME>_PASSWORD
# Uppercase the name, replace hyphens with underscores
export SENTRI_DB_PROD_DB_07_PASSWORD="prod-password"
export SENTRI_DB_DEV_DB_01_PASSWORD="dev-password"

# Optional: LLM provider (pick one or none)
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GOOGLE_API_KEY="AI..."
```

**Naming convention**: `PROD-DB-07` becomes `SENTRI_DB_PROD_DB_07_PASSWORD`.

---

## 6. Test Connectivity

Before starting, verify your database connections work:

```bash
# List configured databases
sentri db list

# Test connectivity to all databases
sentri db test

# Test a specific database
sentri db test PROD-DB-07
```

If a connection fails, check:
- Network access to the Oracle host and port
- The `SENTRI_DB_<NAME>_PASSWORD` environment variable is set
- The Oracle user has SELECT privileges on `V$` and `DBA_` views

---

## 7. Start Monitoring

```bash
sentri start
```

Sentri runs in the foreground. It will:

1. **Profile** each configured database (16 discovery queries)
2. **Start Scout** — polls your IMAP inbox for alert emails
3. **Start Proactive Agent** — runs scheduled health checks
4. **Process alerts** through the specialist agent pipeline

---

## 8. Verify It's Working

In another terminal:

```bash
# Check workflow statistics
sentri stats

# View recent workflows
sentri list

# Filter by status
sentri list --status AWAITING_APPROVAL

# View full details of a workflow
sentri show <workflow_id>

# Check audit trail
sentri audit
```

---

## What Happens When an Alert Arrives

1. Scout detects a new alert email matching a pattern in `alerts/`
2. Supervisor routes it to the right specialist agent (Storage, SQL Tuning, or RCA)
3. The specialist verifies the problem is real by querying the target database
4. It investigates using DBA tools and generates fix candidates
5. Safety Mesh enforces 5 structural checks (policy, conflicts, blast radius, circuit breaker, rollback)
6. Based on environment and confidence:
   - **DEV**: Auto-executes the fix
   - **UAT**: Auto-executes low-risk, sends approval email for high-risk
   - **PROD**: Always sends an approval email — DBA replies APPROVED or DENIED
7. After execution, Sentri validates the fix worked and auto-rollbacks on failure

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

Run `sentri start` or use [NSSM](https://nssm.cc/) for background service management.

---

## Next Steps

- [Configuration Reference](configuration.md) — full reference for every `sentri.yaml` field
- [Adding Alert Types](adding-alerts.md) — add support for new alert types by dropping a `.md` file
- [Architecture Overview](architecture.md) — how the agents, routing, and safety work
- [Safety Model](safety-model.md) — how Sentri keeps your databases safe
- [FAQ](faq.md) — common questions
