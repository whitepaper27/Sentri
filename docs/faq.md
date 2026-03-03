# Frequently Asked Questions

---

### Does Sentri need an LLM API key?

No. Sentri works without any LLM API key using template-based fixes defined in the alert `.md` files. These templates cover the common fix for each alert type (e.g., add a datafile for tablespace full).

Adding an API key (Claude, OpenAI, or Gemini) enables intelligent investigation — the LLM uses 12 DBA tools to investigate before prescribing a fix, generates multiple candidates, and a judge selects the best option. But for stable databases where the same alerts recur, template-based fixes work well at zero cost.

---

### Will Sentri drop my production tables?

No. Multiple layers prevent this:

1. **Blast radius check** — `DROP` and `TRUNCATE` statements are classified as CRITICAL and blocked before they reach execution
2. **Production requires approval** — Every fix on a PROD database requires a human DBA to reply APPROVED to an email (or approve via CLI)
3. **Read-only investigation** — All 12 DBA tools are SELECT-only with a regex guard that blocks DML/DDL
4. **Ground truth validation** — SQL is checked against verified Oracle syntax rules before execution

The Safety Mesh enforces this structurally in code. It cannot be bypassed by LLM prompt injection.

---

### How much does the LLM cost?

Most alerts on stable databases cost $0. Here's why:

The cost gate checks historical success rate before deciding whether to use the LLM:
- **95%+ success rate** for this alert type on this database → template fix (0 LLM calls)
- **80–95%** → one-shot LLM call (~$0.01 with Claude Haiku or Gemini Flash)
- **Below 80% or novel** → full argue/select with investigation (~$0.05–0.10 with Claude Sonnet)

In practice, once Sentri has successfully handled an alert type on a database a few times, future occurrences hit the template path. The LLM is reserved for novel or ambiguous situations.

You can set a daily cost limit in `sentri.yaml`:
```yaml
learning:
  daily_cost_limit: 5.0  # USD per day
```

---

### Can I use Sentri with PostgreSQL or SQL Server?

Not yet. The architecture is database-agnostic (the agent logic, state machine, and safety mesh work with any database), but the implementation currently only includes:
- Oracle connection pool and query runner
- Oracle-specific DBA investigation tools
- Oracle syntax ground truth docs

PostgreSQL and SQL Server support are on the roadmap. The work is primarily adding database-specific tools and verified syntax docs — the core agent framework stays the same.

---

### How is this different from Oracle Autonomous Database?

Oracle Autonomous Database is a managed cloud service — you pay Oracle to run and manage the database for you.

Sentri is an open-source agent that works with **any** Oracle database — on-premises, cloud-hosted (OCI, AWS RDS, Azure), any version from 12c to 23ai. It also:

- Learns from your specific environment's patterns
- Lets you customize every behavior via `.md` files
- Works with your existing monitoring tools (anything that sends email)
- Gives you full visibility into every decision (audit trail)
- Doesn't require changing your database platform

---

### What if Sentri makes a mistake?

Every fix has pre-captured rollback SQL. If post-execution validation fails, Sentri:

1. Automatically executes the rollback SQL
2. Marks the workflow as `ROLLED_BACK`
3. Notifies the DBA team
4. Records everything in the audit trail

The circuit breaker adds another layer: if 3 fixes fail on the same database in 24 hours, Sentri stops attempting fixes on that database and escalates to a human DBA.

For PROD databases, Sentri always requires human approval before executing — so the DBA reviews the proposed SQL before it runs.

---

### What monitoring systems work with Sentri?

Any monitoring system that sends alert emails. Sentri matches alert emails using configurable regex patterns, so it works with:

- Oracle Enterprise Manager (OEM)
- Zabbix
- Nagios / Icinga
- Datadog
- PRTG
- Custom scripts that send email

You configure the regex pattern in the alert `.md` file to match your monitoring system's email format.

---

### Can I run Sentri on Windows?

Yes. Sentri runs on Windows, Linux, and macOS. It uses `python-oracledb` in thin mode, which doesn't require an Oracle Client installation.

On Linux, you can run it as a systemd service:
```bash
sentri install-service
```

On Windows, run `sentri start` or use [NSSM](https://nssm.cc/) for background service management.

---

### How do I add support for a new alert type?

Drop a `.md` file in `~/.sentri/alerts/`. No code changes, no restart needed.

The file defines: a regex to match the alert email, a SQL query to verify the problem, a SQL fix, a rollback SQL, and a validation query. Sentri picks it up on the next poll cycle.

See [Adding Alert Types](adding-alerts.md) for a step-by-step guide with a worked example.

---

### How do I see what Sentri has done?

```bash
# Workflow statistics and success rate
sentri stats

# Recent workflows
sentri list
sentri list --last 20
sentri list --status COMPLETED

# Full details of a specific workflow (including SQL)
sentri show <workflow_id>

# Audit trail
sentri audit
sentri audit --db PROD-DB-07
```

---

### Can multiple DBAs approve fixes?

Approval emails are sent to all configured recipients. The first response (APPROVED or DENIED) wins. All subsequent replies are ignored for that workflow.

```yaml
approvals:
  approval_recipients: "dba1@company.com, dba2@company.com, dba-lead@company.com"
```

---

### Does Sentri store passwords?

No. All passwords are read from environment variables at runtime:

- `SENTRI_EMAIL_PASSWORD` — email account
- `SENTRI_DB_<NAME>_PASSWORD` — per-database Oracle password
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` — LLM providers

The `sentri.yaml` config file should never contain passwords. If you see a `password` field in the config template, it's a placeholder — use the environment variable instead.

---

### What Oracle privileges does Sentri need?

Sentri needs two levels of access:

**For investigation (read-only)**:
- `SELECT` on `V$` views (`V$SESSION`, `V$SQL`, `V$PARAMETER`, etc.)
- `SELECT` on `DBA_` views (`DBA_TABLESPACES`, `DBA_DATA_FILES`, `DBA_TABLES`, etc.)

**For execution (when fixing problems)**:
- `ALTER TABLESPACE` — for storage fixes
- `ALTER SYSTEM` — for parameter changes
- `EXECUTE` on `DBMS_STATS` — for statistics gathering
- `ALTER SESSION` — for session management

We recommend creating a dedicated `sentri_agent` Oracle user with only the privileges needed for the alert types you've enabled.
