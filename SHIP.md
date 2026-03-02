# SENTRI — Ship Plan

## What This Document Is

This is the shipping playbook for Sentri. CLAUDE.md defines how Sentri works. SHIP.md defines how to get it into the world — README, documentation, demo, video, content, and research publications.

**When using Claude Code**: Read this file for any task related to shipping, publishing, marketing, or documentation. Read CLAUDE.md for any task related to architecture, code, or implementation.

---

## PROJECT IDENTITY

### One-Liner
Sentri is an open-source AI agent that detects, diagnoses, and fixes Oracle database problems autonomously.

### Elevator Pitch (30 seconds)
DBAs get paged at 2am for the same problems — tablespace full, slow SQL, session blocking. The fix is usually predictable. Sentri monitors your alert emails, verifies the problem is real, investigates using 12 DBA tools, generates multiple fix candidates, judges the best tradeoff, and executes safely — all through `.md` policy files a DBA can read and edit. No code changes to add new alert types. Structural safety that the LLM cannot bypass. Works without an API key using template fixes.

### Positioning Statement
Sentri is NOT a chatbot wrapper around an LLM. It's an autonomous DBA agent with multi-agent intelligence, structural safety enforcement, and policy-as-markdown extensibility. It reasons about database problems the way experienced DBAs do — consider multiple options, weigh tradeoffs, pick the safest effective fix.

