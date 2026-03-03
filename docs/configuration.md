# Configuration Reference

Sentri is configured via `~/.sentri/config/sentri.yaml`. This page documents every field.

---

## Email

Controls how Sentri reads alert emails (IMAP) and sends approval notifications (SMTP).

```yaml
email:
  # Inbound — reading alert emails
  imap_server: imap.gmail.com
  imap_port: 993
  username: dba-alerts@company.com
  use_ssl: true
  folder: INBOX
  poll_interval: 60
  mark_as_read: true

  # Outbound — sending approval/notification emails
  smtp_server: smtp.gmail.com
  smtp_port: 587
  use_tls: true
```

| Field | Default | Description |
|-------|---------|-------------|
| `imap_server` | — | IMAP server hostname |
| `imap_port` | `993` | IMAP port (993 for SSL, 143 for plain) |
| `username` | — | Email address to monitor |
| `use_ssl` | `true` | Use SSL for IMAP connection |
| `folder` | `INBOX` | IMAP folder to watch. Create a folder like `DBA-Alerts` and filter your OEM emails there |
| `poll_interval` | `60` | Seconds between inbox checks |
| `mark_as_read` | `true` | Mark processed emails as read |
| `smtp_server` | — | SMTP server for outbound email (approval requests, notifications) |
| `smtp_port` | `587` | SMTP port (587 for TLS, 465 for SSL) |
| `use_tls` | `true` | Use TLS for SMTP connection |

**Password**: Set via the `SENTRI_EMAIL_PASSWORD` environment variable. Never put it in the YAML file.

---

## Databases

Each entry defines an Oracle database that Sentri monitors and can act on.

```yaml
databases:
  - name: PROD-DB-07
    connection_string: oracle://sentri_agent@prod-scan:1521/PRODDB
    environment: PROD
    username: sentri_ro
    aliases: [PRODDB, prod-db-07, PROD07]
    autonomy_level: ADVISORY
    oracle_version: "19c"
    architecture: RAC
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `name` | Yes | — | Unique identifier. Used in env var names and CLI commands |
| `connection_string` | Yes | — | Oracle connection URL: `oracle://user@host:port/service` |
| `environment` | Yes | — | `DEV`, `UAT`, or `PROD`. Controls default autonomy level |
| `username` | No | From URL | Override the username in the connection string |
| `password` | No | — | **Not recommended.** Use `SENTRI_DB_<NAME>_PASSWORD` env var instead |
| `aliases` | No | `[]` | Alternate names this database appears as in alert emails |
| `autonomy_level` | No | By env | Per-database override: `AUTONOMOUS`, `SUPERVISED`, or `ADVISORY` |
| `oracle_version` | No | Auto-detect | Helps Sentri select version-specific syntax (e.g., `19c`, `12c`) |
| `architecture` | No | Auto-detect | `STANDALONE`, `CDB`, or `RAC` |
| `critical_schemas` | No | — | Comma-separated schemas that get extra caution |
| `business_owner` | No | — | Contact info for escalation |
| `dba_owner` | No | — | Primary DBA contact |

### Environment Defaults

| Environment | Default Autonomy | Behavior |
|-------------|-----------------|----------|
| `DEV` | `AUTONOMOUS` | Auto-execute all fixes, notify after |
| `UAT` | `SUPERVISED` | Auto-execute LOW risk, require approval for HIGH+ |
| `PROD` | `ADVISORY` | Always require human approval |

### Database Passwords

Set via environment variables using this naming convention:

1. Take the database `name` field
2. Uppercase it
3. Replace hyphens with underscores
4. Prefix with `SENTRI_DB_` and suffix with `_PASSWORD`

| Database Name | Environment Variable |
|---------------|---------------------|
| `PROD-DB-07` | `SENTRI_DB_PROD_DB_07_PASSWORD` |
| `DEV-DB-01` | `SENTRI_DB_DEV_DB_01_PASSWORD` |
| `my-oracle-dev` | `SENTRI_DB_MY_ORACLE_DEV_PASSWORD` |

You can also override the username per-database:

| Database Name | Username Variable |
|---------------|------------------|
| `PROD-DB-07` | `SENTRI_DB_PROD_DB_07_USERNAME` |

---

## Approvals

Controls how Sentri requests human approval for fixes.

```yaml
approvals:
  email_enabled: true
  approval_recipients: "dba-team@company.com"
  approval_timeout: 3600
  slack_webhook_url: ""
```

| Field | Default | Description |
|-------|---------|-------------|
| `email_enabled` | `true` | Send approval requests via email |
| `approval_recipients` | Email username | Comma-separated email addresses for approval requests |
| `approval_timeout` | `3600` | Seconds to wait for approval before timing out (1 hour) |
| `slack_webhook_url` | — | Slack webhook URL for notifications. Set via `SENTRI_SLACK_WEBHOOK_URL` env var |

