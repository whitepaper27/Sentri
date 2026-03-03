# Adding Alert Types and Health Checks

Sentri's alert types and health checks are defined in `.md` files. Add a new one by dropping a file in the right directory — no code changes, no restart.

---

## Adding an Alert Type

Alert patterns live in `~/.sentri/alerts/`. Each `.md` file teaches Sentri how to detect, verify, fix, and rollback a specific type of database alert.

### File Structure

Every alert `.md` file has two parts:

1. **YAML frontmatter** — metadata (between `---` markers)
2. **Markdown sections** — the detection and remediation logic

### Anatomy of an Alert File

```markdown
---
type: alert_pattern
name: my_alert_name
severity: HIGH
action_type: MY_ACTION
version: "1.0"
---

# My Alert Name

Brief description of what this alert detects.

## Email Pattern

​```regex
(?i)your regex pattern here to match alert emails
​```

## Extracted Fields

- `field_name` = group(1) -- Description
- `database_id` = group(3) -- Target database

## Verification Query

​```sql
SELECT columns FROM view WHERE condition = :field_name;
​```

## Tolerance

- `metric_name`: +/- 5% of the reported value

## Pre-Flight Checks

- Description of check -- expected value

​```sql
SELECT check_query;
​```

## Forward Action

​```sql
ALTER ... FIX THE ISSUE;
​```

## Rollback Action

​```sql
ALTER ... UNDO THE FIX;
​```

## Validation Query

​```sql
SELECT columns to verify the fix worked;
​```

## Risk Level

LOW | MEDIUM | HIGH

## Expected Downtime

NONE | BRIEF | estimated duration

## Estimated Duration

~15 seconds
```

### Required Sections

| Section | Purpose |
|---------|---------|
| **Email Pattern** | Regex to match incoming alert email subjects/bodies |
| **Extracted Fields** | Maps regex capture groups to named variables |
| **Verification Query** | Read-only SQL to confirm the alert is real |
| **Tolerance** | How much deviation is acceptable between reported and actual |
| **Forward Action** | SQL to fix the issue |
| **Rollback Action** | SQL to undo the fix (required for risk > LOW) |
| **Validation Query** | SQL to confirm the fix worked |
| **Risk Level** | LOW, MEDIUM, or HIGH — affects approval routing |

### Optional Sections

| Section | Purpose |
|---------|---------|
| **Pre-Flight Checks** | Extra SQL checks to run before executing the fix |
| **Expected Downtime** | NONE, BRIEF, or time estimate |
| **Estimated Duration** | Approximate wall-clock time |

---

## Worked Example: ORA-01555 Snapshot Too Old

Let's walk through adding a new alert type from scratch.

**Scenario**: Your monitoring sends emails like:
> "ORA-01555: snapshot too old on database PROD-DB-07, rollback segment _SYSSMU7_1234$ too small"

### Step 1: Create the file

Create `~/.sentri/alerts/snapshot_too_old.md`:

```markdown
---
type: alert_pattern
name: snapshot_too_old
severity: MEDIUM
action_type: RESIZE_UNDO
version: "1.0"
---

# Snapshot Too Old (ORA-01555)

Detects ORA-01555 errors caused by insufficient undo retention. Increases
undo_retention to prevent long-running queries from failing.

## Email Pattern

​```regex
(?i)ORA-01555.*?snapshot\s+too\s+old.*?(?:on|database)\s+(\S+)
​```

## Extracted Fields

- `database_id` = group(1) -- Target database identifier

## Verification Query

​```sql
SELECT name, value
  FROM v$parameter
 WHERE name = 'undo_retention';
​```

Confirms the current undo_retention setting. A value under 900 (15 minutes)
is likely too low for environments with long-running queries.

## Tolerance

- `value`: No tolerance — any ORA-01555 is actionable

## Forward Action

​```sql
ALTER SYSTEM SET undo_retention = 1800 SCOPE=BOTH;
​```

Doubles the default undo retention to 30 minutes. This is an online
operation with no downtime.

## Rollback Action

​```sql
ALTER SYSTEM SET undo_retention = :original_value SCOPE=BOTH;
​```

Restores the previous undo_retention value.

## Validation Query

​```sql
SELECT name, value
  FROM v$parameter
 WHERE name = 'undo_retention';
​```

**Success criteria**: `value` should be 1800 or higher.

## Risk Level

LOW -- Changing undo_retention is an online, non-disruptive operation.

## Expected Downtime

NONE

## Estimated Duration

~2 seconds
```

### Step 2: Test it

Send a test email to your monitored inbox with a subject like:

> ORA-01555: snapshot too old on database DEV-DB-01

Then check:

```bash
# Wait for Scout to pick it up (default: 60 seconds)
sentri list --last 5
```

You should see a new workflow in `DETECTED` status with alert type `snapshot_too_old`.

