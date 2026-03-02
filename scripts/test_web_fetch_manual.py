"""Manual test: fetch Oracle 12c ALTER TABLESPACE from docs.oracle.com.

Run:
    python scripts/test_web_fetch_manual.py
"""

import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sentri.rag.retriever import (
    WebFetcher,
    get_oracle_doc_url,
    extract_oracle_html,
)

# Use a temp directory for cache
CACHE_DIR = Path(__file__).resolve().parent.parent / "docs" / "oracle"


def main():
    print("=" * 70)
    print("  Manual WebFetcher Test — Oracle 12c ALTER TABLESPACE")
    print("=" * 70)

    # ---------------------------------------------------------------
    # 1. Show the URL that would be used
    # ---------------------------------------------------------------
    url = get_oracle_doc_url("alter_tablespace", "12c")
    print(f"\n[1] URL mapping for alter_tablespace @ 12c:")
    print(f"    {url}")

    if not url:
        print("    ERROR: No URL mapping found!")
        return

    # Also show other version URLs for comparison
    for ver in ("19c", "21c", "23ai"):
        u = get_oracle_doc_url("alter_tablespace", ver)
        print(f"    {ver}: {u}")

    # ---------------------------------------------------------------
    # 2. Fetch the page
    # ---------------------------------------------------------------
    print(f"\n[2] Fetching from docs.oracle.com ...")
    fetcher = WebFetcher(CACHE_DIR, cache_hours=24)

    start = time.time()
    content = fetcher.fetch(url)
    elapsed = time.time() - start

    if content is None:
        print(f"    FAILED to fetch (took {elapsed:.1f}s)")
        print("    This could be a network issue or Oracle blocking the request.")
        return

    print(f"    SUCCESS — fetched {len(content)} chars in {elapsed:.1f}s")

    # ---------------------------------------------------------------
    # 3. Show first 2000 chars of extracted markdown
    # ---------------------------------------------------------------
    print(f"\n[3] Extracted markdown (first 2000 chars):")
    print("-" * 70)
    preview = content[:2000]
    print(preview)
    if len(content) > 2000:
        print(f"\n... ({len(content) - 2000} more chars)")
    print("-" * 70)

    # ---------------------------------------------------------------
    # 4. Show what SQL blocks were found
    # ---------------------------------------------------------------
    sql_blocks = content.count("```sql")
    headings = [line for line in content.splitlines() if line.startswith("#")]
    print(f"\n[4] Content analysis:")
    print(f"    Total chars: {len(content)}")
    print(f"    SQL blocks:  {sql_blocks}")
    print(f"    Headings:    {len(headings)}")
    if headings:
        print(f"    First 10 headings:")
        for h in headings[:10]:
            print(f"      {h}")

    # ---------------------------------------------------------------
    # 5. Test cache — second fetch should be instant
    # ---------------------------------------------------------------
    print(f"\n[5] Testing cache (second fetch)...")
    start2 = time.time()
    content2 = fetcher.fetch(url)
    elapsed2 = time.time() - start2

    print(f"    Cached fetch: {len(content2)} chars in {elapsed2:.4f}s")
    print(f"    Speedup: {elapsed / max(elapsed2, 0.0001):.0f}x faster")

    cache_dir = CACHE_DIR / ".cache"
    if cache_dir.exists():
        cached_files = list(cache_dir.glob("*"))
        print(f"    Cache files: {len(cached_files)}")
        for f in cached_files:
            print(f"      {f.name} ({f.stat().st_size} bytes)")

    # ---------------------------------------------------------------
    # 6. Also test 19c for comparison
    # ---------------------------------------------------------------
    print(f"\n[6] Fetching 19c ALTER TABLESPACE for comparison...")
    url_19c = get_oracle_doc_url("alter_tablespace", "19c")
    content_19c = fetcher.fetch(url_19c)
    if content_19c:
        print(f"    19c: {len(content_19c)} chars")
        sql_19c = content_19c.count("```sql")
        print(f"    19c SQL blocks: {sql_19c}")
    else:
        print("    19c: FAILED to fetch")

    # ---------------------------------------------------------------
    # 7. Test enrichment — create a minimal 12c doc and enrich it
    # ---------------------------------------------------------------
    print(f"\n[7] Testing doc enrichment (simulated 12c local doc)...")
    from sentri.rag.manager import SyntaxDoc

    local_doc = SyntaxDoc(
        path="12c/tablespace/alter_tablespace.md",
        version="12c",
        topic="tablespace",
        operation="alter_tablespace",
        content="# ALTER TABLESPACE — Oracle 12c\n\n(Local content placeholder)",
        keywords=["tablespace", "alter tablespace"],
        web_source="",  # No explicit URL — will auto-map
    )

    enriched = fetcher.enrich_doc(local_doc)
    print(f"    Original: {len(local_doc.content)} chars")
    print(f"    Enriched: {len(enriched.content)} chars")
    has_ref = "Additional Reference" in enriched.content
    print(f"    Has web supplement: {has_ref}")

    print(f"\n{'=' * 70}")
    print(f"  DONE — all tests completed!")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
