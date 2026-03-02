# Sentri Environment Definitions

This directory contains database environment configuration files. Each `.md` file
defines a single Oracle database that Sentri monitors and manages.

## How to Add a New Database Environment

1. Create a new `.md` file in this directory. Use the naming convention:
   `<environment>_<short_name>.md` (e.g., `prod_db_07.md`, `dev_db_01.md`).

2. Add the required YAML frontmatter at the top of the file.

3. Fill in the database details in the structured sections below the frontmatter.

4. Register the database connection in `~/.sentri/config/sentri.yaml` (credentials
   are stored there, not in these policy files).

5. Restart Sentri or run `sentri reload` to pick up the new environment.

## Required YAML Frontmatter

Every environment file must include the following frontmatter fields:

```yaml
---
type: environment
database_id: <UNIQUE-ID>        # Unique identifier (e.g., PROD-DB-07)
environment: <DEV|UAT|PROD>     # Environment classification
autonomy_level: <LEVEL>         # One of: AUTONOMOUS, SUPERVISED, ADVISORY
---
```

### Autonomy Levels

| Level | Description | Behavior |
|-------|-------------|----------|
| **AUTONOMOUS** | Full auto-execution | All verified alerts are fixed automatically, no approval needed. Suitable for DEV. |
| **SUPERVISED** | Conditional approval | Low-risk fixes auto-execute; high-risk fixes require approval. Suitable for UAT. |
| **ADVISORY** | Always requires approval | All changes require explicit human approval before execution. Required for PROD. |

## Required Content Sections

Each environment file should contain the following information:

| Field | Description | Example |
|-------|-------------|---------|
| **Database Name** | Oracle database name | `PRODDB` |
| **Oracle Version** | Major Oracle version | `19c`, `21c` |
| **Architecture** | Database architecture | `STANDALONE`, `CDB`, `RAC` |
| **Connection** | Connection string template | `oracle://sentri_agent@host:1521/SERVICE` |
| **Critical Schemas** | Schemas requiring extra approval | `["FINANCE", "HR"]` or `none` |
| **Business Owner** | Team or person responsible | `CTO Office` |
| **DBA Owner** | DBA team responsible | `Senior DBA Team` |
| **Notes** | Operational notes and constraints | Maintenance windows, special rules |

## Connection Credentials

Database passwords and sensitive credentials are **never** stored in these policy
files. Credentials are configured in `~/.sentri/config/sentri.yaml` using
environment variable references:

```yaml
databases:
  - name: PROD-DB-07
    connection_string: oracle://sentri_agent@prod-scan:1521/PRODDB
    credentials_env: PROD_DB_07_PASSWORD
```

## Environment Classification Rules

- **DEV**: Development databases used for testing. Low risk. Full autonomy.
- **UAT**: User acceptance testing databases. Medium risk. Supervised autonomy.
- **PROD**: Production databases serving live users. High risk. Advisory only.

The environment classification directly controls the approval workflow. See
`workflows/approval_workflow.md` for details.
