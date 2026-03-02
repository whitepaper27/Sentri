# Oracle Ground Truth Docs

Verified Oracle SQL syntax reference used by Sentri's RAG system to prevent
SQL hallucinations. The LLM researcher receives these docs as context and
its generated SQL is validated against the hard rules before execution.

## Directory Structure

```
docs/oracle/
├── {version}/             # Version-specific syntax (12c, 19c, 21c, 23ai)
│   ├── tablespace/        # ALTER TABLESPACE, RESIZE, ADD DATAFILE
│   ├── performance/       # KILL SESSION, ALTER SESSION
│   ├── archive/           # RMAN, archive log management
│   ├── undo/              # UNDO tablespace management
│   ├── multitenant/       # CDB/PDB operations
│   ├── rac/               # RAC services, cluster management
│   └── standby/           # Data Guard, switchover
│
└── rules/                 # Version-INDEPENDENT hard rules
    ├── bigfile_tablespace.md
    ├── omf_paths.md
    └── cdb_pdb.md
```

## How to Add a New Doc

1. Create a `.md` file in the correct `{version}/{topic}/` folder.
2. Add YAML frontmatter with version, topic, operation, keywords, applies_to.
3. Write the verified SQL syntax with examples.
4. Copy to all three locations:
   - `src/sentri/_default_policies/docs/oracle/` (bundled)
   - `~/.sentri/docs/oracle/` (deployed by sentri init)
   - `docs/oracle/` (project root, if exists)

## Doc File Format

```markdown
---
version: "19c"
topic: tablespace
operation: alter_tablespace
keywords: [tablespace, add datafile, resize]
applies_to: [tablespace_full]
---

# Title — Oracle {version}

## Section
(SQL syntax with examples)
```

## Rule File Format

```markdown
---
rule_id: unique_rule_name
severity: CRITICAL | HIGH | MEDIUM
applies_to: [alert_type1, alert_type2]
---

# Rule Title

## Rule
(Description of the rule)

## Detection Pattern
```regex
(regex to detect the violation in SQL)
```

## Condition
(When the rule applies — e.g., "tablespace_type == BIGFILE")

## Required Action
(What to do instead)
```

## Version Fallback

If a doc doesn't exist for the target version, Sentri falls back:
1. Exact version (e.g., 21c)
2. Nearest common version (19c)
3. Other versions in order