When a fix requires approval, Sentri sends an email with `[WF:xxxxxxxx]` in the subject line. The DBA replies with `APPROVED` or `DENIED` (with optional reason). Sentri's Scout agent detects the reply and acts accordingly.

You can also approve or deny via CLI:

```bash
sentri approve <workflow_id>
sentri approve <workflow_id> --deny --reason "Not safe during batch window"
sentri resolve <workflow_id> --reason "Fixed manually"
```

---

## Monitoring

Controls polling intervals and logging.

```yaml
monitoring:
  log_level: INFO
  scout_poll_interval: 60
  orchestrator_poll_interval: 10
  profile_refresh_hours: 24
```

| Field | Default | Description |
|-------|---------|-------------|
| `log_level` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `scout_poll_interval` | `60` | Seconds between IMAP inbox checks |
| `orchestrator_poll_interval` | `10` | Seconds between workflow processing cycles |
| `profile_refresh_hours` | `24` | Hours between database re-profiling (0 = startup only) |

Logs are written to `~/.sentri/logs/sentri.log` (rotating, 10 MB x 5 files).

---

## Learning Engine

Controls the self-improvement pipeline that observes outcomes and proposes policy improvements.

```yaml
learning:
  enabled: true
  min_observations: 5
  judge_count: 3
  judge_agreement: 2
  monitoring_days: 30

  llm_provider: "gemini"
  llm_model: ""

  researcher_provider: ""
  judge_provider: "diverse"

  daily_cost_limit: 5.0
```

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `true` | Enable the observe-propose-judge-apply pipeline |
| `min_observations` | `5` | Minimum observations before proposing improvements |
| `judge_count` | `3` | Number of LLM judges for consensus |
| `judge_agreement` | `2` | Minimum judges that must agree to approve a change |
| `monitoring_days` | `30` | Days to monitor after applying an improvement |
| `llm_provider` | `"gemini"` | Default LLM: `claude`, `openai`, or `gemini` |
| `llm_model` | `""` | Specific model name (blank = provider default) |
| `researcher_provider` | `""` | Override LLM for research tasks (falls back to `llm_provider`) |
| `judge_provider` | `"diverse"` | `diverse` uses all available providers for judge consensus |
| `daily_cost_limit` | `5.0` | Maximum USD per day across all LLM providers |

### LLM API Keys

Set via environment variables:

| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` | Claude (Anthropic) |
| `OPENAI_API_KEY` | OpenAI (GPT) |
| `GOOGLE_API_KEY` | Gemini (Google) |

Sentri works without any LLM key — it falls back to template-based fixes defined in the alert `.md` files.

---

## Notifications (Advanced)

Optional webhook adapters for additional notification channels.

```yaml
notifications:
  adapters:
    - type: webhook
      enabled: true
      url: https://hooks.slack.com/services/YOUR/WEBHOOK/URL
      headers:
        Content-Type: application/json

    - type: pagerduty
      enabled: false
      routing_key: "your-pagerduty-integration-key"
```

---

## Environment Variables Summary

| Variable | Purpose |
|----------|---------|
| `SENTRI_EMAIL_PASSWORD` | IMAP/SMTP email password |
| `SENTRI_DB_<NAME>_PASSWORD` | Per-database Oracle password |
| `SENTRI_DB_<NAME>_USERNAME` | Per-database Oracle username override |
| `SENTRI_SLACK_WEBHOOK_URL` | Slack notification webhook |
| `ANTHROPIC_API_KEY` | Claude API key (optional) |
| `OPENAI_API_KEY` | OpenAI API key (optional) |
| `GOOGLE_API_KEY` | Gemini API key (optional) |

---

## Full Example

```yaml
email:
  imap_server: imap.gmail.com
  imap_port: 993
  username: dba-alerts@company.com
  use_ssl: true
  folder: DBA-Alerts
  poll_interval: 60
  mark_as_read: true
  smtp_server: smtp.gmail.com
  smtp_port: 587
  use_tls: true

databases:
  - name: PROD-DB-07
    connection_string: oracle://sentri_agent@prod-scan:1521/PRODDB
    environment: PROD
    username: sentri_ro
    aliases: [PRODDB, prod-db-07]

  - name: DEV-DB-01
    connection_string: oracle://sentri_agent@dev-db-01:1521/DEVDB
    environment: DEV

approvals:
  email_enabled: true
  approval_recipients: "dba-team@company.com"
  approval_timeout: 3600

monitoring:
  log_level: INFO
  scout_poll_interval: 60
  profile_refresh_hours: 24

learning:
  enabled: true
  llm_provider: "gemini"
  daily_cost_limit: 5.0
```
