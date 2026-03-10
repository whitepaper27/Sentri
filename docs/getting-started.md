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


## File-Driven Behavior (What Happens If You Edit `.md` Files?)

Sentri is intentionally file-driven. As a DBA, you can control behavior without changing Python code.

| If you... | File Location | What happens at runtime |
|-----------|---------------|-------------------------|
| Edit an existing alert | `~/.sentri/alerts/*.md` | Scout uses the new regex/extraction on the next poll cycle; verification/fix/rollback/validation logic for that alert type also changes for newly detected workflows |
| Add a new alert file | `~/.sentri/alerts/*.md` | A new alert type becomes detectable automatically (no restart required) |
| Delete an alert file | `~/.sentri/alerts/*.md` | New emails for that pattern are no longer detected; existing workflows remain in DB/audit history |
| Edit/add a proactive check | `~/.sentri/checks/*.md` | Proactive Agent loads new check definitions on schedule and creates findings using the updated SQL/thresholds |
| Edit routing/policy brain docs | `~/.sentri/brain/*.md` | Routing and policy decisions follow the updated rules on subsequent workflow evaluations |
| Edit Oracle ground-truth docs | `~/.sentri/docs/oracle/**/*.md` | Researcher prompt context + SQL validation rule matching changes for future candidate generation/validation |

### Important Scope Notes

- Changes apply to **new detections and future decision points**; they do not rewrite already-completed audit history.
- For in-flight workflows, behavior depends on when each stage reads policy content (detection vs routing vs execution).
- Deleting a file disables that behavior for future runs; it does not delete historical records from SQLite.

### Safe DBA Change Workflow

1. Clone/backup the `.md` file before editing.
2. Make one change at a time (regex, SQL, or threshold).
3. Run a test email or proactive cycle in DEV.
4. Review `sentri show <workflow_id>` and `sentri audit`.
5. Promote the same `.md` change to UAT/PROD repo/config.

### Manager-Friendly Change Impact Examples (L3 DBA View)

Use this as a quick "what happens if we change this file" checklist during CAB/review calls.

| Change by DBA | File action | Immediate runtime effect | Approval behavior | Recommended L3 action |
|---|---|---|---|---|
| Add `rac_vip_down.md` alert | New file in `~/.sentri/alerts/` | Sentri starts detecting RAC VIP-down emails on next poll; workflows route to RCA/infra path you define in the file | In PROD, fix execution still requires approval before any SQL/action runs | Start in DEV RAC first, simulate one alert, verify extracted `database_id`/node/VIP, then promote |
| Add `exadata_cell_down.md` alert | New file in `~/.sentri/alerts/` | Exadata cell-down incidents become first-class workflows with your verify/escalate/runbook steps | Keep `Risk Level` HIGH so PROD always needs approval/human gate | Prefer escalation/runbook actions over autonomous SQL for storage-cell outages |
| Remove `high_cell_iops.md` | Delete file from `~/.sentri/alerts/` | New emails for high cell IOPS are no longer matched by Sentri | No approval generated because no workflow is created for that pattern | Remove only after manager sign-off; keep a replacement check/alert to avoid blind spots |
| Tighten approval on existing alert | Edit `Risk Level` and/or environment autonomy in config | Same alert still detected, but execution path becomes more restrictive | HIGH risk and PROD always require approval; UAT behavior depends on risk/autonomy settings | Use for change-freeze windows and quarter-close periods |

### Transparent Rules for Approvals

For managers, the approval model is deterministic and auditable:

- **Alert `.md` defines technical risk** via `Risk Level` and rollback/validation sections.
- **`sentri.yaml` defines environment autonomy** (`DEV`/`UAT`/`PROD`, optional `autonomy_level`).
- **Safety Mesh is final gate** and can still block unsafe SQL even if an alert file is permissive.
- **Every decision is logged** in `sentri audit`, including approval requests, approvals/denials, and final outcome.

If you need a strict operating mode for PROD, set policy to require approval for all actions and use alert `.md` files mainly for detection + evidence gathering.

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
- [L3 DBA Alert Control Playbook](l3-dba-alert-control-playbook.md) — manager/operator guide for add/edit/delete alert behavior
- [L2/L3 DBA Adoption Guide](l2-l3-dba-adoption-guide.md) — no-AI-background guide to create and operate alert `.md` files