### Target Audience (Be Honest)
- DBA teams managing 1–50 Oracle databases
- Already running OEM or monitoring that sends email alerts
- Want autonomous fixes for common issues without building custom scripts
- Comfortable with Python, direct database connections, YAML config
- NOT for: teams with 500+ databases (need collector architecture, that's v7.0)
- NOT for: non-Oracle databases yet (Postgres/SQL Server is future)

### Key Numbers (v5.0)
- 90 source files, ~14,700 LOC
- 576 tests (412 existing + 164 new in v5.0, zero regressions)
- 12 DBA investigation tools
- 9 alert types (4 working, 5 ready to test)
- 7 proactive health checks
- 4 specialist agents (Storage, SQL Tuning, RCA, Proactive)
- 5-check Safety Mesh
- 3-level LLM fallback (Agentic → One-shot → Template)
- Works with Claude, OpenAI, Gemini — or NO LLM at all (template mode)

---

## PHASE 1: GITHUB LAUNCH (Week 1)

### Task 1: README.md

**Priority**: HIGHEST. This is what every visitor sees first. A DBA decides to star or bounce in 60 seconds.

**File**: `README.md` at repo root

**Structure**:

```markdown
# 🛡️ Sentri — AI-Powered Autonomous DBA Agent

> Detects, diagnoses, and fixes Oracle database problems automatically.
> Drop a `.md` file to add new alert types — zero code changes.

[badges: tests passing, python version, license, GitHub stars]

## What Sentri Does

[30-second GIF: alert email → Sentri fixes tablespace → done]

Sentri monitors your DBA alert emails, verifies problems against
real database state, investigates using 12 specialized tools,
generates multiple fix candidates, scores them against configurable
criteria, and executes the best option — with full rollback
guarantee and immutable audit trail.

**Works without an LLM API key** using template-based fixes.
Add Claude, OpenAI, or Gemini for intelligent investigation.

## Quick Start

    pip install sentri
    sentri init
    sentri db add prod-01 --connect oracle://user:pass@host:1521/SID --env production
    sentri start

Or try the demo (no Oracle needed):

    git clone https://github.com/[user]/sentri.git
    cd sentri
    docker-compose up

## Who Is Sentri For?

- DBA teams managing 1–50 Oracle databases
- Running OEM or monitoring that sends email alerts
- Want autonomous fixes for common issues (tablespace, temp, slow SQL)
- Comfortable with direct database connections from a central server

## How It Works

[simplified architecture diagram — Sources → Supervisor → Specialists → Safety Mesh → Execute]

1. **Scout** monitors your IMAP inbox for DBA alert emails
2. **Supervisor** correlates alerts and routes to the right specialist
3. **Specialist agents** investigate with 12 DBA tools, propose fixes
4. **Argue/Select** generates 3–5 candidates, LLM judge picks the best
5. **Safety Mesh** enforces policy gate, blast radius, circuit breaker
6. **Executor** runs the fix with pre/post metrics and auto-rollback
7. **Analyst** learns from outcomes to improve future decisions

## Add a New Alert Type (Zero Code)

Drop a `.md` file in `~/.sentri/alerts/`:

    ---
    alert_type: tablespace_full
    severity: HIGH
    risk_level: MEDIUM
    ---

    ## Email Pattern
    (regex to match alert email)

    ## Verification Query
    (SQL to verify alert is real)

    ## Forward Action
    (SQL to fix the issue)

    ## Rollback Action
    (SQL to undo the fix)

No enum, no code change, no restart. Sentri picks it up on the next poll.

## Features

**Intelligence**
- 12 DBA investigation tools (tablespace, SQL plans, session diagnostics, wait events)
- Argue/select pattern — multiple candidates scored by configurable criteria
- Ground truth RAG — verified Oracle syntax prevents SQL hallucination
- Short-term memory (24h) + long-term patterns (90 days)

**Safety**
- 5-check Safety Mesh (policy gate, conflict detection, blast radius, circuit breaker, rollback)
- Structural enforcement — LLM cannot bypass safety checks
- Confidence-based routing (auto-execute in DEV, approval in PROD)
- Auto-rollback on failure

**Autonomy**
- 4 specialist agents (Storage, SQL Tuning, RCA, Proactive Health)
- Cost gate — historical success rate determines LLM depth (most alerts = zero LLM cost)
- Proactive health checks — catches problems BEFORE they trigger alerts
- Three-level fallback — works without an API key

**Extensibility**
- `.md`-driven everything — alerts, checks, policies, agent behavior
- Multi-provider LLM support (Claude, OpenAI, Gemini)
- 9 alert types out of the box, add more by dropping a file

## Alert Types

| Alert | Status | Specialist |
|-------|--------|-----------|
| tablespace_full | ✅ Working | Storage Agent |
| temp_full | 🔧 Ready to test | Storage Agent |
| archive_dest_full | 🔧 Ready to test | Storage Agent |
| high_undo_usage | 🔧 Ready to test | Storage Agent |
| long_running_sql | 🔧 Ready to test | SQL Tuning Agent |
| cpu_high | 🔧 Ready to test | SQL Tuning Agent |
| session_blocker | 🔧 Ready to test | RCA Agent |
| listener_down | 📋 Needs OS support | — |
| archive_gap | 📋 Complex | — |

## Configuration

All config lives in `~/.sentri/config/sentri.yaml`:

    email:
      imap_server: mail.corp.com
      folder: DBA_ALERTS
      poll_interval: 60

    safety:
      circuit_breaker_max_failures: 3
      change_windows:
        - days: [mon, tue, wed, thu, fri]
          hours: "08:00-18:00"

    autonomy:
      development: auto_execute
      uat: approve_high_risk
      production: always_approve

    llm:
      provider: claude
      model: claude-sonnet-4-5-20250514
      fallback: template

## Documentation

- [Getting Started](docs/getting-started.md)
- [Configuration Reference](docs/configuration.md)
- [Adding Alert Types](docs/adding-alerts.md)
- [Adding Health Checks](docs/adding-checks.md)
- [Architecture Overview](docs/architecture.md)
- [Safety Model](docs/safety-model.md)
- [FAQ](docs/faq.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The easiest way to contribute
is adding new alert `.md` files for Oracle scenarios you've seen.

## License

Apache 2.0
```

**Rules for README**:
- No jargon a DBA wouldn't know (no "Universal Agent Contract", no "multi-horizon memory")
- Show, don't tell — GIF first, explanation second
- Honest about scope (1–50 databases, Oracle only, email-triggered)
- The quick start must actually work in under 5 minutes

---

### Task 2: LICENSE

**File**: `LICENSE` at repo root
**Content**: Apache 2.0 (standard text)
**Why Apache 2.0**: Enterprise-friendly, allows commercial use, patent protection. Same license as Kubernetes, TensorFlow, Airflow.

---

### Task 3: GitHub Actions CI

**File**: `.github/workflows/tests.yml`

```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]"
      - run: pytest --tb=short -q
```

Badge in README: `![Tests](https://github.com/[user]/sentri/actions/workflows/tests.yml/badge.svg)`

---

### Task 4: Docker Demo Environment

**File**: `docker-compose.yml` at repo root

**What it spins up**:
- `oracle-xe`: Oracle 21c XE container with pre-seeded problems:
  - USERS tablespace at 92% capacity
  - Tables with stale stats (60+ days)
  - A long-running SQL session
  - A blocking session
- `mail-server`: Lightweight IMAP server (e.g., GreenMail or MailHog) with pre-loaded alert emails matching the seeded problems
- `sentri`: Sentri configured to connect to both, running in foreground with verbose logging

**Experience**: `docker-compose up` → watch Sentri detect and fix 3 problems in real-time in the terminal output. No Oracle license, no real database, no email server setup.

**Seed scripts**: `demo/seed_oracle.sql` creates the problems. `demo/seed_emails/` contains pre-written alert emails matching OEM format.

---

### Task 5: GitHub Repository Setup

**Files**:
- `.github/ISSUE_TEMPLATE/bug_report.md`
- `.github/ISSUE_TEMPLATE/feature_request.md`
- `.github/ISSUE_TEMPLATE/new_alert_type.md` (special — for contributing `.md` alert definitions)
- `.github/PULL_REQUEST_TEMPLATE.md`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `.gitignore` (Python, venv, .env, sentri.db, logs/)

**CONTRIBUTING.md** structure:
```
## How to Contribute

### Easiest: Add a New Alert Type
1. Copy an existing alert .md file from alerts/
2. Modify the email pattern, verification query, fix SQL
3. Submit a PR

### Medium: Add a Proactive Health Check
1. Write a check .md file for checks/
2. Include health query, threshold, recommended action
3. Submit a PR

### Advanced: Add DBA Tools or Improve Agents
1. Read CLAUDE.md for architecture details
2. Follow the existing tool pattern in src/sentri/llm/tools.py
3. Add tests
4. Submit a PR
```

---

### Task 6: PyPI Package

**File**: `pyproject.toml`

```toml
[project]
name = "sentri"
version = "5.0.0"
description = "AI-powered autonomous DBA agent"
requires-python = ">=3.11"
license = "Apache-2.0"
keywords = ["dba", "oracle", "ai", "agent", "database", "automation"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Topic :: Database :: Database Engines/Servers",
    "License :: OSI Approved :: Apache Software License",
]

[project.scripts]
sentri = "sentri.cli.main:app"
```

Publish: `python -m build && twine upload dist/*`

Test: `pip install sentri && sentri init && sentri --help` must work on a clean machine.

---

## PHASE 2: DOCUMENTATION (Week 2)

### Task 7: Documentation Site

**Location**: `docs/` directory in repo (GitHub renders markdown natively, or use mkdocs for a nicer site)

**Files to create**:

#### docs/getting-started.md
```
# Getting Started

## Prerequisites
- Python 3.11+
- Network access to your Oracle databases
- An IMAP mailbox receiving DBA alerts (from OEM, Zabbix, custom)
- (Optional) LLM API key (Claude, OpenAI, or Gemini)

## Install
    pip install sentri

## Initialize
    sentri init
This creates ~/.sentri/ with default policies, sample config, and SQLite database.

## Configure Databases
    sentri db add prod-01 \
      --connect oracle://sentri_monitor:xxx@host:1521/SID \
      --env production

## Configure Email
Edit ~/.sentri/config/sentri.yaml:
    email:
      imap_server: mail.corp.com
      username: sentri@corp.com
      password_env: SENTRI_IMAP_PASSWORD
      folder: DBA_ALERTS
      poll_interval: 60

## Start
    sentri start

## Verify
    sentri stats
    sentri list --last 5
```

#### docs/configuration.md
Full reference for every field in `sentri.yaml`. Every field, what it does, default value, example values.

#### docs/adding-alerts.md
Step-by-step guide to writing an alert `.md` file. Include a worked example from scratch (e.g., "Let's add support for ORA-01555 Snapshot Too Old alerts").

#### docs/adding-checks.md
Same format — how to write a proactive health check `.md`. Worked example: "Let's add a check for indexes with high clustering factor."

#### docs/architecture.md
**Public-facing simplified version of CLAUDE.md.** NOT the full internal doc. Cover:
- The 6 core agents (one paragraph each)
- The 4 specialist agents (one paragraph each)
- The Supervisor routing model
- The Safety Mesh (5 checks)
- The argue/select pattern
- The three-level LLM fallback
- The cost gate

Do NOT include: internal implementation details, future vision (v5.1/v6.0/v7.0), known issues, tech debt. Those stay in CLAUDE.md.

#### docs/safety-model.md
Deep dive on how Sentri keeps databases safe. This is a trust-building doc for DBA managers evaluating Sentri. Cover:
- Structural safety (architecture enforces, not prompts)
- The 5 Safety Mesh checks with examples
- Confidence-based routing (DEV auto, PROD approval)
- Ground truth SQL validation
- Auto-rollback on failure
- Immutable audit trail
- Circuit breaker (3 failures → stop)
- What CAN'T Sentri do? (honest about limitations — no DDL on partitioned tables, no RAC-specific fixes, etc.)

#### docs/faq.md
```
Q: Does Sentri need an LLM API key?
A: No. It works with template-based fixes (zero LLM cost). Add an
   API key for intelligent investigation and argue/select.

Q: Will Sentri drop my production tables?
A: No. The Safety Mesh blocks DDL above LOW risk without approval.
   DROP statements are classified as CRITICAL blast radius.
   Production always requires human approval regardless of confidence.

Q: How much does the LLM cost?
A: Most alerts on stable databases hit the template fast path (zero cost).
   Novel or ambiguous alerts use 1-3 LLM calls (~$0.01-0.10 per alert
   with Claude Sonnet). The cost gate makes this data-driven.

Q: Can I use Sentri with Postgres/SQL Server?
A: Not yet. The architecture supports multi-database (the agent logic is
   DB-agnostic), but only Oracle tools and syntax docs exist today.
   Postgres support is on the roadmap.

Q: How is this different from Oracle Autonomous Database?
A: Oracle Autonomous DB is a managed cloud service. Sentri is an
   open-source agent that works with ANY Oracle database — on-prem,
   cloud, any version. It also learns from your specific environment
   and lets you customize behavior via .md files.

Q: What if Sentri makes a mistake?
A: Every fix has a pre-captured rollback SQL. If post-execution validation
   fails, Sentri auto-rollbacks and marks the workflow as ROLLED_BACK.
   The circuit breaker stops after 3 failures on the same database.
   PROD always requires human approval before execution.
```

---

## PHASE 3: DEMO & VIDEO (Week 2-3)

### Task 8: Demo GIF (30 seconds)

**Tool**: asciinema + agg (for GIF conversion), or screen recording + gifski

**Script** (what the GIF shows):
```
# Terminal 1: Sentri running
$ sentri start --verbose

[Scout] New alert: TABLESPACE_FULL on PROD-DB-07 (USERS at 95%)
[Auditor] Verified: actual usage 94.7% (confidence: 0.97)
[Supervisor] Routing to: StorageAgent
[Researcher] Investigating with tools...
  → get_tablespace_info: USERS is BIGFILE, OMF enabled
  → get_storage_info: /u01/oradata/USERS01.dbf, autoextend ON
[Researcher] Proposed 3 candidates:
  1. RESIZE BIGFILE +2G (score: 0.91)
  2. Enable autoextend (score: 0.74)
  3. Compress cold segments (score: 0.52)
[Safety Mesh] ✓ Policy gate ✓ No conflicts ✓ Blast: MEDIUM ✓ Circuit OK ✓ Rollback ready
[Executor] Executing: ALTER TABLESPACE USERS RESIZE 20G
[Executor] Validation: 94.7% → 61.3% ✓
[Workflow] COMPLETED in 8.3s
```

**Embed in README** as the first visual element after the one-liner.

### Task 9: Demo Video (3-5 minutes)

**Tool**: Screen recording (OBS or QuickTime) with terminal + optional VS Code side-by-side

**Script**:
```
[0:00] Title card: "Sentri — AI-Powered Autonomous DBA Agent"

[0:10] "What if your database could fix itself?"
       Show: terminal, docker-compose up, services starting

[0:30] "An OEM alert arrives..."
       Show: alert email in the fake IMAP inbox

[0:45] "Sentri detects it, verifies it's real..."
       Show: Scout + Auditor logs in terminal

[1:00] "Investigates using specialized DBA tools..."
       Show: Researcher tool calls in terminal (tablespace info, storage info)

[1:15] "Generates multiple fix candidates and judges the best one..."
       Show: argue/select output (3 candidates with scores)

[1:30] "Safety Mesh checks pass..."
       Show: 5 green checkmarks in terminal output

[1:45] "Fix applied. Tablespace back to 61%."
       Show: executor output, validation result

[2:00] "Now let's add a completely new alert type..."
       Show: create a new .md file in alerts/, save it
       Show: Sentri picks it up on next poll, no restart needed

[2:30] "Proactive checks catch problems BEFORE alerts fire..."
       Show: stale_stats check finding, Sentri gathering stats automatically

[3:00] "sentri stats shows the full picture..."
       Show: CLI output with success rates, workflows processed

[3:15] "All of this through .md files a DBA can read and edit."
       Show: quick scroll through brain/global_policy.md and alerts/tablespace_full.md

[3:30] "Open source. Apache 2.0. Install in 2 minutes."
       Show: pip install sentri, sentri init, sentri start

[3:45] End card: GitHub link, star count
```

**Upload to**: YouTube (unlisted or public), embed in README and docs.

### Task 10: Asciinema Recording (Optional)

Pure terminal recording. People can copy commands. Embed in docs/getting-started.md.

---

## PHASE 4: CONTENT & VISIBILITY (Week 3-4)

### Task 11: LinkedIn Launch Post

**Tone**: Professional but personal. "I built this" energy. Not corporate marketing.

**Length**: Under 1300 characters (optimal for LinkedIn engagement).

**Structure**:
```
[Hook — personal, specific]
After 20+ years in tech and 8+ in AI/ML, I built something
I wish existed when I was on-call at 2am.

[What]
Sentri is an open-source AI agent that detects, diagnoses, and
fixes Oracle database problems autonomously.

[How — 3 bullets, concrete]
• Drop a .md file → Sentri learns a new alert type (zero code)
• LLM generates 3-5 fix candidates, a judge picks the best tradeoff
• A 5-check Safety Mesh structurally prevents dangerous SQL from executing

[Numbers — credibility]
90 source files. ~14,700 lines of code. 576 tests. 12 DBA tools.
4 specialist agents. Works without an API key.

[Differentiation — one sentence]
This isn't a chatbot — it's a multi-agent system that reasons about
database problems the way an experienced DBA does.

[CTA]
Open source, Apache 2.0: [GitHub link]

Try the Docker demo — watch it fix a tablespace in under 10 seconds.

#AI #DBA #Oracle #OpenSource #AgenticAI #DatabaseAutomation
```

**Image**: Include the v5.0 architecture diagram (Sources → Supervisor → Specialists → Safety Mesh → Execute) as a clean PNG. Create using Mermaid, draw.io, or Excalidraw.

---

### Task 12: Twitter/X Launch Thread

**Length**: 6-8 tweets

```
🧵 1/ I built an AI agent that fixes Oracle databases at 2am
so you don't have to.

Open source. 576 tests. Works without an API key.

Here's how it works: 👇

2/ The problem: DBAs get paged for the same issues.
Tablespace full. Slow SQL. Blocking sessions.
The fix is usually the same 3 commands.
Why is a human doing this at 2am?

3/ Sentri watches your alert emails, verifies the problem
is real by querying the actual database, then investigates
using 12 specialized DBA tools.

It doesn't guess. It checks.

4/ The key innovation: argue/select.

Instead of "LLM gives one answer," Sentri generates 3-5
fix candidates. A separate LLM judge scores each on:
- Root cause fix (30%)
- Reversibility (25%)
- Side effects (20%)
- Speed (15%)
- History (10%)

Best tradeoff wins.

5/ Safety is structural, not prompt-based.

A 5-check Safety Mesh sits between EVERY agent and the database:
✓ Policy gate (allowed now?)
✓ Conflict detection (another fix running?)
✓ Blast radius (how dangerous?)
✓ Circuit breaker (too many failures?)
✓ Rollback guarantee (can we undo?)

The LLM CANNOT bypass this.

6/ Want to add a new alert type?

Drop a .md file in alerts/. That's it.
No code. No restart. No enum.
Sentri picks it up on the next poll.

A DBA who's never written Python can extend the system.

7/ The cost story: most alerts cost $0.

Historical success rate > 95%? → template fix, zero LLM calls.
Novel or ambiguous? → full argue/select (2-3 LLM calls, ~$0.05).

Data-driven routing, not "call the API every time."

8/ 90 files. ~14,700 LOC. 576 tests.
4 specialist agents. 12 DBA tools. 9 alert types.

Open source. Apache 2.0.

🔗 [GitHub link]

Try the Docker demo — watch it fix a tablespace
in under 10 seconds. No Oracle license needed.
```

---

### Task 13: Medium Article

**Title**: "Building an Autonomous DBA: How I Designed a Self-Healing Database Agent with Multi-Agent Architecture"

**Length**: 2,500–3,500 words

**Tone**: Technical storytelling. Not a tutorial, not a press release. "Here's the problem, here's how I thought about it, here's what I built."

**Outline**:

```
# Building an Autonomous DBA

## The 2am Problem
[Personal — the on-call experience. Predictable alerts, predictable fixes.
Why is a human doing this?]

## Why Existing Tools Don't Solve This
[OEM alerts but doesn't fix. Ansible fixes but doesn't reason.
Scripts work until the edge case. ChatGPT knows SQL but shouldn't
touch your production database.]

## The Architecture: Agents That Reason
[The multi-agent model. Not one mega-agent — specialists.
Storage Agent for tablespace problems. SQL Tuning Agent for
performance. RCA Agent for complex incidents. Each has domain
expertise, shared tools, independent improvement.]

## The Key Innovation: Argue and Select
[Why single-shot LLM generation isn't good enough for databases.
Generate 3-5 candidates. Score on 5 dimensions. Pick the best
tradeoff. Scoring weights in .md files — DBAs tune without code.
Include a real example with scoring output.]

## Safety as Architecture, Not Prompts
[The Safety Mesh. Why prompt-based guardrails fail.
Structural enforcement — the execution path goes through
5 checks, the LLM cannot bypass them. Compare to OpenClaw's
permission-based model. Include the 5-check diagram.]

## .md-Driven Everything
[Policy-as-code in markdown. Drop a file, get autonomous
remediation. Why this matters for DBA adoption — they configure,
they don't code. Show a real alert .md file annotated.]

## The Cost Story
[Template fast path for known fixes. Cost gate checks historical
success rate. Most alerts = zero LLM cost. Argue/select only
for novel scenarios. Include the cost table.]

## What I Learned Building This
[Honest reflections. What worked (evolutionary architecture,
no rewrites). What was hard (SQL hallucination, ground truth).
What surprised me (the cost gate made argue/select affordable).]

## What's Next
[Brief mention of task decomposition, collectors, RL learning.
Don't over-detail — just enough to show the vision.]

## Try It
[GitHub link, Docker demo, pip install. Clear CTA.]
```

**SEO keywords to include naturally**: autonomous database, DBA automation, AI agent, Oracle DBA, multi-agent system, LLM safety, database self-healing, open source DBA tool.

---

### Task 14: Hacker News Post

**Title**: "Sentri – Open-source AI agent that autonomously fixes Oracle database problems"

**Type**: Show HN

**Comment** (post by author):
```
Hi HN, I built Sentri because I was tired of being paged at 2am
for predictable database issues.

It's a multi-agent system (4 specialist agents) that monitors
DBA alert emails, investigates problems using 12 read-only Oracle
tools, generates multiple fix candidates, and executes the best
one — with full rollback guarantee.

What makes it different from "just wrap an LLM":
- Safety is structural (5-check mesh), not prompt-based
- Works WITHOUT an API key (template mode)
- .md files to add new alert types (zero code)
- Cost gate: most alerts = $0 (template fast path)

~14,700 LOC, 576 tests, Apache 2.0. Docker demo included
so you can watch it fix a tablespace without a real database.

I'd love feedback on the architecture (CLAUDE.md in the repo
has the full design doc) and ideas for what Oracle alert types
to add next.
```

---

## PHASE 5: RESEARCH & EB-1A (Week 4-8)

### Task 15: Technical Whitepaper

**Title**: "Sentri: Multi-Agent Architecture for Autonomous Database Remediation with Structural Safety Enforcement"

**Target venue**: arXiv preprint (cs.DB or cs.AI), then submit to VLDB, SIGMOD workshop, or AAAI workshop on AI for systems.

**Length**: 10-14 pages, standard ACM/IEEE format.

**Abstract** (~200 words):
```
Autonomous database management requires AI systems that can reliably
detect, diagnose, and remediate operational issues without human
intervention. Existing approaches fall into two categories: rule-based
automation systems (Ansible, StackStorm) that execute predefined
playbooks but cannot reason about novel problems, and LLM-based
agents (OpenClaw, AutoGPT) that can reason but lack domain-specific
safety guarantees for database operations. We present Sentri, an
open-source multi-agent system for autonomous L3 database
administration. Sentri introduces five key contributions:
(1) an argue/select pattern where specialist agents generate
multiple remediation candidates scored by a configurable reward
function; (2) a structural Safety Mesh that architecturally enforces
safety constraints independent of LLM output; (3) policy-as-markdown
extensibility enabling zero-code addition of alert types and health
checks; (4) a cost-aware execution gate routing between template,
one-shot, and full LLM-judged remediation based on historical
success rates; (5) multi-horizon memory combining 24-hour short-term,
90-day long-term, and failure-specific recall. We evaluate Sentri
across 9 Oracle alert types with 576 tests demonstrating zero
regressions across 13 release milestones.
```

**Paper structure**:
```
1. Introduction
   - DBA workload problem (cite: DBA survey data, on-call statistics)
   - L1-L5 autonomy scale for database operations
   - Why L3 is the right target (L4-L5 premature, L2 insufficient)
   - Contributions list (5 items from abstract)

2. Related Work
   2.1 Database Self-Tuning
       - Oracle Autonomous Database (Pavlo & Zhang, 2019)
       - OtterTune (Van Aken et al., 2017)
       - CDBTune (Zhang et al., 2019)
       - UDO (Wang et al., 2021)
   2.2 LLM-Based Agents for Systems
       - OpenClaw (Steinberger, 2025) — personal AI agent
       - DB-GPT (Xue et al., 2023) — LLM for database diagnosis
       - D-Bot (Zhou et al., 2023) — LLM database administrator
   2.3 Multi-Agent Systems
       - AutoGen (Wu et al., 2023)
       - MetaGPT (Hong et al., 2023)
       - CAMEL (Li et al., 2023)

3. System Architecture
   3.1 Design Principles (policy-as-code, LLM-augmented not dependent,
       structural safety, evolutionary architecture)
   3.2 Agent Pipeline (6 core agents + 4 specialists)
   3.3 Universal Agent Contract (7-step: verify through learn)
   3.4 Supervisor with Category-Aware Correlation
   3.5 Concurrency Model (lane-per-database)

4. Key Innovations
   4.1 Argue/Select Pattern
       - Multi-candidate generation
       - LLM-as-judge with configurable scoring
       - Comparison to single-shot generation (ablation)
   4.2 Structural Safety Mesh
       - 5-check enforcement pipeline
       - Comparison to prompt-based guardrails
       - Formal properties (safety guarantees)
   4.3 Cost-Aware Execution Gate
       - Historical success routing
       - Cost analysis across alert types
   4.4 Ground Truth RAG for SQL Generation
       - Version-aware Oracle syntax verification
       - Hallucination prevention rates
   4.5 Multi-Horizon Memory
       - Short-term (24h), long-term (90d), failure-specific
       - Impact on repeated alert handling

5. Evaluation
   5.1 Alert Coverage (9 types, 3 domains)
   5.2 Test Coverage (576 tests, zero regressions v1→v5)
   5.3 Cost Analysis (template vs one-shot vs argue/select)
   5.4 Safety Analysis (false positive rate, hallucination prevention)
   5.5 Evolutionary Architecture (LOC growth, test preservation)

6. Discussion
   - Comparison with OpenClaw (structural vs permission safety)
   - Multi-database extensibility path
   - Limitations (Oracle-only, direct-connect only, email-triggered)
   - Future: task decomposition, collectors, RL learning

7. Conclusion

References
```

**EB-1A contributions this paper establishes**:
1. Argue/select for database remediation — original contribution
2. Structural safety mesh — original contribution
3. Policy-as-markdown extensibility model — original contribution
4. Cost-aware execution gate — original contribution
5. Multi-horizon memory for operational AI — original contribution

Each of these should be explicitly framed as "to the best of our knowledge, this is the first system to..." in the paper.

---

### Task 16: Blog Series (5 parts, Medium or personal blog)

Publish one per week. Each links to GitHub. Each builds EB-1A evidence.

```
Part 1: "Why I Built Sentri: The 2am DBA Problem"
  - Personal story, the problem, the vision
  - ~1,200 words, launch alongside GitHub release

Part 2: "Policy-as-Markdown: How Sentri Lets DBAs Configure AI Without Code"
  - Deep dive on .md-driven architecture
  - Walk through creating a custom alert type
  - ~1,500 words

Part 3: "Argue/Select: How Sentri Chooses the Best Database Fix"
  - The multi-candidate pattern, LLM-as-judge, scoring weights
  - Real example with scoring output
  - ~1,500 words

Part 4: "Why Prompt Guardrails Aren't Enough: Sentri's Structural Safety Mesh"
  - Compare prompt-based vs structural safety
  - The 5-check pipeline with examples
  - Reference OpenClaw security incidents as motivation
  - ~1,500 words

Part 5: "From Fixed Pipeline to Agent Network: Building Sentri v1 Through v5"
  - Evolution story: how architecture grew without rewrites
  - 576 tests, zero regressions, evolutionary design
  - What's next (task decomposition, collectors, RL)
  - ~1,500 words
```

---

### Task 17: Conference Talk Proposal

**Target**: PyCon 2027, VLDB 2027, Oracle CloudWorld, DevOps Days, AI Engineer Summit

**Title**: "Multi-Agent Architecture for Autonomous Database Operations: From Alert to Fix in Under 60 Seconds"

**Abstract** (250 words):
```
We present Sentri, an open-source multi-agent system that
autonomously remediates Oracle database incidents. Unlike
traditional automation (Ansible, Rundeck) that executes predefined
playbooks, Sentri reasons about problems using specialist agents —
a Storage Agent for capacity issues, a SQL Tuning Agent for
performance problems, and an RCA Agent for complex correlated
incidents. Each agent follows a Universal Agent Contract: verify
the problem, investigate with domain-specific tools, generate
multiple fix candidates, score them via an LLM judge, and execute
the best option through a 5-check structural Safety Mesh.

Key innovations include: (1) an argue/select pattern that generates
3-5 remediation candidates scored against configurable criteria,
replacing single-shot LLM generation; (2) structural safety
enforcement where the architecture itself prevents dangerous SQL
from reaching execution, independent of LLM output; (3) a
cost-aware execution gate that routes between template (zero LLM
cost), one-shot, and full argue/select based on historical success
rates. The system is entirely extensible via markdown files — DBAs
add new alert types by dropping a .md file, no code changes required.

We demonstrate the system fixing 9 Oracle alert types across
storage, performance, and root-cause domains, with 576 passing
tests and zero regressions across 13 milestones from v1.0 to v5.0.
The talk includes a live demo of Sentri detecting and fixing a
tablespace issue end-to-end in under 60 seconds.
```

---

## TIMELINE SUMMARY

| Week | Deliverables | Goal |
|------|-------------|------|
| Week 1 | README.md, LICENSE, CI, Docker demo, PyPI, GitHub setup | **Launchable repo** |
| Week 2 | docs/ (getting-started, config, adding-alerts, architecture, safety, FAQ) | **Usable by others** |
| Week 2-3 | Demo GIF (30s), demo video (3-5min), asciinema | **Visual proof it works** |
| Week 3 | LinkedIn post, Twitter thread, HN Show post | **Public launch** |
| Week 3-4 | Medium article (2,500-3,500 words) | **Deep-dive visibility** |
| Week 4-6 | Blog series (5 parts, one per week) | **Sustained visibility + EB-1A evidence** |
| Week 6-8 | Technical whitepaper (arXiv) | **Research credibility + EB-1A evidence** |
| Week 8+ | Conference talk proposal | **Speaking credibility + EB-1A evidence** |

---

## RULES FOR ALL CONTENT

1. **Honest about scope**: 1-50 Oracle databases, email-triggered, direct connections. Don't oversell.
2. **Show, don't tell**: GIF/video first, explanation second. Code examples > feature lists.
3. **DBA language, not engineer language**: "fixes your tablespace" not "Universal Agent Contract with argue/select pattern."
4. **Always link to GitHub**: Every piece of content ends with the repo link.
5. **Consistent messaging**: The 3 differentiators are always: (a) .md-driven extensibility, (b) structural safety, (c) argue/select intelligence.
6. **No future promises in public content**: Don't mention v5.1/v6.0/v7.0 in README, articles, or talks. Ship what's built. Tease "what's next" in one sentence maximum.
7. **The CLAUDE.md stays private**: It's the internal architecture doc. The public `docs/architecture.md` is a simplified version.

---

_This document is the ship plan for Sentri v5.0. Updated 2026-02-26._
