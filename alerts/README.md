# Alert Pattern Definitions

This directory contains alert pattern policy files that Sentri uses to detect, verify, and remediate Oracle database issues from email alerts.

## File Format

Each `.md` file (except this README) defines a single alert type using **YAML frontmatter** for metadata and **structured Markdown sections** for the detection and remediation logic.

### YAML Frontmatter

```yaml
---
type: alert_pattern
name: human_readable_name
severity: LOW | MEDIUM | HIGH | CRITICAL
version: "1.0"
---
```

### Required Sections

| Section                | Purpose                                                        |
|------------------------|----------------------------------------------------------------|
| `## Email Pattern`     | Regex (in a `regex` code block) to match incoming alert emails |
| `## Extracted Fields`  | Bullet list mapping regex capture groups to named fields       |
| `## Verification Query`| SQL query to confirm the alert is genuine (read-only)          |
| `## Tolerance`         | Acceptable deviation between reported and actual values        |
| `## Forward Action`    | SQL or OS command to remediate the issue                       |
| `## Rollback Action`   | SQL or OS command to reverse the forward action                |
| `## Validation Query`  | SQL query to confirm the fix was successful                    |
| `## Risk Level`        | LOW, MEDIUM, or HIGH                                           |
| `## Expected Downtime` | NONE, BRIEF, or estimated duration                             |
| `## Estimated Duration`| Approximate wall-clock time for the forward action             |

## How to Add a New Alert Pattern

1. Create a new `.md` file in this directory (e.g., `my_new_alert.md`).
2. Add YAML frontmatter with `type: alert_pattern`, a unique `name`, `severity`, and `version`.
3. Fill in every required section listed above.
4. Ensure the regex in `## Email Pattern` captures all fields listed in `## Extracted Fields`.
5. Test the regex against real or sample alert email subjects.
6. Ensure `## Verification Query` uses only read-only SQL (SELECT statements).
7. Ensure `## Forward Action` has a corresponding `## Rollback Action` wherever possible.
8. Restart Sentri or trigger a policy reload -- patterns are loaded at runtime from this directory.

## Naming Conventions

- File names use `snake_case` matching the alert `name` field.
- One alert type per file.
- Keep regex patterns case-insensitive where possible (use `(?i)` flag).

## Included Patterns (POC)

| File                    | Alert Type              | Severity |
|-------------------------|-------------------------|----------|
| `tablespace_full.md`   | Tablespace capacity     | HIGH     |
| `archive_dest_full.md` | Archive destination full| CRITICAL |
| `temp_full.md`         | Temp tablespace full    | HIGH     |
| `listener_down.md`     | TNS Listener down       | CRITICAL |
| `archive_gap.md`       | Archive log gap         | HIGH     |
