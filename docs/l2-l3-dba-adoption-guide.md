# L2/L3 DBA Adoption Guide (No AI Background Needed)

This guide is for DBAs who want to operate Sentri safely without learning AI internals.

If you know alerts, SQL, and change control, you already have enough to use Sentri.

---

## 1) Mental Model in 60 Seconds

Think of Sentri like this:

- **Alert `.md` file = runbook card** for one alert type.
- Sentri reads that card and follows it:
  1. detect alert email,
  2. verify with SQL,
  3. propose action,
  4. require approval if policy says so,
  5. validate result,
  6. rollback if needed.

You are not writing AI prompts. You are writing structured runbook content.

---

## 2) What L2/L3 Should Edit vs Avoid

### Safe to edit regularly

- `~/.sentri/alerts/*.md` (new/updated alert patterns)
- `~/.sentri/checks/*.md` (proactive checks + thresholds)

### Edit with peer review

- `~/.sentri/brain/*.md` (routing/policy rules)
- `~/.sentri/docs/oracle/**/*.md` (RAG ground-truth + SQL validation rules)

Rule of thumb: if a change can affect many alert types, require peer review.

---

## 3) Plain-Language RAG Explanation

RAG here means: Sentri reads trusted Oracle docs before generating SQL.

For DBAs:
- It reduces bad SQL suggestions.
- It does **not** auto-bypass approvals.
- Safety Mesh still blocks unsafe statements.

So you can treat RAG docs as an extra safety reference, not magic AI behavior.

---

## 4) Fast Adoption Path (First 7 Days)

1. Pick top 3 recurring alerts in your shop.
2. Add/adjust one alert `.md` per day.
3. Test each in DEV with one sample alert email.
4. Review workflow output using:
   - `sentri show <workflow_id>`
   - `sentri audit`
5. Promote unchanged file to UAT, then PROD.

This approach keeps risk low and builds team confidence quickly.

---

## 5) Copy-Paste Template for New Alert (L2/L3 Friendly)

Create `~/.sentri/alerts/<alert_name>.md`:

```markdown
---
type: alert_pattern
name: <alert_name>
severity: MEDIUM
action_type: <ACTION_CODE>
version: "1.0"
---

# <Alert Title>

## Email Pattern
```regex
(?i)<regex that matches subject/body and captures database_id>
```

## Extracted Fields
- `database_id` = group(1)

## Verification Query
```sql
SELECT ... FROM ... WHERE ...;
```

## Tolerance
- <metric>: <allowed deviation>

## Forward Action
```sql
ALTER ...;
```

## Rollback Action
```sql
ALTER ...;
```

## Validation Query
```sql
SELECT ... FROM ... WHERE ...;
```

## Risk Level
LOW | MEDIUM | HIGH
```

Use HIGH for RAC/Exadata infra-impact actions unless your governance allows otherwise.

---

## 6) RAC/Exadata Operating Defaults (Recommended)

- **RAC VIP down**: detect + verify + escalate, keep HIGH risk in PROD.
- **Exadata cell down**: treat as infra incident; prefer escalation/runbook steps over autonomous SQL.
- **High cell IOPS removal**: only remove alert with CAB sign-off and replacement signal.

---

## 7) L2/L3 Daily Checklist

- Check approval queue: `sentri list --status AWAITING_APPROVAL`
- Review recent actions: `sentri audit --last 50`
- Sample one completed workflow for quality: `sentri show <workflow_id>`
- Confirm no accidental alert coverage gaps after file changes

---

## 8) Common Mistakes to Avoid

- Editing many `.md` files in one change (hard to debug)
- Deleting alerts without replacement monitoring
- Promoting to PROD before DEV evidence is attached
- Treating RAG docs as optional when Oracle syntax rules changed

---

## 9) Manager Sign-off Checklist

- Ticket/CAB reference present
- Scope limited to specific files
- DEV evidence attached (`sentri show` + `sentri audit`)
- Approval policy validated for PROD
- Rollback plan documented
