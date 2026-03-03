"""Tests for the WebFetcher — Oracle docs fetching, HTML extraction, and caching."""

import time
from unittest.mock import MagicMock, patch

from sentri.rag.manager import SyntaxDoc
from sentri.rag.retriever import (
    _ORACLE_DOC_PAGES,
    _ORACLE_DOCS_BASE,
    WebFetcher,
    extract_oracle_html,
    get_oracle_doc_url,
)

# ---------------------------------------------------------------------------
# Oracle doc URL mapping
# ---------------------------------------------------------------------------


class TestOracleDocUrlMapping:
    def test_alter_tablespace_19c(self):
        url = get_oracle_doc_url("alter_tablespace", "19c")
        assert (
            url
            == "https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/ALTER-TABLESPACE.html"
        )

    def test_alter_tablespace_23ai(self):
        url = get_oracle_doc_url("alter_tablespace", "23ai")
        assert url is not None
        assert "/23/" in url
        assert "ALTER-TABLESPACE" in url

    def test_alter_session_21c(self):
        url = get_oracle_doc_url("alter_session", "21c")
        assert url is not None
        assert "/21/" in url
        assert "ALTER-SESSION" in url

    def test_rman_archivelog_19c(self):
        """Admin guide page, not SQL reference."""
        url = get_oracle_doc_url("rman_archivelog", "19c")
        assert url is not None
        assert "/admin/" in url
        assert "managing-archived-redo-log-files" in url

    def test_undo_management_19c(self):
        url = get_oracle_doc_url("undo_management", "19c")
        assert url is not None
        assert "managing-undo" in url

    def test_unknown_operation(self):
        url = get_oracle_doc_url("nonexistent_op", "19c")
        assert url is None

    def test_unknown_version(self):
        url = get_oracle_doc_url("alter_tablespace", "99z")
        assert url is None

    def test_all_versions_have_sql_base(self):
        """Every known version should have a SQL reference base URL."""
        for version in ["12c", "19c", "21c", "23ai"]:
            assert version in _ORACLE_DOCS_BASE

    def test_all_sql_pages_mapped(self):
        """Core SQL operations should be mapped."""
        expected = ["alter_tablespace", "alter_session", "alter_system", "alter_database"]
        for op in expected:
            assert op in _ORACLE_DOC_PAGES, f"{op} not mapped"


# ---------------------------------------------------------------------------
# HTML → Markdown extraction
# ---------------------------------------------------------------------------


class TestHTMLExtraction:
    def test_heading_extraction(self):
        html = "<h1>ALTER TABLESPACE</h1><h2>Syntax</h2>"
        md = extract_oracle_html(html)
        assert "# ALTER TABLESPACE" in md
        assert "## Syntax" in md

    def test_pre_block_extraction(self):
        html = "<pre>ALTER TABLESPACE users ADD DATAFILE SIZE 10G;</pre>"
        md = extract_oracle_html(html)
        assert "```sql" in md
        assert "ALTER TABLESPACE users ADD DATAFILE SIZE 10G;" in md
        assert "```" in md

    def test_paragraph_extraction(self):
        html = "<p>This is a description of the command.</p>"
        md = extract_oracle_html(html)
        assert "This is a description of the command." in md

    def test_list_extraction(self):
        html = "<ul><li>First item</li><li>Second item</li></ul>"
        md = extract_oracle_html(html)
        assert "- First item" in md
        assert "- Second item" in md

    def test_code_inline(self):
        html = "<p>Use the <code>RESIZE</code> command.</p>"
        md = extract_oracle_html(html)
        assert "`RESIZE`" in md

    def test_script_stripped(self):
        html = "<script>var x = 1;</script><p>Visible text</p>"
        md = extract_oracle_html(html)
        assert "var x" not in md
        assert "Visible text" in md

    def test_style_stripped(self):
        html = "<style>.foo { color: red; }</style><p>Content here</p>"
        md = extract_oracle_html(html)
        assert "color" not in md
        assert "Content here" in md

    def test_nav_stripped(self):
        html = "<nav><a href='/'>Home</a></nav><p>Main content</p>"
        md = extract_oracle_html(html)
        assert "Main content" in md
        # Nav content should be stripped
        assert "Home" not in md

    def test_empty_html(self):
        assert extract_oracle_html("") == ""

    def test_no_html_tags(self):
        """Plain text should be returned as-is (mostly)."""
        md = extract_oracle_html("Just plain text")
        # Plain text outside tags may or may not be captured; that's OK
        # The important thing is no crash
        assert isinstance(md, str)

    def test_multiple_pre_blocks(self):
        html = (
            "<h2>Example 1</h2><pre>SELECT 1 FROM dual;</pre>"
            "<h2>Example 2</h2><pre>SELECT 2 FROM dual;</pre>"
        )
        md = extract_oracle_html(html)
        assert "SELECT 1 FROM dual;" in md
        assert "SELECT 2 FROM dual;" in md
        assert md.count("```sql") == 2

    def test_nested_tags_in_pre(self):
        """Pre blocks in Oracle docs sometimes contain spans."""
        html = "<pre><span>ALTER</span> <span>TABLESPACE</span> test;</pre>"
        md = extract_oracle_html(html)
        assert "ALTER" in md
        assert "TABLESPACE" in md

    def test_excessive_whitespace_cleaned(self):
        html = "<p>One</p>\n\n\n\n\n<p>Two</p>"
        md = extract_oracle_html(html)
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in md


