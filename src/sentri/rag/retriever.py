"""Retrieval strategies for ground truth Oracle docs.

Three retrieval levels:
1. KeywordRetriever (default) — direct alert_type → doc file mapping
2. WebFetcher (optional) — fetch docs from URLs in frontmatter, cache locally
3. EmbeddingRetriever (optional) — semantic search via chromadb/FAISS
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from sentri.rag.manager import (
    KNOWN_VERSIONS,
    RuleDoc,
    SyntaxDoc,
)

logger = logging.getLogger("sentri.rag.retriever")


# ---------------------------------------------------------------------------
# Alert type → doc path mapping
# ---------------------------------------------------------------------------

# Maps each alert type to the doc files it needs (relative to version folder).
# Can be overridden per-alert via `## Ground Truth Docs` section in alert .md.
ALERT_DOC_MAP: dict[str, list[str]] = {
    "tablespace_full": ["tablespace/alter_tablespace.md"],
    "temp_full": ["tablespace/temp_tablespace.md"],
    "archive_dest_full": ["archive/rman_archivelog.md"],
    "session_blocker": ["performance/kill_session.md"],
    "cpu_high": ["performance/alter_session.md", "performance/sql_tuning.md"],
    "high_undo_usage": ["undo/undo_management.md"],
    "long_running_sql": ["performance/sql_tuning.md", "performance/alter_session.md"],
    "listener_down": [],  # OS-level, no SQL docs needed
    "archive_gap": ["archive/rman_archivelog.md", "standby/data_guard.md"],
}


# ---------------------------------------------------------------------------
# KeywordRetriever (default — zero external dependencies)
# ---------------------------------------------------------------------------


class KeywordRetriever:
    """Direct file mapping retriever: alert_type → version-specific docs."""

    def __init__(self, docs_path: Path):
        self._docs_path = docs_path

    def get_syntax_docs(self, alert_type: str, version: str) -> list[SyntaxDoc]:
        """Load syntax docs for an alert type at a specific version.

        Uses the ALERT_DOC_MAP to determine which files to load, then
        tries the exact version first, falling back through the chain.
        """
        doc_paths = ALERT_DOC_MAP.get(alert_type, [])
        if not doc_paths:
            logger.debug("No doc mapping for alert type: %s", alert_type)
            return []

        docs = []
        for rel_path in doc_paths:
            doc = self._load_syntax_doc(rel_path, version)
            if doc:
                docs.append(doc)

        return docs

    def get_rule_docs(self, alert_type: str) -> list[RuleDoc]:
        """Load all hard rules applicable to an alert type.

        Scans docs/oracle/rules/*.md, parses frontmatter, and returns
        only rules where applies_to includes the given alert_type.
        """
        rules_dir = self._docs_path / "rules"
        if not rules_dir.exists():
            return []

        rules = []
        for md_file in sorted(rules_dir.glob("*.md")):
            if md_file.name.lower() == "readme.md":
                continue
            rule = self._parse_rule_file(md_file)
            if rule and (not rule.applies_to or alert_type in rule.applies_to):
                rules.append(rule)

        return rules

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_syntax_doc(self, rel_path: str, version: str) -> Optional[SyntaxDoc]:
        """Try to load a doc at the given version, falling back through chain."""
        for v in self._fallback_chain(version):
            full_path = self._docs_path / v / rel_path
            if full_path.exists():
                try:
                    raw = full_path.read_text(encoding="utf-8")
                    frontmatter, body = _split_frontmatter(raw)

                    # Extract metadata from frontmatter
                    topic = frontmatter.get("topic", "")
                    operation = frontmatter.get(
                        "operation",
                        full_path.stem,  # filename without extension
                    )
                    keywords = frontmatter.get("keywords", [])
                    if isinstance(keywords, str):
                        keywords = [k.strip() for k in keywords.split(",")]

                    web_source = frontmatter.get("web_source", "")

                    doc = SyntaxDoc(
                        path=f"{v}/{rel_path}",
                        version=v,
                        topic=topic,
                        operation=operation,
                        content=body.strip(),
                        keywords=keywords,
                        web_source=web_source,
                    )
                    logger.debug("Loaded syntax doc: %s", doc.path)
                    return doc
                except Exception as e:
                    logger.warning("Failed to read %s: %s", full_path, e)
                    continue

        logger.debug("No syntax doc found for %s at version %s", rel_path, version)
        return None

    def _parse_rule_file(self, path: Path) -> Optional[RuleDoc]:
        """Parse a rules/*.md file into a RuleDoc."""
        try:
            raw = path.read_text(encoding="utf-8")
            frontmatter, body = _split_frontmatter(raw)

            rule_id = frontmatter.get("rule_id", path.stem)
            severity = frontmatter.get("severity", "HIGH")
            applies_to = frontmatter.get("applies_to", [])
            if isinstance(applies_to, str):
                applies_to = [a.strip() for a in applies_to.split(",")]

            # Extract detection pattern from ## Detection Pattern section
            detection_pattern = _extract_section_code(body, "Detection Pattern", "regex")

            # Extract condition from ## Condition section
            condition = _extract_section_text(body, "Condition")

            # Extract required action from ## Required Action section
            required_action = _extract_section_text(body, "Required Action")

            return RuleDoc(
                rule_id=rule_id,
                severity=severity,
                detection_pattern=detection_pattern,
                condition=condition,
                required_action=required_action,
                applies_to=applies_to,
                content=body.strip(),
            )
        except Exception as e:
            logger.warning("Failed to parse rule file %s: %s", path, e)
            return None

    def _fallback_chain(self, version: str) -> list[str]:
        """Build version fallback chain: exact → nearest → common.

        Example: '21c' → ['21c', '19c', '23ai', '12c']

        The requested version comes first, then the rest ordered by
        proximity (19c is always included as the most complete set).
        """
        chain = [version]
        for v in KNOWN_VERSIONS:
            if v not in chain:
                chain.append(v)
        return chain


# ---------------------------------------------------------------------------
# Oracle docs URL mapping (version × topic → docs.oracle.com URL)
# ---------------------------------------------------------------------------

# Base URLs per Oracle version (SQL Language Reference manuals)
_ORACLE_DOCS_BASE: dict[str, str] = {
    "23ai": "https://docs.oracle.com/en/database/oracle/oracle-database/23/sqlrf/",
    "21c": "https://docs.oracle.com/en/database/oracle/oracle-database/21/sqlrf/",
    "19c": "https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/",
    "12c": "https://docs.oracle.com/en/database/oracle/oracle-database/12.2/sqlrf/",
}

# Operation → page name in docs.oracle.com (same across versions)
_ORACLE_DOC_PAGES: dict[str, str] = {
    "alter_tablespace": "ALTER-TABLESPACE.html",
    "alter_session": "ALTER-SESSION.html",
    "alter_system": "ALTER-SYSTEM.html",
    "alter_database": "ALTER-DATABASE.html",
    "create_tablespace": "CREATE-TABLESPACE.html",
    "kill_session": "ALTER-SYSTEM.html",  # KILL SESSION is part of ALTER SYSTEM
}

# RMAN and DBA guides (different manual per version)
_ORACLE_ADMIN_BASE: dict[str, str] = {
    "23ai": "https://docs.oracle.com/en/database/oracle/oracle-database/23/admin/",
    "21c": "https://docs.oracle.com/en/database/oracle/oracle-database/21/admin/",
    "19c": "https://docs.oracle.com/en/database/oracle/oracle-database/19/admin/",
    "12c": "https://docs.oracle.com/en/database/oracle/oracle-database/12.2/admin/",
}

_ORACLE_ADMIN_PAGES: dict[str, str] = {
    "rman_archivelog": "managing-archived-redo-log-files.html",
    "undo_management": "managing-undo.html",
    "temp_tablespace": "managing-tablespaces.html",
}


def get_oracle_doc_url(operation: str, version: str) -> Optional[str]:
    """Build a docs.oracle.com URL for a given operation and Oracle version.

    Returns None if no URL mapping exists for this operation.
    """
    # Try SQL Reference pages first
    page = _ORACLE_DOC_PAGES.get(operation)
    if page:
        base = _ORACLE_DOCS_BASE.get(version)
        if base:
            return base + page

    # Try Admin guide pages
    page = _ORACLE_ADMIN_PAGES.get(operation)
    if page:
        base = _ORACLE_ADMIN_BASE.get(version)
        if base:
            return base + page

    return None


# ---------------------------------------------------------------------------
# HTML → Markdown extraction (stdlib html.parser — zero external deps)
# ---------------------------------------------------------------------------


class _OracleHTMLExtractor(HTMLParser):
    """Extract SQL syntax sections from Oracle docs HTML pages.

    Oracle docs.oracle.com uses a consistent HTML structure across all
    versions (12c through 23ai):
    - ``<h2>`` for the page title (only 1 per page)
    - ``<p class="subhead1">`` for section titles (Purpose, Syntax, etc.)
    - ``<span class="bold">`` for sub-section titles (clauses, restrictions)
    - ``<pre>`` for SQL syntax blocks
    - ``<code class="codeph">`` for inline code references
    - ``<p class="notep1">`` for "See Also" / "Note" boxes

    This parser converts that structure to markdown headings, fenced
    code blocks, and paragraphs — suitable for LLM consumption.
    """

    def __init__(self):
        super().__init__()
        self._result: list[str] = []
        self._tag_stack: list[str] = []
        self._attrs_stack: list[dict[str, str]] = []
        self._in_pre = False
        self._in_heading = False
        self._heading_level = 0
        self._in_subhead = False  # <p class="subhead1">
        self._in_bold_span = False  # <span class="bold"> (sub-section title)
        self._in_list_item = False
        self._current_text: list[str] = []
        self._skip_tags = {"script", "style", "nav", "footer", "header", "aside"}
        self._skip_depth = 0

    def _get_attr(self, attrs: list[tuple[str, Optional[str]]], name: str) -> str:
        """Get an attribute value from the attrs list."""
        for k, v in attrs:
            if k.lower() == name:
                return (v or "").lower()
        return ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        tag_lower = tag.lower()
        self._tag_stack.append(tag_lower)
        self._attrs_stack.append({k.lower(): (v or "") for k, v in attrs})

        if tag_lower in self._skip_tags:
            self._skip_depth += 1
            return

        if self._skip_depth > 0:
            return

        cls = self._get_attr(attrs, "class")

        if tag_lower == "pre":
            self._in_pre = True
            self._current_text = []
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
            self._heading_level = int(tag_lower[1])
            self._current_text = []
        elif tag_lower == "p" and "subhead1" in cls:
            # Oracle's section title: <p class="subhead1">Purpose</p>
            self._in_subhead = True
            self._current_text = []
        elif tag_lower == "p" and "notep1" in cls:
            # "See Also:" / "Note:" box title — render as bold
            self._current_text = ["**"]
        elif tag_lower == "p":
            self._current_text = []
        elif tag_lower == "span" and "bold" in cls and not self._in_subhead:
            # Oracle's sub-section title: <span class="bold">RESIZE Clause</span>
            self._in_bold_span = True
            self._current_text = []
        elif tag_lower == "li":
            self._in_list_item = True
            self._current_text = []
        elif tag_lower == "br":
            if self._in_pre:
                self._current_text.append("\n")
        elif tag_lower == "code" and not self._in_pre:
            self._current_text.append("`")

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()

        if tag_lower in self._skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)

        if self._tag_stack and self._tag_stack[-1] == tag_lower:
            self._tag_stack.pop()
        if self._attrs_stack:
            self._attrs_stack.pop()

        if self._skip_depth > 0:
            return

        if tag_lower == "pre":
            self._in_pre = False
            text = "".join(self._current_text).strip()
            if text:
                self._result.append(f"\n```sql\n{text}\n```\n")
            self._current_text = []
        elif tag_lower in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = False
            text = "".join(self._current_text).strip()
            if text:
                prefix = "#" * self._heading_level
                self._result.append(f"\n{prefix} {text}\n")
            self._current_text = []
        elif tag_lower == "p" and self._in_subhead:
            # End of <p class="subhead1"> — emit as ## heading
            self._in_subhead = False
            text = "".join(self._current_text).strip()
            if text:
                self._result.append(f"\n## {text}\n")
            self._current_text = []
        elif tag_lower == "span" and self._in_bold_span:
            # End of <span class="bold"> — emit as ### heading
            self._in_bold_span = False
            text = "".join(self._current_text).strip()
            if text:
                self._result.append(f"\n### {text}\n")
            self._current_text = []
        elif tag_lower == "li":
            self._in_list_item = False
            text = "".join(self._current_text).strip()
            if text:
                self._result.append(f"- {text}")
            self._current_text = []
        elif tag_lower == "p" and not self._in_subhead:
            text = "".join(self._current_text).strip()
            if text:
                # Close notep1 bold if opened
                if text.startswith("**") and not text.endswith("**"):
                    text += "**"
                self._result.append(f"\n{text}\n")
            self._current_text = []
        elif tag_lower == "code" and not self._in_pre:
            self._current_text.append("`")

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return

        if self._in_pre or self._in_heading or self._in_list_item:
            self._current_text.append(data)
        elif self._in_subhead or self._in_bold_span:
            self._current_text.append(data)
        elif self._tag_stack and self._tag_stack[-1] in (
            "p",
            "td",
            "th",
            "code",
            "span",
            "a",
            "em",
            "strong",
            "b",
            "i",
        ):
            self._current_text.append(data)

    def get_markdown(self) -> str:
        """Return the extracted content as markdown text."""
        return "\n".join(self._result).strip()


def extract_oracle_html(html: str) -> str:
    """Convert Oracle docs HTML to simplified markdown.

    Focuses on headings, paragraphs, lists, and <pre> blocks (SQL syntax).
    Strips navigation, scripts, styles, and boilerplate.
    """
    parser = _OracleHTMLExtractor()
    parser.feed(html)
    content = parser.get_markdown()

    # Clean up excessive blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content


# ---------------------------------------------------------------------------
# WebFetcher (fetches Oracle docs from docs.oracle.com, caches to disk)
# ---------------------------------------------------------------------------


class WebFetcher:
    """Fetch Oracle documentation from docs.oracle.com and cache locally.

    Two modes:
    1. **Explicit URL**: doc frontmatter has `web_source: <url>` — fetch that URL
    2. **Auto-mapped URL**: given an operation + version, build the URL from
       the _ORACLE_DOCS_BASE / _ORACLE_DOC_PAGES mapping

    Fetched HTML is converted to markdown via stdlib html.parser and cached
    in docs/oracle/.cache/ with a configurable TTL (default 24 hours).
    """

    # HTTP timeout (seconds)
    FETCH_TIMEOUT = 30

    # User-Agent to use for requests (Oracle docs may block default urllib UA)
    USER_AGENT = "Mozilla/5.0 (compatible; SentriDBA/3.1; " "+https://github.com/sentri-dba/sentri)"

    def __init__(self, docs_path: Path, cache_hours: int = 24):
        self._docs_path = docs_path
        self._cache_dir = docs_path / ".cache"
        self._cache_hours = cache_hours

    def fetch(self, url: str) -> Optional[str]:
        """Fetch and cache a doc from a URL.

        Returns the extracted markdown content, or None on failure.
        Uses disk cache: if a cached version exists and is within TTL,
        returns that instead of re-fetching.
        """
        if not url:
            return None

        # Check cache first
        cached = self._read_cache(url)
        if cached is not None:
            logger.debug("Cache hit for %s", url)
            return cached

        # Fetch from web
        html = self._http_get(url)
        if html is None:
            return None

        # Extract content from HTML
        markdown = extract_oracle_html(html)
        if not markdown.strip():
            logger.warning("No content extracted from %s", url)
            return None

        # Cache to disk
        self._write_cache(url, markdown)

        logger.info("Fetched and cached Oracle doc: %s (%d chars)", url, len(markdown))
        return markdown

    def fetch_for_operation(self, operation: str, version: str) -> Optional[str]:
        """Fetch Oracle docs for a known operation and version.

        Uses the built-in URL mapping to construct the docs.oracle.com URL.
        """
        url = get_oracle_doc_url(operation, version)
        if not url:
            logger.debug("No URL mapping for operation=%s version=%s", operation, version)
            return None
        return self.fetch(url)

    def enrich_doc(self, doc: SyntaxDoc) -> SyntaxDoc:
        """Supplement a local SyntaxDoc with web-fetched content.

        If the doc's frontmatter had a `web_source` URL, fetch that URL.
        Otherwise, try auto-mapping by operation name.
        Returns a new SyntaxDoc with enriched content (original unchanged).
        """
        # Prefer explicit web_source from frontmatter
        web_content = None
        if doc.web_source:
            web_content = self.fetch(doc.web_source)

        # Fallback to auto-mapped URL
        if not web_content:
            web_content = self.fetch_for_operation(doc.operation, doc.version)

        if not web_content:
            return doc

        # Append web content as a clearly-marked supplement
        enriched_content = (
            f"{doc.content}\n\n"
            f"---\n"
            f"## Additional Reference (docs.oracle.com)\n\n"
            f"{web_content}"
        )

        return SyntaxDoc(
            path=doc.path,
            version=doc.version,
            topic=doc.topic,
            operation=doc.operation,
            content=enriched_content,
            keywords=doc.keywords,
        )

    def clear_cache(self) -> int:
        """Remove all cached files. Returns number of files removed."""
        if not self._cache_dir.exists():
            return 0
        count = 0
        for f in self._cache_dir.glob("*.md"):
            f.unlink()
            count += 1
        # Also remove timestamp files
        for f in self._cache_dir.glob("*.ts"):
            f.unlink()
            count += 1
        logger.info("Cleared %d cached files", count)
        return count

    # ------------------------------------------------------------------
    # Internal: HTTP + caching
    # ------------------------------------------------------------------

    def _http_get(self, url: str) -> Optional[str]:
        """Fetch a URL via urllib (stdlib — no external deps)."""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
            with urllib.request.urlopen(req, timeout=self.FETCH_TIMEOUT) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset)
        except urllib.error.HTTPError as e:
            logger.warning("HTTP %d fetching %s: %s", e.code, url, e.reason)
        except urllib.error.URLError as e:
            logger.warning("URL error fetching %s: %s", url, e.reason)
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", url, e)
        return None

    def _cache_key(self, url: str) -> str:
        """Generate a filesystem-safe cache key from a URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _read_cache(self, url: str) -> Optional[str]:
        """Read cached content if it exists and hasn't expired."""
        key = self._cache_key(url)
        cache_file = self._cache_dir / f"{key}.md"
        ts_file = self._cache_dir / f"{key}.ts"

        if not cache_file.exists() or not ts_file.exists():
            return None

        # Check TTL
        try:
            cached_time = float(ts_file.read_text(encoding="utf-8").strip())
            age_hours = (time.time() - cached_time) / 3600
            if age_hours > self._cache_hours:
                logger.debug("Cache expired for %s (%.1f hours old)", url, age_hours)
                return None
        except (ValueError, OSError):
            return None

        try:
            return cache_file.read_text(encoding="utf-8")
        except OSError:
            return None

    def _write_cache(self, url: str, content: str) -> None:
        """Write content to cache with timestamp."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            key = self._cache_key(url)
            cache_file = self._cache_dir / f"{key}.md"
            ts_file = self._cache_dir / f"{key}.ts"
            cache_file.write_text(content, encoding="utf-8")
            ts_file.write_text(str(time.time()), encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to write cache for %s: %s", url, e)


# ---------------------------------------------------------------------------
# EmbeddingRetriever (optional — semantic search)
# ---------------------------------------------------------------------------


class EmbeddingRetriever:
    """Semantic search over Oracle docs via chromadb or FAISS.

    Supplements KeywordRetriever when an alert type has no direct mapping.
    Only loaded if chromadb or faiss-cpu is installed (graceful import).

    Not implemented in Phase 2a — stubbed for architecture completeness.
    """

    def __init__(self, docs_path: Path):
        self._docs_path = docs_path
        self._index = None

    def search(self, query: str, top_k: int = 3) -> list[SyntaxDoc]:
        """Search docs by semantic similarity."""
        # TODO: Phase 2b — implement with chromadb or faiss
        logger.debug("EmbeddingRetriever.search() not yet implemented")
        return []


# ---------------------------------------------------------------------------
# Markdown parsing helpers
# ---------------------------------------------------------------------------


def _split_frontmatter(raw: str) -> tuple[dict, str]:
    """Split a markdown file into YAML frontmatter dict and body text.

    Expects optional frontmatter delimited by --- lines at the top.
    """
    frontmatter: dict = {}
    body = raw

    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1].strip()
            body = parts[2]

            # Simple YAML-like parsing (key: value per line)
            for line in fm_text.splitlines():
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")

                    # Parse lists: [a, b, c]
                    if val.startswith("[") and val.endswith("]"):
                        items = val[1:-1].split(",")
                        val = [item.strip().strip('"').strip("'") for item in items if item.strip()]

                    frontmatter[key] = val

    return frontmatter, body


def _extract_section_code(body: str, section_name: str, lang: str = "sql") -> str:
    """Extract a fenced code block from a named ## section."""
    # Find the section
    pattern = rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, body, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""

    section_text = match.group(1)

    # Find code fence
    fence_pattern = rf"```{re.escape(lang)}\s*\n(.*?)```"
    code_match = re.search(fence_pattern, section_text, re.DOTALL)
    if code_match:
        return code_match.group(1).strip()

    return ""


def _extract_section_text(body: str, section_name: str) -> str:
    """Extract plain text from a named ## section (no code blocks)."""
    pattern = rf"##\s+{re.escape(section_name)}\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, body, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""

    text = match.group(1).strip()
    # Remove code fences if any
    text = re.sub(r"```\w*\n.*?```", "", text, flags=re.DOTALL).strip()
    return text
