# Contributing to Sentri

Thanks for your interest in contributing. Sentri is an open-source project and we welcome contributions of all kinds.

## Easiest: Add a New Alert Type

The simplest way to contribute is adding a new alert `.md` file. No Python required.

1. Copy an existing alert from `alerts/` (e.g. `alerts/tablespace_full.md`)
2. Modify the email pattern, verification query, fix SQL, and rollback SQL
3. Test the regex against your alert email format
4. Submit a PR

See [Adding Alerts](docs/adding-alerts.md) for a step-by-step guide.

## Medium: Add a Proactive Health Check

Same pattern as alerts — drop a `.md` file in `checks/`.

1. Copy an existing check from `checks/` (e.g. `checks/stale_stats.md`)
2. Write the health query, threshold, and recommended action
3. Submit a PR

See [Adding Health Checks](docs/adding-alerts.md#adding-a-proactive-health-check) for details.

## Advanced: Code Contributions

For changes to Python source code:

### Setup

```bash
git clone https://github.com/whitepaper27/sentri.git
cd sentri
pip install -e ".[dev,llm]"
```

### Run Tests

```bash
python -m pytest tests/ -x -q --ignore=tests/integration --ignore=tests/e2e
```

### Lint

```bash
ruff check src/ tests/
ruff format --check src/ tests/
```

### Guidelines

- Read `CLAUDE.md` for the full architecture document
- Follow existing patterns in `src/sentri/`
- Add tests for new functionality
- Keep changes focused — one feature or fix per PR
- No secrets or credentials in committed files

### Areas We'd Love Help With

- New alert `.md` files for Oracle scenarios you've encountered
- New health check `.md` files
- Postgres/SQL Server/Snowflake adapter implementations (see `src/sentri/oracle/` for the Oracle pattern)
- Documentation improvements
- Bug reports with reproduction steps

## Reporting Issues

Use [GitHub Issues](https://github.com/whitepaper27/sentri/issues) with the appropriate template:
- **Bug Report** — something isn't working
- **Feature Request** — suggest an improvement
- **New Alert Type** — contribute an alert definition

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