# ---------------------------------------------------------------------------
# WebFetcher — caching
# ---------------------------------------------------------------------------


class TestWebFetcherCache:
    def test_write_and_read_cache(self, tmp_path):
        fetcher = WebFetcher(tmp_path, cache_hours=24)
        url = "https://example.com/test"

        # Write to cache
        fetcher._write_cache(url, "# Cached content")

        # Read it back
        cached = fetcher._read_cache(url)
        assert cached == "# Cached content"

    def test_cache_miss(self, tmp_path):
        fetcher = WebFetcher(tmp_path, cache_hours=24)
        cached = fetcher._read_cache("https://example.com/nonexistent")
        assert cached is None

    def test_cache_expiry(self, tmp_path):
        fetcher = WebFetcher(tmp_path, cache_hours=0)  # 0 hours TTL = always expired

        url = "https://example.com/test"
        fetcher._write_cache(url, "# Old content")

        # Manually set timestamp to 2 hours ago
        key = fetcher._cache_key(url)
        ts_file = tmp_path / ".cache" / f"{key}.ts"
        ts_file.write_text(str(time.time() - 7200), encoding="utf-8")

        cached = fetcher._read_cache(url)
        assert cached is None  # Expired

    def test_cache_key_deterministic(self, tmp_path):
        fetcher = WebFetcher(tmp_path)
        k1 = fetcher._cache_key("https://example.com/foo")
        k2 = fetcher._cache_key("https://example.com/foo")
        assert k1 == k2

    def test_cache_key_different_urls(self, tmp_path):
        fetcher = WebFetcher(tmp_path)
        k1 = fetcher._cache_key("https://example.com/foo")
        k2 = fetcher._cache_key("https://example.com/bar")
        assert k1 != k2

    def test_clear_cache(self, tmp_path):
        fetcher = WebFetcher(tmp_path, cache_hours=24)

        # Write two cached items
        fetcher._write_cache("https://example.com/a", "Content A")
        fetcher._write_cache("https://example.com/b", "Content B")

        count = fetcher.clear_cache()
        assert count == 4  # 2 .md + 2 .ts files

        # Verify cache is empty
        assert fetcher._read_cache("https://example.com/a") is None
        assert fetcher._read_cache("https://example.com/b") is None

    def test_clear_cache_empty_dir(self, tmp_path):
        fetcher = WebFetcher(tmp_path)
        count = fetcher.clear_cache()
        assert count == 0

    def test_cache_creates_dir(self, tmp_path):
        fetcher = WebFetcher(tmp_path)
        cache_dir = tmp_path / ".cache"
        assert not cache_dir.exists()

        fetcher._write_cache("https://example.com/test", "Content")
        assert cache_dir.exists()


