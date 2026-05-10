#!/usr/bin/env python3
"""CLI entry point for Sentri evaluation suite.

Usage:
    python eval/run_eval.py                    # Run everything
    python eval/run_eval.py --part remediation # 24 runs only
    python eval/run_eval.py --part adversarial # 10 adversarial only
    python eval/run_eval.py --part format      # Format from saved JSON
    python eval/run_eval.py --seed-only        # Just run seed SQL
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy loggers
    logging.getLogger("sentri.db").setLevel(logging.WARNING)
    logging.getLogger("sentri.policy").setLevel(logging.WARNING)
    logging.getLogger("sentri.rag").setLevel(logging.WARNING)


def run_seed():
    """Run seed SQL against Docker Oracle."""
    import oracledb

    from sentri.config.settings import Settings

    settings = Settings.load()
    db_cfg = settings.databases[0]

    conn_str = db_cfg.connection_string
    if conn_str.startswith("oracle://"):
        conn_str = conn_str[len("oracle://"):]
    if "@" in conn_str:
        user_part, dsn = conn_str.split("@", 1)
    else:
        user_part = db_cfg.username or "system"
        dsn = conn_str

    user = db_cfg.username or user_part
    password = db_cfg.password

    print(f"Connecting to {dsn} as {user}...")
    conn = oracledb.connect(user=user, password=password, dsn=dsn)

    seed_path = Path(__file__).parent / "seed_oracle.sql"
    seed_sql = seed_path.read_text()

    # Split by '/' delimiter (PL/SQL blocks) and ';' for simple statements
    cursor = conn.cursor()

    # Execute each PL/SQL block separately
    blocks = seed_sql.split("\n/\n")
    for block in blocks:
        block = block.strip()
        if not block or block.startswith("--"):
            continue

        # Split non-PL/SQL statements by semicolon
        if block.startswith("BEGIN") or block.startswith("DECLARE"):
            try:
                cursor.execute(block)
                print(f"  OK: {block[:60]}...")
            except Exception as e:
                print(f"  WARN: {e}")
        else:
            for stmt in block.split(";"):
                stmt = stmt.strip()
                if not stmt or stmt.startswith("--"):
                    continue
                try:
                    cursor.execute(stmt)
                    # Print SELECT results
                    if stmt.upper().startswith("SELECT"):
                        for row in cursor:
                            print(f"  {row}")
                    else:
                        print(f"  OK: {stmt[:60]}...")
                except Exception as e:
                    print(f"  WARN: {e}")

    conn.commit()
    conn.close()
    print("\nSeed complete.")


def main():
    parser = argparse.ArgumentParser(description="Sentri Evaluation Suite")
    parser.add_argument(
        "--part",
        choices=["remediation", "adversarial", "format", "all"],
        default="all",
        help="Which part to run (default: all)",
    )
    parser.add_argument("--seed-only", action="store_true", help="Just run seed SQL")
    parser.add_argument("--seed", action="store_true", help="Run seed SQL before evaluation")
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Run the full 80-case adversarial suite (IEEE Block 4) instead of the 10-case arXiv suite",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.seed_only:
        run_seed()
        return

    # Check Oracle connectivity
    from eval.harness import check_oracle_connection
    from sentri.config.settings import Settings

    settings = Settings.load()
    ok, msg = check_oracle_connection(settings)
    print(f"Oracle: {msg}")
    if not ok:
        print("ERROR: Cannot connect to Oracle. Is Docker running?")
        sys.exit(1)

    # Run seed if requested
    if args.seed:
        run_seed()

    results = None
    adv_results = None

    if args.part in ("remediation", "all"):
        from eval.runner import run_all

        results = run_all()

    if args.part in ("adversarial", "all"):
        from eval.adversarial import run_adversarial
        from eval.config import ADVERSARIAL_CASES, ADVERSARIAL_CASES_ALL

        cases = ADVERSARIAL_CASES_ALL if args.extended else ADVERSARIAL_CASES
        adv_results = run_adversarial(cases=cases)

    if args.part == "format":
        from eval.formatter import load_results_from_json

        results, adv_results = load_results_from_json()
        if not results and not adv_results:
            print("No saved results found. Run remediation/adversarial first.")
            sys.exit(1)

    # Format and save output
    from eval.formatter import save_formatted_output

    save_formatted_output(results, adv_results)

    # Print summary
    print(f"\n{'='*60}")
    print("  EVALUATION COMPLETE")
    print(f"{'='*60}")

    if results:
        total = len(results)
        success = sum(1 for r in results if r.outcome in ("COMPLETED", "NEEDS_APPROVAL"))
        print(f"  Remediation: {success}/{total} successful")

    if adv_results:
        passed = sum(1 for r in adv_results if r.passed)
        print(f"  Adversarial: {passed}/{len(adv_results)} passed")

    print(f"\n  Output files:")
    print(f"    eval/results/tables.md")
    print(f"    eval/results/paper_section.md")
    if results:
        print(f"    eval/results/raw_results.json")
    if adv_results:
        print(f"    eval/results/adversarial_results.json")


if __name__ == "__main__":
    main()
