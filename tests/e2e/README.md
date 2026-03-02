# Sentri v5.0 End-to-End Testing Guide

## Prerequisites

1. **Docker Oracle DB** running on `localhost:1521/FREEPDB1`
2. **Email configured** in `config/sentri.yaml` (Gmail IMAP)
3. **Python environment**: `C:/Users/Sahil/anaconda3/envs/Python3.12/python.exe`
4. **Sentri initialized**: `python -m sentri init`

## Quick Start

```bash
# Terminal 1: Start Sentri daemon
cd C:\Users\Sahil\Desktop\Sentri
C:/Users/Sahil/anaconda3/envs/Python3.12/python.exe -m sentri start

# Terminal 2: Send test alerts
C:/Users/Sahil/anaconda3/envs/Python3.12/python.exe send_test_email.py tablespace_full
C:/Users/Sahil/anaconda3/envs/Python3.12/python.exe send_test_email.py temp_full

# Terminal 2: Check results (wait 60-90s for Scout to poll)
C:/Users/Sahil/anaconda3/envs/Python3.12/python.exe -m sentri list --last 5
C:/Users/Sahil/anaconda3/envs/Python3.12/python.exe -m sentri stats
```

## Automated E2E Test Script

```bash
# Run the full E2E test suite (sends emails, waits, verifies)
C:/Users/Sahil/anaconda3/envs/Python3.12/python.exe tests/e2e/run_e2e_tests.py
```

## Alert Types to Test

| # | Alert Type | Routes To | Expected Action |
|---|------------|-----------|-----------------|
| 1 | `tablespace_full` | StorageAgent | ADD DATAFILE to tablespace |
| 2 | `temp_full` | StorageAgent | ADD TEMPFILE to temp tablespace |
| 3 | `archive_dest_full` | StorageAgent | RMAN archive cleanup |
| 4 | `high_undo_usage` | StorageAgent | Investigate undo consumer |
| 5 | `long_running_sql` | SQLTuningAgent | Analyze/gather stats |
| 6 | `cpu_high` | SQLTuningAgent | Identify top SQL consumer |
| 7 | `session_blocker` | RCAAgent | Kill blocking session |
| 8 | `listener_down` | (not routed) | OS-level, needs lsnrctl |
| 9 | `archive_gap` | (not routed) | Data Guard, complex |

## v5.0 Pipeline Flow

```
Email → Scout (IMAP poll) → Workflow(DETECTED)
  → Supervisor (routes by alert_type via brain/routing_rules.md)
    → StorageAgent / SQLTuningAgent / RCAAgent
      → verify() → investigate() → propose() → argue/select()
        → Safety Mesh (5 checks) → execute() or AWAITING_APPROVAL
          → learn()
```

## Monitoring During Tests

```bash
# Watch live logs
tail -f logs/sentri.log

# Check latest workflows
python -m sentri list --last 10

# Show specific workflow detail
python -m sentri show <workflow_id>

# Audit trail
python -m sentri audit --last 10

# Overall stats
python -m sentri stats
```

## Troubleshooting

- **Scout not picking up emails**: Check `SENTRI_EMAIL_PASSWORD` env var is set
- **Routing warning**: Run `python -m sentri init --force` to refresh policy files
- **Oracle connection fails**: Verify Docker is running: `docker ps`
- **Workflow stuck in DETECTED**: Supervisor hasn't polled yet (waits 10s between cycles)
- **VERIFICATION_FAILED**: Alert metrics don't match live DB (expected for test alerts)
- **ESCALATED**: Confidence too low (<0.60) — normal for template-only mode
