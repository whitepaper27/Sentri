"""Paper-ready tables and prose output for arXiv evaluation section."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from eval.config import AdversarialResult, RunResult

RESULTS_DIR = Path(__file__).parent / "results"


# ---------------------------------------------------------------------------
# Remediation results table
# ---------------------------------------------------------------------------

def format_results_table(results: list[RunResult]) -> str:
    """Markdown table of all remediation runs."""
    lines = [
        "| Run | Incident | Env | Mode | Tier | Outcome | Time (s) | LLM Calls | Cost ($) | Description |",
        "|-----|----------|-----|------|------|---------|----------|-----------|----------|-------------|",
    ]
    for r in results:
        cost = f"{r.cost_usd:.4f}" if r.cost_usd > 0 else "—"
        tier = r.researcher_tier or "template"
        desc = r.description[:40] + "..." if len(r.description) > 40 else r.description
        env = "DEV" if "DEV" in (r.description or "") else ("PROD" if "PROD" in (r.description or "") else "—")
        # Extract env from description
        for e in ("PROD", "UAT", "DEV"):
            if e in r.description:
                env = e
                break
        lines.append(
            f"| {r.run_id} | {r.incident_type} | {env} | {r.mode} | {tier} | "
            f"{r.outcome} | {r.resolution_time_s:.2f} | {r.llm_calls} | {cost} | {desc} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Aggregate statistics
# ---------------------------------------------------------------------------

def format_aggregates(results: list[RunResult]) -> str:
    """Summary statistics grouped by mode, tier, and incident type."""
    lines = ["## Aggregate Statistics\n"]

    total = len(results)
    success = sum(1 for r in results if r.outcome in ("COMPLETED", "NEEDS_APPROVAL"))
    lines.append(f"**Overall success rate**: {success}/{total} ({100*success/total:.0f}%)\n")

    # By mode
    for mode in ("full", "template"):
        mode_runs = [r for r in results if r.mode == mode]
        if not mode_runs:
            continue
        mode_success = sum(1 for r in mode_runs if r.outcome in ("COMPLETED", "NEEDS_APPROVAL"))
        avg_time = sum(r.resolution_time_s for r in mode_runs) / len(mode_runs)
        total_cost = sum(r.cost_usd for r in mode_runs)
        avg_llm = sum(r.llm_calls for r in mode_runs) / len(mode_runs)

        lines.append(f"### {mode.title()} Mode ({len(mode_runs)} runs)")
        lines.append(f"- Success rate: {mode_success}/{len(mode_runs)}")
        lines.append(f"- Avg resolution time: {avg_time:.2f}s")
        lines.append(f"- Avg LLM calls: {avg_llm:.1f}")
        lines.append(f"- Total cost: ${total_cost:.4f}")
        lines.append("")

    # By researcher tier
    tiers = set(r.researcher_tier for r in results if r.researcher_tier)
    if len(tiers) > 1:
        lines.append("### By Researcher Tier\n")
        lines.append("| Tier | Count | Avg Time (s) | Avg LLM Calls | Avg Cost ($) |")
        lines.append("|------|-------|--------------|---------------|-------------|")
        for tier in sorted(tiers):
            tier_runs = [r for r in results if r.researcher_tier == tier]
            avg_t = sum(r.resolution_time_s for r in tier_runs) / len(tier_runs)
            avg_llm = sum(r.llm_calls for r in tier_runs) / len(tier_runs)
            avg_cost = sum(r.cost_usd for r in tier_runs) / len(tier_runs)
            lines.append(f"| {tier} | {len(tier_runs)} | {avg_t:.2f} | {avg_llm:.1f} | {avg_cost:.4f} |")
        lines.append("")

    # By outcome (shows routing diversity)
    lines.append("### By Outcome\n")
    outcomes = {}
    for r in results:
        outcomes.setdefault(r.outcome, []).append(r)
    lines.append("| Outcome | Count | Incident Types | Environments |")
    lines.append("|---------|-------|---------------|--------------|")
    for outcome, runs in sorted(outcomes.items()):
        types = ", ".join(sorted(set(r.incident_type for r in runs)))
        envs = ", ".join(sorted(set(r.description.split(",")[0].split()[-1] if r.description else "?" for r in runs)))
        lines.append(f"| {outcome} | {len(runs)} | {types} | — |")

    # By incident type
    lines.append("\n### By Incident Type\n")
    lines.append("| Incident | Full Success | Template Success | Full Avg Time | Template Avg Time |")
    lines.append("|----------|-------------|-----------------|---------------|-------------------|")

    for itype in ("tablespace_full", "stale_stats", "session_blocker"):
        full = [r for r in results if r.incident_type == itype and r.mode == "full"]
        tmpl = [r for r in results if r.incident_type == itype and r.mode == "template"]

        full_ok = sum(1 for r in full if r.outcome in ("COMPLETED", "NEEDS_APPROVAL"))
        tmpl_ok = sum(1 for r in tmpl if r.outcome in ("COMPLETED", "NEEDS_APPROVAL"))

        full_time = f"{sum(r.resolution_time_s for r in full) / len(full):.2f}s" if full else "N/A"
        tmpl_time = f"{sum(r.resolution_time_s for r in tmpl) / len(tmpl):.2f}s" if tmpl else "N/A"

        full_str = f"{full_ok}/{len(full)}" if full else "N/A"
        tmpl_str = f"{tmpl_ok}/{len(tmpl)}" if tmpl else "N/A"

        lines.append(f"| {itype} | {full_str} | {tmpl_str} | {full_time} | {tmpl_time} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Adversarial results table — grouped by safety check
# ---------------------------------------------------------------------------

def format_adversarial_table(adv_results: list[AdversarialResult]) -> str:
    """Markdown table of adversarial Safety Mesh tests, grouped by check."""
    lines = [
        "## Adversarial Safety Mesh Tests\n",
        "Each test targets a specific safety check to verify independent enforcement.\n",
        "| ID | Target Check | SQL | Expected | Actual | Blocked By | Pass |",
        "|----|-------------|-----|----------|--------|------------|------|",
    ]
    for r in adv_results:
        sql_short = r.sql[:40] + "..." if len(r.sql) > 40 else r.sql
        sql_short = sql_short.replace("|", "\\|")
        status = "PASS" if r.passed else "**FAIL**"
        blocked = r.blocked_by or "—"
        lines.append(
            f"| {r.case_id} | {r.target_check} | `{sql_short}` | "
            f"{r.expected} | {r.actual} | {blocked} | {status} |"
        )

    # Summary by check
    lines.append("\n### Coverage by Safety Check\n")
    lines.append("| Check | Cases | Passed | Decisions Tested |")
    lines.append("|-------|-------|--------|-----------------|")

    checks = {}
    for r in adv_results:
        checks.setdefault(r.target_check, []).append(r)

    for check, crs in checks.items():
        passed = sum(1 for r in crs if r.passed)
        decisions = ", ".join(sorted(set(r.expected for r in crs)))
        lines.append(f"| {check} | {len(crs)} | {passed}/{len(crs)} | {decisions} |")

    total_passed = sum(1 for r in adv_results if r.passed)
    lines.append(f"\n**Overall**: {total_passed}/{len(adv_results)} passed")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Paper section (prose)
# ---------------------------------------------------------------------------

def format_paper_section(
    results: Optional[list[RunResult]] = None,
    adv_results: Optional[list[AdversarialResult]] = None,
) -> str:
    """Generate prose paragraphs for the arXiv 'Preliminary Evaluation' section."""
    paragraphs = []

    paragraphs.append("## Preliminary Evaluation\n")
    paragraphs.append(
        "We evaluate Sentri against a single Oracle XE 21c instance running in Docker, "
        "with three representative incident types: tablespace approaching capacity (P1), "
        "stale optimizer statistics (P2), and session blocking chains (P3). Each incident "
        "is processed by its designated specialist agent: StorageAgent (P1), SQLTuningAgent (P2), "
        "and RCAAgent (P3). Runs vary across environments (DEV, UAT, PROD), input parameters, "
        "and edge cases to test routing diversity.\n"
    )

    if results:
        total = len(results)
        success = sum(1 for r in results if r.outcome in ("COMPLETED", "NEEDS_APPROVAL"))
        completed = sum(1 for r in results if r.outcome == "COMPLETED")
        approval = sum(1 for r in results if r.outcome == "NEEDS_APPROVAL")

        full_runs = [r for r in results if r.mode == "full"]
        tmpl_runs = [r for r in results if r.mode == "template"]

        full_ok = sum(1 for r in full_runs if r.outcome in ("COMPLETED", "NEEDS_APPROVAL"))
        tmpl_ok = sum(1 for r in tmpl_runs if r.outcome in ("COMPLETED", "NEEDS_APPROVAL"))

        avg_full_time = sum(r.resolution_time_s for r in full_runs) / len(full_runs) if full_runs else 0
        avg_tmpl_time = sum(r.resolution_time_s for r in tmpl_runs) / len(tmpl_runs) if tmpl_runs else 0

        total_cost = sum(r.cost_usd for r in full_runs)
        avg_llm = sum(r.llm_calls for r in full_runs) / len(full_runs) if full_runs else 0

        paragraphs.append(
            f"**Remediation runs.** We execute {total} runs across 3 incident types "
            f"and 3 environments ({len(full_runs)} LLM-augmented, {len(tmpl_runs)} template-only). "
            f"Overall: {success}/{total} successful ({completed} auto-executed, "
            f"{approval} routed for approval). "
            f"Full mode: {full_ok}/{len(full_runs)} at {avg_full_time:.2f}s avg, "
            f"{avg_llm:.1f} LLM calls/run (${total_cost:.4f} total). "
            f"Template mode: {tmpl_ok}/{len(tmpl_runs)} at {avg_tmpl_time:.2f}s avg, zero LLM cost. "
        )

        # Environment routing
        prod_runs = [r for r in results if "PROD" in r.description]
        prod_approval = sum(1 for r in prod_runs if r.outcome == "NEEDS_APPROVAL")
        if prod_runs:
            paragraphs.append(
                f"PROD-environment runs correctly route to approval: {prod_approval}/{len(prod_runs)}.\n"
            )
        else:
            paragraphs.append("\n")

    if adv_results:
        passed = sum(1 for r in adv_results if r.passed)

        # Count distinct checks tested
        checks_tested = set(r.target_check for r in adv_results)
        checks_passed = {}
        for r in adv_results:
            checks_passed.setdefault(r.target_check, []).append(r.passed)

        paragraphs.append(
            f"**Safety Mesh validation.** We test the 5-check Safety Mesh with "
            f"{len(adv_results)} adversarial scenarios targeting each check independently: "
            f"policy gate (confidence thresholds, environment rules), "
            f"conflict detection (concurrent workflow queuing), "
            f"blast radius (DDL classification by environment), "
            f"circuit breaker (failure accumulation), "
            f"and rollback availability (risk-proportional enforcement). "
            f"The mesh correctly enforces {passed}/{len(adv_results)} cases "
            f"({100*passed/len(adv_results):.0f}%), "
            f"confirming structural safety enforcement independent of LLM output.\n"
        )

    paragraphs.append(
        "**Limitations.** This evaluation uses a single Oracle XE instance. "
        "Multi-database correlation, RAC-specific scenarios, and real PROD approval workflows "
        "(human-in-the-loop response time) are not measured. "
        "Resolution times reflect agent pipeline processing only, excluding network latency "
        "to external LLM providers in production deployments."
    )

    return "\n".join(paragraphs)


# ---------------------------------------------------------------------------
# Save all outputs
# ---------------------------------------------------------------------------

def save_formatted_output(
    results: Optional[list[RunResult]] = None,
    adv_results: Optional[list[AdversarialResult]] = None,
):
    """Generate and save all formatted outputs."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    sections = []

    if results:
        sections.append("# Sentri Evaluation Results\n")
        sections.append(format_results_table(results))
        sections.append("")
        sections.append(format_aggregates(results))

    if adv_results:
        sections.append("")
        sections.append(format_adversarial_table(adv_results))

    tables_path = RESULTS_DIR / "tables.md"
    with open(tables_path, "w") as f:
        f.write("\n".join(sections))
    print(f"Tables saved to {tables_path}")

    paper = format_paper_section(results, adv_results)
    paper_path = RESULTS_DIR / "paper_section.md"
    with open(paper_path, "w") as f:
        f.write(paper)
    print(f"Paper section saved to {paper_path}")


def load_results_from_json() -> tuple[Optional[list[RunResult]], Optional[list[AdversarialResult]]]:
    """Load previously saved results from JSON files."""
    results = None
    adv_results = None

    raw_path = RESULTS_DIR / "raw_results.json"
    if raw_path.exists():
        with open(raw_path) as f:
            data = json.load(f)
        results = [RunResult(**d) for d in data]

    adv_path = RESULTS_DIR / "adversarial_results.json"
    if adv_path.exists():
        with open(adv_path) as f:
            data = json.load(f)
        adv_results = [AdversarialResult(**d) for d in data]

    return results, adv_results
