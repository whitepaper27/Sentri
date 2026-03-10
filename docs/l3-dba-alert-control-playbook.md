# L3 DBA Alert Control Playbook (Manager + Operator View)

This playbook shows exactly what happens when DBAs change file-driven configuration in Sentri.

It is designed for CAB reviews, shift handovers, and management reporting.

If your team is new to AI terms, start with the [L2/L3 DBA Adoption Guide](l2-l3-dba-adoption-guide.md) and then return here for manager governance.

---

## 1) What You Control with `.md` Files

- `~/.sentri/alerts/*.md` = alert detection + extraction + verify + action + rollback + validation
- `~/.sentri/checks/*.md` = proactive checks (scheduled SQL, thresholds, recommendations)
- `~/.sentri/brain/*.md` = routing and policy behavior
- `~/.sentri/docs/oracle/**/*.md` = Oracle ground-truth docs used by RAG context and SQL rule validation

---

## 2) Immediate Runtime Impact Matrix

| DBA change | What changes at runtime | What does **not** change | Manager control point |
|---|---|---|---|
| Edit existing alert file | Next detected workflows use updated regex/SQL/actions | Historical workflows/audit records remain unchanged | Require DEV validation evidence before PROD promotion |
| Add new alert file | New alert type starts matching on next poll cycle | Existing alert types unchanged | Approve new alert category ownership + escalation path |
| Delete alert file | Future emails for that pattern stop creating workflows | Existing workflow history is preserved | CAB approval required + replacement signal check |
| Edit proactive check file | Next schedule run uses new query/threshold/recommendation | Historical findings stay intact | Confirm false-positive/false-negative impact |
| Edit Oracle docs/rules | Future generation/validation context changes | Past executed workflows unchanged | Require peer review for syntax/rule changes |

---

## 3) Requested Concrete Examples

### A) Add `rac_vip_down.md`

**Expected behavior**
- Sentri starts detecting RAC VIP-down emails on next poll.
- Workflow is created and routed per your alert definition/rules.
- In PROD, approval is still required before execution.

**Recommended L3 action**
1. Deploy in DEV/UAT RAC first.
2. Trigger one test alert email.
3. Confirm extracted fields (`database_id`, node, VIP) in workflow details.
4. Promote same file to PROD after evidence review.

---

### B) Add `exadata_cell_down.md`

**Expected behavior**
- Exadata cell-down alerts become structured workflows with verification and response steps.
- Approval behavior remains policy-driven; set HIGH risk for strict PROD gating.

**Recommended L3 action**
- Prefer runbook/escalation actions over autonomous SQL for infra/storage-cell failures.
- Keep explicit on-call owner and escalation targets in the alert runbook.

---

### C) Remove `high_cell_iops.md`

**Expected behavior**
- New “high cell IOPS” emails stop matching.
- No workflow means no approval request for that pattern.

**Recommended L3 action**
- Remove only with manager/CAB sign-off.
- Ensure replacement monitoring exists (check, dashboard, or another alert pattern).

---

## 4) Approval Logic (Deterministic and Auditable)

Final action path is the combination of four layers:

1. **Alert file risk** (`Risk Level` in alert `.md`)
2. **Environment autonomy policy** (`sentri.yaml` for DEV/UAT/PROD)
3. **Safety Mesh gate** (can block unsafe SQL regardless of risk setting)
4. **Audit trail** (`sentri audit`, `sentri show`) for every request/decision/outcome

If you want maximum control in PROD, set policy so all actions require approval.

---

## 5) RAG + Oracle Docs: How Hallucinations Are Reduced

Sentri does not rely on free-form generation alone.

- Oracle docs under `~/.sentri/docs/oracle/` provide version-aware, grounded syntax context.
- Rules docs constrain unsafe/invalid patterns during SQL validation.
- Safety Mesh still performs structural checks before any execution.

Operationally: updating Oracle docs/rules affects **future** candidate generation and validation only.

---

## 6) Standard Change Procedure (L3 DBA)

1. Create/change one `.md` file only.
2. Validate in DEV with a test alert/proactive run.
3. Review `sentri show <workflow_id>` for extraction, routing, verify, and action plan.
4. Review `sentri audit` for approval and policy decisions.
5. Promote unchanged file to UAT then PROD.
6. Record CAB ticket + rollback plan for deletions/high-impact alerts.

---

## 7) Manager Checklist (30-second review)

- Is this add/edit/delete request tied to a ticket?
- Is risk level appropriate for environment criticality?
- Is PROD approval policy unchanged/explicit?
- Is there a replacement signal before deleting any alert?
- Is DEV evidence attached (`sentri show` + `sentri audit`)?