### Step 3: Watch it work

```bash
# View workflow details
sentri show <workflow_id>
```

Sentri will verify the alert, investigate, generate a fix, run it through Safety Mesh, and either auto-execute (DEV) or request approval (PROD).

---

## Tips for Writing Alert Patterns

### Email Pattern (Regex)

- Use `(?i)` for case-insensitive matching
- The `database_id` capture group is critical — Sentri uses it to route to the correct database
- Test your regex against real alert emails before deploying
- Keep patterns specific enough to avoid false matches across alert types

### Verification Query

- Must be **read-only** (SELECT only)
- Uses bind variables matching the extracted fields (`:tablespace_name`, `:database_id`, etc.)
- Should confirm the problem still exists — alerts might self-resolve

### Forward and Rollback Actions

- Forward action should be the minimal fix (don't over-engineer)
- Rollback action should completely undo the forward action
- Use bind variables for values captured during execution
- If a rollback isn't possible (e.g., gathering stats), note that in the rollback section

### Risk Level

The risk level affects approval routing:

| Risk | DEV | UAT | PROD |
|------|-----|-----|------|
| LOW | Auto-execute | Auto-execute | Require approval |
| MEDIUM | Auto-execute | Require approval | Require approval |
| HIGH | Require approval | Require approval | Require approval |

---

## Adding a Proactive Health Check

Health checks live in `~/.sentri/checks/`. They work the same way as alerts, but instead of matching emails, they run on a schedule and look for problems proactively.

### Health Check File Structure

```markdown
---
check_type: my_check_name
severity: MEDIUM
schedule: every_6_hours
routes_to: storage_agent
---

## Description

What this check detects and why it matters.

## Health Query

​```sql
SELECT columns FROM views WHERE bad_condition;
​```

## Threshold

- column_name: threshold_value

## Recommended Action

​```sql
SQL to fix the issue;
​```
```

### YAML Frontmatter

| Field | Values | Description |
|-------|--------|-------------|
| `check_type` | Unique name | Matches the filename (without `.md`) |
| `severity` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` | How urgent findings are |
| `schedule` | `every_6_hours`, `daily`, `weekly` | How often to run |
| `routes_to` | `storage_agent`, `sql_tuning_agent`, `rca_agent` | Which specialist handles findings |

### Example: High Clustering Factor Indexes

Create `~/.sentri/checks/high_clustering_factor.md`:

```markdown
---
check_type: high_clustering_factor
severity: LOW
schedule: weekly
routes_to: sql_tuning_agent
---

## Description

Detects indexes with a clustering factor close to the number of rows,
which makes the index inefficient for range scans. The optimizer may
choose full table scans instead.

## Health Query

​```sql
SELECT i.owner, i.index_name, i.table_name,
       i.clustering_factor, t.num_rows,
       ROUND(i.clustering_factor / NULLIF(t.num_rows, 0) * 100, 1) AS scatter_pct
  FROM dba_indexes i
  JOIN dba_tables t ON i.table_owner = t.owner AND i.table_name = t.table_name
 WHERE i.owner NOT IN ('SYS', 'SYSTEM')
   AND t.num_rows > 10000
   AND i.clustering_factor > t.num_rows * 0.9
 ORDER BY scatter_pct DESC
​```

## Threshold

- scatter_pct: 90

## Recommended Action

​```sql
-- Consider reorganizing the table to match the index order
ALTER TABLE {owner}.{table_name} MOVE;
ALTER INDEX {owner}.{index_name} REBUILD;
​```
```

### Included Health Checks

Sentri ships with 7 health checks:

| Check | Schedule | Specialist | What It Detects |
|-------|----------|------------|-----------------|
| Stale Statistics | Every 6 hours | SQL Tuning Agent | Tables not analyzed in 30+ days |
| Tablespace Trend | Every 6 hours | Storage Agent | Tablespaces above 85% usage |
| Index Usage | Daily | SQL Tuning Agent | Unused indexes wasting space |
| Redo Log Sizing | Every 6 hours | Storage Agent | Excessive log switches (>6/hour) |
| Temp Growth Trend | Every 6 hours | Storage Agent | Temp tablespace trending toward full |
| Password Expiry | Daily | Escalate | Database accounts expiring in 14 days |
| Backup Freshness | Daily | Escalate | No RMAN backup in 48+ hours |

---

## How Sentri Discovers New Files

Sentri scans the `alerts/` and `checks/` directories at startup and on each poll cycle. When you drop a new `.md` file:

1. **No restart needed** — Sentri picks up the new file on the next cycle
2. The YAML frontmatter is parsed for metadata
3. The markdown sections are parsed for queries and actions
4. The new pattern is added to the active set

If there's a syntax error in your `.md` file, Sentri logs a warning and skips that file — it doesn't break other alerts.
