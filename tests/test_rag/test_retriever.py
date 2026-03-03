"""Tests for the RAG retriever — keyword mapping + doc loading."""

from sentri.rag.retriever import (
    ALERT_DOC_MAP,
    KeywordRetriever,
    _extract_section_code,
    _extract_section_text,
    _split_frontmatter,
)

# ---------------------------------------------------------------------------
# ALERT_DOC_MAP coverage
# ---------------------------------------------------------------------------


class TestAlertDocMap:
    def test_tablespace_full_mapped(self):
        assert "tablespace_full" in ALERT_DOC_MAP
        assert len(ALERT_DOC_MAP["tablespace_full"]) > 0

    def test_temp_full_mapped(self):
        assert "temp_full" in ALERT_DOC_MAP

    def test_session_blocker_mapped(self):
        assert "session_blocker" in ALERT_DOC_MAP

    def test_listener_down_empty(self):
        """OS-level alert — no SQL docs."""
        assert ALERT_DOC_MAP.get("listener_down") == []


# ---------------------------------------------------------------------------
# KeywordRetriever
# ---------------------------------------------------------------------------


class TestKeywordRetriever:
    def test_unknown_alert_returns_empty(self, tmp_path):
        retriever = KeywordRetriever(tmp_path)
        docs = retriever.get_syntax_docs("nonexistent_alert", "19c")
        assert docs == []

    def test_no_docs_dir_returns_empty(self, tmp_path):
        retriever = KeywordRetriever(tmp_path)
        docs = retriever.get_syntax_docs("tablespace_full", "19c")
        assert docs == []

    def test_loads_doc_from_version_folder(self, tmp_path):
        """Create a doc file in 19c/tablespace/ and verify it's loaded."""
        doc_dir = tmp_path / "19c" / "tablespace"
        doc_dir.mkdir(parents=True)
        (doc_dir / "alter_tablespace.md").write_text(
            "---\nversion: 19c\ntopic: tablespace\n"
            "operation: alter_tablespace\nkeywords: [tablespace]\n"
            "applies_to: [tablespace_full]\n---\n\n# Test Content\nSome SQL here",
            encoding="utf-8",
        )

        retriever = KeywordRetriever(tmp_path)
        docs = retriever.get_syntax_docs("tablespace_full", "19c")
        assert len(docs) == 1
        assert docs[0].version == "19c"
        assert docs[0].topic == "tablespace"
        assert docs[0].operation == "alter_tablespace"
        assert "Test Content" in docs[0].content

    def test_version_fallback(self, tmp_path):
        """If 21c/ doesn't exist but 19c/ does, fallback to 19c."""
        doc_dir = tmp_path / "19c" / "tablespace"
        doc_dir.mkdir(parents=True)
        (doc_dir / "alter_tablespace.md").write_text(
            "---\nversion: 19c\ntopic: tablespace\n"
            "operation: alter_tablespace\n---\n\n# 19c Fallback",
            encoding="utf-8",
        )

        retriever = KeywordRetriever(tmp_path)
        docs = retriever.get_syntax_docs("tablespace_full", "21c")
        assert len(docs) == 1
        assert docs[0].version == "19c"
        assert "19c Fallback" in docs[0].content

    def test_prefers_exact_version(self, tmp_path):
        """If both 21c/ and 19c/ exist, prefer exact match."""
        for v in ["19c", "21c"]:
            doc_dir = tmp_path / v / "tablespace"
            doc_dir.mkdir(parents=True)
            (doc_dir / "alter_tablespace.md").write_text(
                f"---\nversion: {v}\ntopic: tablespace\n"
                f"operation: alter_tablespace\n---\n\n# {v} doc",
                encoding="utf-8",
            )

        retriever = KeywordRetriever(tmp_path)
        docs = retriever.get_syntax_docs("tablespace_full", "21c")
        assert len(docs) == 1
        assert docs[0].version == "21c"

    def test_fallback_chain_order(self, tmp_path):
        """Verify fallback chain: exact → 19c → others."""
        retriever = KeywordRetriever(tmp_path)
        chain = retriever._fallback_chain("21c")
        assert chain[0] == "21c"
        # 19c should be in the chain
        assert "19c" in chain
        # All known versions should be in the chain
        assert len(chain) >= 4


