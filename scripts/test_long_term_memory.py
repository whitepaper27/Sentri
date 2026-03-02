"""Manual test: Long-Term Memory (v3.3) — end-to-end prompt verification.

Inserts realistic historical data into a temp SQLite DB and shows exactly
what the LLM researcher prompt would look like. Verifies:
1. Biweekly recurrence pattern (tablespace_full every ~14 days)
2. Day-of-week pattern (archive_dest_full every Friday)
3. Failure stats (RESIZE_DATAFILE with ORA-01119 errors)
4. Short-term + long-term memory combined in one prompt
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Fix Windows console encoding for Unicode characters (→, —)
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentri.core.models import AuditRecord, Workflow
from sentri.db.connection import Database
from sentri.db.audit_repo import AuditRepository
from sentri.db.workflow_repo import WorkflowRepository
from sentri.memory.manager import MemoryManager
from sentri.policy.loader import PolicyLoader


def main():
    print("=" * 70)
    print("v3.3 Long-Term Memory - Manual E2E Test")
    print("=" * 70)

    # Setup: temp DB + repos (ignore_cleanup_errors for Windows file locking)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        db.initialize_schema()

        wf_repo = WorkflowRepository(db)
        audit_repo = AuditRepository(db)
        policies_path = Path(__file__).parent.parent / "src" / "sentri" / "_default_policies"
        policy_loader = PolicyLoader(policies_path)

        base = datetime.now(timezone.utc)

        # -----------------------------------------------------------------
        # 1. Insert biweekly tablespace_full pattern (every ~14 days)
        # -----------------------------------------------------------------
        print("\n[1] Inserting tablespace_full events (every ~14 days)...")
        for i in range(6):
            wf = Workflow(
                alert_type="tablespace_full",
                database_id="PROD-DB-07",
                environment="PROD",
                status="COMPLETED",
            )
            wf_id = wf_repo.create(wf)
            # Backdating: 0, 14, 28, 42, 56, 70 days ago
            created = (base - timedelta(days=i * 14)).isoformat()
            db.execute_write(
                "UPDATE workflows SET created_at = ? WHERE id = ?",
                (created, wf_id),
            )
            day_name = (base - timedelta(days=i * 14)).strftime("%A")
            print(f"  - {created[:10]} ({day_name}) -> COMPLETED")

        # -----------------------------------------------------------------
        # 2. Insert Friday archive_dest_full pattern
        # -----------------------------------------------------------------
        print("\n[2] Inserting archive_dest_full events (every Friday)...")
        # Find the most recent Friday
        days_since_friday = (base.weekday() - 4) % 7
        last_friday = base - timedelta(days=days_since_friday)

        for i in range(5):
            friday = last_friday - timedelta(weeks=i)
            wf = Workflow(
                alert_type="archive_dest_full",
                database_id="PROD-DB-07",
                environment="PROD",
                status="COMPLETED",
            )
            wf_id = wf_repo.create(wf)
            created = friday.isoformat()
            db.execute_write(
                "UPDATE workflows SET created_at = ? WHERE id = ?",
                (created, wf_id),
            )
            print(f"  - {created[:10]} ({friday.strftime('%A')}) -> COMPLETED")

        # -----------------------------------------------------------------
        # 3. Insert a failed workflow (tablespace_full, 3 days ago)
        # -----------------------------------------------------------------
        print("\n[3] Inserting a FAILED tablespace_full (3 days ago)...")
        wf_fail = Workflow(
            alert_type="tablespace_full",
            database_id="PROD-DB-07",
            environment="PROD",
            status="FAILED",
        )
        wf_fail_id = wf_repo.create(wf_fail)
        failed_date = (base - timedelta(days=3)).isoformat()
        db.execute_write(
            "UPDATE workflows SET created_at = ? WHERE id = ?",
            (failed_date, wf_fail_id),
        )
        print(f"  - {failed_date[:10]} -> FAILED")

        # -----------------------------------------------------------------
        # 4. Insert audit records with failures (RESIZE_DATAFILE)
        # -----------------------------------------------------------------
        print("\n[4] Inserting audit records (RESIZE_DATAFILE: 3 success, 2 failed)...")
        for i in range(3):
            audit_repo.create(AuditRecord(
                workflow_id=wf_fail_id,
                action_type="RESIZE_DATAFILE",
                action_sql="ALTER DATABASE DATAFILE '/u01/oradata/PRODDB/users01.dbf' RESIZE 50G",
                database_id="PROD-DB-07",
                environment="PROD",
                executed_by="agent4",
                result="SUCCESS",
            ))
        for i in range(2):
            audit_repo.create(AuditRecord(
                workflow_id=wf_fail_id,
                action_type="RESIZE_DATAFILE",
                action_sql="ALTER DATABASE DATAFILE '/u01/oradata/PRODDB/users01.dbf' RESIZE 50G",
                database_id="PROD-DB-07",
                environment="PROD",
                executed_by="agent4",
                result="FAILED",
                error_message="ORA-01119: error in creating database file '/u01/oradata/PRODDB/users01.dbf'",
            ))
        print("  - 3x SUCCESS, 2x FAILED (ORA-01119)")

        # -----------------------------------------------------------------
        # 5. Insert a recent action (short-term, 2 hours ago)
        # -----------------------------------------------------------------
        print("\n[5] Inserting recent action (ADD_DATAFILE, 2h ago)...")
        recent_wf = Workflow(
            alert_type="tablespace_full",
            database_id="PROD-DB-07",
            environment="PROD",
            status="COMPLETED",
        )
        recent_id = wf_repo.create(recent_wf)
        audit_repo.create(AuditRecord(
            workflow_id=recent_id,
            action_type="ADD_DATAFILE",
            action_sql="ALTER TABLESPACE USERS ADD DATAFILE SIZE 10G",
            database_id="PROD-DB-07",
            environment="PROD",
            executed_by="agent4",
            result="SUCCESS",
        ))
        print("  - ADD_DATAFILE -> SUCCESS")

        # -----------------------------------------------------------------
        # 6. Build memory context and format prompt
        # -----------------------------------------------------------------
        print("\n" + "=" * 70)
        print("BUILDING MEMORY CONTEXT...")
        print("=" * 70)

        mm = MemoryManager(db, policy_loader)
        ctx = mm.get_context("PROD-DB-07", "tablespace_full", environment="PROD")

        print(f"\nMemory Context Summary:")
        print(f"  database_id:      {ctx.database_id}")
        print(f"  alert_type:       {ctx.alert_type}")
        print(f"  recent_actions:   {len(ctx.recent_actions)}")
        print(f"  recent_outcomes:  {len(ctx.recent_outcomes)}")
        print(f"  failed_approaches: {len(ctx.failed_approaches)}")
        print(f"  alert_history:    {len(ctx.alert_history)}")
        print(f"  failure_stats:    {len(ctx.failure_stats)}")
        print(f"  has_memory:       {ctx.has_memory}")

        # -----------------------------------------------------------------
        # 7. Show the formatted prompt (what the LLM sees)
        # -----------------------------------------------------------------
        print("\n" + "=" * 70)
        print("FORMATTED PROMPT (what the LLM researcher receives):")
        print("=" * 70)

        prompt_text = mm.format_for_prompt(ctx)
        print(prompt_text)

        # -----------------------------------------------------------------
        # 8. Assertions
        # -----------------------------------------------------------------
        print("\n" + "=" * 70)
        print("ASSERTIONS")
        print("=" * 70)

        errors = []

        # Alert history should have all events (tablespace + archive + failed)
        if len(ctx.alert_history) < 10:
            errors.append(f"Expected 10+ history events, got {len(ctx.alert_history)}")

        # Should see both alert types in history
        alert_types = {h.alert_type for h in ctx.alert_history}
        if "tablespace_full" not in alert_types:
            errors.append("tablespace_full not in alert_history")
        if "archive_dest_full" not in alert_types:
            errors.append("archive_dest_full not in alert_history")

        # archive_dest_full should all be Fridays
        archive_events = [h for h in ctx.alert_history if h.alert_type == "archive_dest_full"]
        friday_count = sum(1 for h in archive_events if h.day_name == "Friday")
        if friday_count != len(archive_events):
            errors.append(f"Expected all archive events on Friday, got {friday_count}/{len(archive_events)}")

        # Failure stats should include RESIZE_DATAFILE
        if len(ctx.failure_stats) < 1:
            errors.append("No failure stats found")
        else:
            resize_stat = next((s for s in ctx.failure_stats if s.action_type == "RESIZE_DATAFILE"), None)
            if not resize_stat:
                errors.append("RESIZE_DATAFILE not in failure_stats")
            elif resize_stat.failures != 2:
                errors.append(f"Expected 2 failures, got {resize_stat.failures}")

        # Prompt should contain key sections
        if "Historical Alert Patterns" not in prompt_text:
            errors.append("Missing 'Historical Alert Patterns' section in prompt")
        if "Historical Failure Stats" not in prompt_text:
            errors.append("Missing 'Historical Failure Stats' section in prompt")
        if "Memory Rules" not in prompt_text:
            errors.append("Missing 'Memory Rules' section in prompt")

        # Day names should appear
        if "(Fri)" not in prompt_text:
            errors.append("Missing '(Fri)' day abbreviation in prompt")

        # Failure stats text
        if "RESIZE_DATAFILE" not in prompt_text:
            errors.append("Missing 'RESIZE_DATAFILE' in failure stats")

        if errors:
            print("\nFAILED:")
            for e in errors:
                print(f"  X {e}")
            db.close()
            sys.exit(1)
        else:
            print("\nALL ASSERTIONS PASSED!")
            print(f"  - {len(ctx.alert_history)} history events loaded")
            print(f"  - {len(archive_events)} archive events, all on Fridays")
            print(f"  - {len(ctx.failure_stats)} failure stat entries")
            print(f"  - Prompt is {len(prompt_text)} chars")
            print(f"  - Short-term + long-term memory combined successfully")

        # Close DB before TemporaryDirectory cleanup (Windows lock issue)
        db.close()
        print("\nDone!")


if __name__ == "__main__":
    main()
