"""Test WebFetcher across all Oracle versions — verify extraction quality."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sentri.rag.retriever import WebFetcher, get_oracle_doc_url

CACHE_DIR = Path(__file__).resolve().parent.parent / "docs" / "oracle"

# Clean cache first
import shutil
cache_dir = CACHE_DIR / ".cache"
if cache_dir.exists():
    shutil.rmtree(cache_dir)

fetcher = WebFetcher(CACHE_DIR, cache_hours=24)

VERSIONS = ["12c", "19c", "21c", "23ai"]
OPERATIONS = ["alter_tablespace", "alter_session", "alter_system"]

print("=" * 70)
print("  Full WebFetcher Test — All Versions × Operations")
print("=" * 70)

results = []

for op in OPERATIONS:
    for ver in VERSIONS:
        url = get_oracle_doc_url(op, ver)
        if not url:
            print(f"  {ver}/{op}: NO URL MAPPING")
            continue

        start = time.time()
        content = fetcher.fetch(url)
        elapsed = time.time() - start

        if content:
            headings = [l for l in content.splitlines() if l.startswith("#")]
            sql_blocks = content.count("```sql")
            results.append((ver, op, len(content), len(headings), sql_blocks, elapsed))
            print(f"  {ver}/{op}: {len(content):,} chars, {len(headings)} headings, {sql_blocks} SQL blocks ({elapsed:.1f}s)")
        else:
            print(f"  {ver}/{op}: FAILED ({elapsed:.1f}s)")

print(f"\n{'=' * 70}")
print(f"  Summary: {len(results)} pages fetched successfully")
print(f"{'=' * 70}")
print(f"  {'Version':<8} {'Operation':<20} {'Chars':>8} {'Headings':>10} {'SQL':>5}")
print(f"  {'-'*8} {'-'*20} {'-'*8} {'-'*10} {'-'*5}")
for ver, op, chars, heads, sql, _ in results:
    print(f"  {ver:<8} {op:<20} {chars:>8,} {heads:>10} {sql:>5}")

# Also test admin guide pages
print(f"\n--- Admin Guide pages ---")
ADMIN_OPS = ["rman_archivelog", "undo_management", "temp_tablespace"]
for op in ADMIN_OPS:
    for ver in ["12c", "19c", "23ai"]:
        url = get_oracle_doc_url(op, ver)
        if not url:
            continue
        content = fetcher.fetch(url)
        if content:
            headings = [l for l in content.splitlines() if l.startswith("#")]
            sql_blocks = content.count("```sql")
            print(f"  {ver}/{op}: {len(content):,} chars, {len(headings)} headings, {sql_blocks} SQL blocks")
        else:
            print(f"  {ver}/{op}: FAILED")