# ---------------------------------------------------------------------------
# Rule docs
# ---------------------------------------------------------------------------


class TestRuleDocs:
    def test_loads_rules(self, tmp_path):
        """Create a rule file and verify it's loaded."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "test_rule.md").write_text(
            "---\nrule_id: test_rule\nseverity: HIGH\n"
            "applies_to: [tablespace_full]\n---\n\n"
            "# Test Rule\n\n"
            "## Rule\nA test rule\n\n"
            "## Detection Pattern\n\n```regex\n(?i)TEST\n```\n\n"
            "## Condition\nAlways\n\n"
            "## Required Action\nDo something else\n",
            encoding="utf-8",
        )

        retriever = KeywordRetriever(tmp_path)
        rules = retriever.get_rule_docs("tablespace_full")
        assert len(rules) == 1
        assert rules[0].rule_id == "test_rule"
        assert rules[0].severity == "HIGH"
        assert rules[0].detection_pattern == "(?i)TEST"
        assert "Do something else" in rules[0].required_action

    def test_rule_filtering_by_alert(self, tmp_path):
        """Rules are filtered by applies_to."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "rule_a.md").write_text(
            "---\nrule_id: rule_a\nseverity: HIGH\napplies_to: [tablespace_full]\n---\n\nContent",
            encoding="utf-8",
        )
        (rules_dir / "rule_b.md").write_text(
            "---\nrule_id: rule_b\nseverity: MEDIUM\napplies_to: [cpu_high]\n---\n\nContent",
            encoding="utf-8",
        )

        retriever = KeywordRetriever(tmp_path)

        ts_rules = retriever.get_rule_docs("tablespace_full")
        assert len(ts_rules) == 1
        assert ts_rules[0].rule_id == "rule_a"

        cpu_rules = retriever.get_rule_docs("cpu_high")
        assert len(cpu_rules) == 1
        assert cpu_rules[0].rule_id == "rule_b"

    def test_no_rules_dir(self, tmp_path):
        retriever = KeywordRetriever(tmp_path)
        rules = retriever.get_rule_docs("tablespace_full")
        assert rules == []

    def test_readme_skipped(self, tmp_path):
        """README.md in rules/ should be ignored."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "README.md").write_text("# Rules README", encoding="utf-8")

        retriever = KeywordRetriever(tmp_path)
        rules = retriever.get_rule_docs("tablespace_full")
        assert rules == []


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


class TestFrontmatterParsing:
    def test_with_frontmatter(self):
        raw = "---\nversion: 19c\ntopic: tablespace\n---\n\n# Body"
        fm, body = _split_frontmatter(raw)
        assert fm["version"] == "19c"
        assert fm["topic"] == "tablespace"
        assert "# Body" in body

    def test_no_frontmatter(self):
        raw = "# Just a body\nNo frontmatter here"
        fm, body = _split_frontmatter(raw)
        assert fm == {}
        assert "# Just a body" in body

    def test_list_parsing(self):
        raw = "---\nkeywords: [tablespace, add datafile, resize]\napplies_to: [tablespace_full, temp_full]\n---\n\nBody"
        fm, body = _split_frontmatter(raw)
        assert isinstance(fm["keywords"], list)
        assert "tablespace" in fm["keywords"]
        assert "add datafile" in fm["keywords"]
        assert isinstance(fm["applies_to"], list)
        assert "tablespace_full" in fm["applies_to"]

    def test_quoted_values(self):
        raw = "---\nversion: \"19c\"\ntopic: 'tablespace'\n---\n\nBody"
        fm, body = _split_frontmatter(raw)
        assert fm["version"] == "19c"
        assert fm["topic"] == "tablespace"


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------


class TestSectionExtraction:
    def test_extract_code(self):
        body = "## Detection Pattern\n\n```regex\n(?i)ADD\\s+FILE\n```\n\n## Next"
        code = _extract_section_code(body, "Detection Pattern", "regex")
        assert code == "(?i)ADD\\s+FILE"

    def test_extract_text(self):
        body = "## Condition\nSome condition here\n\n## Next"
        text = _extract_section_text(body, "Condition")
        assert text == "Some condition here"

    def test_missing_section(self):
        body = "## Other\nContent"
        assert _extract_section_code(body, "Nonexistent", "sql") == ""
        assert _extract_section_text(body, "Nonexistent") == ""
