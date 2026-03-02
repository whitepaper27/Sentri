"""End-to-end test: 12c tablespace_full alert -> RAG retriever -> web enrichment.

This tests the FULL pipeline:
1. KeywordRetriever finds 12c/tablespace/alter_tablespace.md
2. WebFetcher enriches with content from docs.oracle.com
3. Verify local + web content both present
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sentri.rag.retriever import KeywordRetriever, WebFetcher

DOCS_PATH = Path(__file__).resolve().parent.parent / "docs" / "oracle"


def main():
    print("=" * 70)
    print("  E2E Test: 12c tablespace_full -> RAG -> Web Enrichment")
    print("=" * 70)

    # ---------------------------------------------------------------
    # 1. KeywordRetriever loads the 12c local doc
    # ---------------------------------------------------------------
    print("\n[1] KeywordRetriever -- loading syntax docs for tablespace_full @ 12c")
    retriever = KeywordRetriever(DOCS_PATH)
    docs = retriever.get_syntax_docs("tablespace_full", "12c")

    print(f"    Docs found: {len(docs)}")
    for d in docs:
        print(f"    - {d.path} (version={d.version}, {len(d.content)} chars)")
        print(f"      web_source: {d.web_source or '(none)'}")
        print(f"      keywords: {d.keywords}")

    if not docs:
        print("    ERROR: No docs found!")
        return

    doc = docs[0]
    assert doc.version == "12c", f"Expected 12c, got {doc.version}"
    assert "BIGFILE" in doc.content, "Local doc should mention BIGFILE"
    assert "12c" in doc.content, "Local doc should mention 12c"
    print(f"    OK: Local doc loaded correctly")

    # ---------------------------------------------------------------
    # 2. WebFetcher enriches the doc
    # ---------------------------------------------------------------
    print(f"\n[2] WebFetcher -- enriching doc with content from docs.oracle.com")
    fetcher = WebFetcher(DOCS_PATH, cache_hours=24)
    enriched = fetcher.enrich_doc(doc)

    print(f"    Original: {len(doc.content)} chars")
    print(f"    Enriched: {len(enriched.content)} chars")
    print(f"    Growth:   {len(enriched.content) - len(doc.content):,} chars added")

    assert len(enriched.content) > len(doc.content), "Enriched should be larger"
    assert "Additional Reference" in enriched.content, "Should have web supplement marker"
    assert "## Purpose" in enriched.content, "Web content should have Purpose heading"
    assert "## Semantics" in enriched.content, "Web content should have Semantics heading"
    print(f"    OK: Web enrichment working")

    # ---------------------------------------------------------------
    # 3. Verify local content preserved
    # ---------------------------------------------------------------
    print(f"\n[3] Verify local content is preserved in enriched doc")
    assert "BIGFILE tablespaces have exactly ONE datafile" in enriched.content
    assert "12c-Specific Notes" in enriched.content
    assert "ORA-32771" in enriched.content
    print(f"    OK: Local BIGFILE warning preserved")
    print(f"    OK: 12c-specific notes preserved")

    # ---------------------------------------------------------------
    # 4. Verify web content has SQL
    # ---------------------------------------------------------------
    print(f"\n[4] Check SQL blocks in enriched content")
    sql_blocks = enriched.content.count("```sql")
    print(f"    SQL code blocks: {sql_blocks}")
    assert sql_blocks >= 6, f"Expected at least 6 SQL blocks, got {sql_blocks}"
    print(f"    OK: SQL blocks present from both local and web")

    # ---------------------------------------------------------------
    # 5. Verify version fallback works (21c -> falls back to 19c)
    # ---------------------------------------------------------------
    print(f"\n[5] Version fallback: request 21c (no 21c folder) -> falls to 19c")
    docs_21c = retriever.get_syntax_docs("tablespace_full", "21c")
    if docs_21c:
        print(f"    Got: {docs_21c[0].path} (version={docs_21c[0].version})")
        # Should fall back to 19c or 12c (whatever exists)
        assert docs_21c[0].version in ("19c", "12c"), f"Should fallback, got {docs_21c[0].version}"
        print(f"    OK: Fallback working -- got {docs_21c[0].version} docs")
    else:
        print(f"    No docs found (expected -- need 19c or 12c)")

    # ---------------------------------------------------------------
    # 6. Rules still load correctly
    # ---------------------------------------------------------------
    print(f"\n[6] Rule docs for tablespace_full")
    rules = retriever.get_rule_docs("tablespace_full")
    print(f"    Rules found: {len(rules)}")
    for r in rules:
        print(f"    - {r.rule_id} (severity={r.severity})")
    if rules:
        bigfile_rule = [r for r in rules if "bigfile" in r.rule_id]
        if bigfile_rule:
            print(f"    OK: BIGFILE rule loaded: {bigfile_rule[0].rule_id}")

    # ---------------------------------------------------------------
    # 7. Show a preview of what the LLM would see
    # ---------------------------------------------------------------
    print(f"\n[7] Preview: first 500 chars of what LLM would see (enriched doc)")
    print("-" * 70)
    print(enriched.content[:500])
    print("-" * 70)

    print(f"\n{'=' * 70}")
    print(f"  ALL CHECKS PASSED")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