# ---------------------------------------------------------------------------
# WebFetcher — fetch with mocked HTTP
# ---------------------------------------------------------------------------


class TestWebFetcherFetch:
    def _mock_html_response(self, html_content: str):
        """Create a mock urlopen response."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = html_content.encode("utf-8")
        mock_resp.headers = MagicMock()
        mock_resp.headers.get_content_charset.return_value = "utf-8"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_fetch_success(self, mock_urlopen, tmp_path):
        html = "<h1>ALTER TABLESPACE</h1><pre>ALTER TABLESPACE users RESIZE 50G;</pre>"
        mock_urlopen.return_value = self._mock_html_response(html)

        fetcher = WebFetcher(tmp_path, cache_hours=24)
        result = fetcher.fetch("https://docs.oracle.com/test")

        assert result is not None
        assert "ALTER TABLESPACE" in result
        assert "RESIZE 50G" in result
        mock_urlopen.assert_called_once()

    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_fetch_caches_result(self, mock_urlopen, tmp_path):
        html = "<p>Test content</p>"
        mock_urlopen.return_value = self._mock_html_response(html)

        fetcher = WebFetcher(tmp_path, cache_hours=24)
        url = "https://docs.oracle.com/test"

        # First fetch — hits web
        result1 = fetcher.fetch(url)
        assert mock_urlopen.call_count == 1

        # Second fetch — hits cache, no web call
        result2 = fetcher.fetch(url)
        assert mock_urlopen.call_count == 1  # Still 1, not 2
        assert result1 == result2

    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_fetch_http_error(self, mock_urlopen, tmp_path):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None
        )

        fetcher = WebFetcher(tmp_path)
        result = fetcher.fetch("https://example.com/nonexistent")
        assert result is None

    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_fetch_url_error(self, mock_urlopen, tmp_path):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        fetcher = WebFetcher(tmp_path)
        result = fetcher.fetch("https://example.com/down")
        assert result is None

    def test_fetch_empty_url(self, tmp_path):
        fetcher = WebFetcher(tmp_path)
        assert fetcher.fetch("") is None
        assert fetcher.fetch(None) is None

    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_fetch_empty_html(self, mock_urlopen, tmp_path):
        """Empty HTML → no content → returns None."""
        mock_urlopen.return_value = self._mock_html_response("")

        fetcher = WebFetcher(tmp_path)
        result = fetcher.fetch("https://example.com/empty")
        assert result is None

    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_fetch_for_operation(self, mock_urlopen, tmp_path):
        html = "<h2>ALTER TABLESPACE</h2><pre>RESIZE syntax here</pre>"
        mock_urlopen.return_value = self._mock_html_response(html)

        fetcher = WebFetcher(tmp_path)
        result = fetcher.fetch_for_operation("alter_tablespace", "19c")

        assert result is not None
        assert "RESIZE syntax here" in result

    def test_fetch_for_unknown_operation(self, tmp_path):
        fetcher = WebFetcher(tmp_path)
        result = fetcher.fetch_for_operation("nonexistent_op", "19c")
        assert result is None


# ---------------------------------------------------------------------------
# WebFetcher — enrich_doc
# ---------------------------------------------------------------------------


class TestWebFetcherEnrich:
    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_enrich_doc_with_web_source(self, mock_urlopen, tmp_path):
        """Doc with web_source in frontmatter → fetches that URL."""
        html = "<h2>Official Docs</h2><pre>ALTER TABLESPACE syntax</pre>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html.encode("utf-8")
        mock_resp.headers = MagicMock()
        mock_resp.headers.get_content_charset.return_value = "utf-8"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        doc = SyntaxDoc(
            path="19c/tablespace/alter_tablespace.md",
            version="19c",
            topic="tablespace",
            operation="alter_tablespace",
            content="# Local content here",
            keywords=["tablespace"],
            web_source="https://docs.oracle.com/custom-url",
        )

        fetcher = WebFetcher(tmp_path, cache_hours=24)
        enriched = fetcher.enrich_doc(doc)

        assert "Local content here" in enriched.content
        assert "Additional Reference (docs.oracle.com)" in enriched.content
        assert "ALTER TABLESPACE syntax" in enriched.content
        # Metadata preserved
        assert enriched.version == "19c"
        assert enriched.operation == "alter_tablespace"

    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_enrich_doc_auto_mapped(self, mock_urlopen, tmp_path):
        """Doc without web_source → falls back to auto-mapped URL."""
        html = "<pre>Auto-mapped content</pre>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html.encode("utf-8")
        mock_resp.headers = MagicMock()
        mock_resp.headers.get_content_charset.return_value = "utf-8"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        doc = SyntaxDoc(
            path="19c/tablespace/alter_tablespace.md",
            version="19c",
            topic="tablespace",
            operation="alter_tablespace",
            content="# Local only",
        )

        fetcher = WebFetcher(tmp_path, cache_hours=24)
        enriched = fetcher.enrich_doc(doc)

        assert "Local only" in enriched.content
        assert "Auto-mapped content" in enriched.content

    def test_enrich_doc_no_mapping(self, tmp_path):
        """Unknown operation with no web_source → returns original doc."""
        doc = SyntaxDoc(
            path="19c/custom/my_doc.md",
            version="19c",
            topic="custom",
            operation="my_custom_op",
            content="# Custom content",
        )

        fetcher = WebFetcher(tmp_path)
        enriched = fetcher.enrich_doc(doc)

        # Should be the exact same doc (no enrichment)
        assert enriched.content == "# Custom content"

    @patch("sentri.rag.retriever.urllib.request.urlopen")
    def test_enrich_prefers_web_source_over_auto(self, mock_urlopen, tmp_path):
        """When web_source is set, it should be used instead of auto-mapping."""
        call_urls = []

        def mock_open(req, **kwargs):
            call_urls.append(req.full_url)
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"<pre>Content</pre>"
            mock_resp.headers = MagicMock()
            mock_resp.headers.get_content_charset.return_value = "utf-8"
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        mock_urlopen.side_effect = mock_open

        doc = SyntaxDoc(
            path="19c/tablespace/alter_tablespace.md",
            version="19c",
            topic="tablespace",
            operation="alter_tablespace",
            content="# Local",
            web_source="https://custom.oracle.com/my-page",
        )

        fetcher = WebFetcher(tmp_path, cache_hours=24)
        fetcher.enrich_doc(doc)

        # Should have fetched the web_source URL, not the auto-mapped one
        assert len(call_urls) == 1
        assert call_urls[0] == "https://custom.oracle.com/my-page"


# ---------------------------------------------------------------------------
# WebFetcher — web_source in frontmatter
# ---------------------------------------------------------------------------


class TestWebSourceFrontmatter:
    def test_web_source_loaded_from_doc(self, tmp_path):
        """web_source field in frontmatter is passed to SyntaxDoc."""
        from sentri.rag.retriever import KeywordRetriever

        doc_dir = tmp_path / "19c" / "tablespace"
        doc_dir.mkdir(parents=True)
        (doc_dir / "alter_tablespace.md").write_text(
            "---\nversion: 19c\ntopic: tablespace\n"
            "operation: alter_tablespace\n"
            'web_source: "https://docs.oracle.com/test"\n'
            "---\n\n# Content",
            encoding="utf-8",
        )

        retriever = KeywordRetriever(tmp_path)
        docs = retriever.get_syntax_docs("tablespace_full", "19c")
        assert len(docs) == 1
        assert docs[0].web_source == "https://docs.oracle.com/test"

    def test_no_web_source_defaults_empty(self, tmp_path):
        """Doc without web_source field → empty string."""
        from sentri.rag.retriever import KeywordRetriever

        doc_dir = tmp_path / "19c" / "tablespace"
        doc_dir.mkdir(parents=True)
        (doc_dir / "alter_tablespace.md").write_text(
            "---\nversion: 19c\ntopic: tablespace\noperation: alter_tablespace\n---\n\n# Content",
            encoding="utf-8",
        )

        retriever = KeywordRetriever(tmp_path)
        docs = retriever.get_syntax_docs("tablespace_full", "19c")
        assert len(docs) == 1
        assert docs[0].web_source == ""
