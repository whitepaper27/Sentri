"""RagManager — orchestrates ground truth doc loading, prompt formatting, and SQL validation.

Version-aware: auto-detects Oracle version from DatabaseProfile, loads the
correct syntax docs from docs/oracle/{version}/{topic}/, and validates
LLM-generated SQL against hard rules before execution.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sentri.config.paths import ORACLE_DOCS_DIR

logger = logging.getLogger("sentri.rag.manager")

# ---------------------------------------------------------------------------
# Version constants
# ---------------------------------------------------------------------------

# Ordered from newest to oldest (fallback priority)
KNOWN_VERSIONS = ["23ai", "21c", "19c", "12c"]
DEFAULT_VERSION = "19c"

# Map major version number to folder name
_VERSION_MAP = {
    "23": "23ai",
    "21": "21c",
    "19": "19c",
    "18": "19c",  # 18c is close enough to 19c
    "12": "12c",
    "11": "12c",  # 11g maps to 12c (closest available)
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DocConfig:
    """Configuration for doc loading."""

    enable_web_fetch: bool = False
    enable_embeddings: bool = False
    cache_hours: int = 24
    max_docs_in_prompt: int = 5
    default_version: str = DEFAULT_VERSION
    validate_sql: bool = True  # Post-generation SQL validation


@dataclass
class SyntaxDoc:
    """One loaded syntax document."""

    path: str  # e.g., "19c/tablespace/alter_tablespace.md"
    version: str  # "19c"
    topic: str  # "tablespace"
    operation: str  # "alter_tablespace"
    content: str  # Full markdown content (body, no frontmatter)
    keywords: list[str] = field(default_factory=list)
    web_source: str = ""  # Optional docs.oracle.com URL for web enrichment


@dataclass
class RuleDoc:
    """One loaded hard rule."""

    rule_id: str  # "bigfile_no_add_datafile"
    severity: str  # "CRITICAL"
    detection_pattern: str  # regex string
    condition: str  # human-readable condition
    required_action: str  # what to do instead
    applies_to: list[str] = field(default_factory=list)
    content: str = ""  # Full markdown content


@dataclass
class RuleViolation:
    """A specific rule violation found in generated SQL."""

    rule_id: str
    severity: str
    message: str
    sql_fragment: str  # The offending SQL
    suggested_fix: str


@dataclass
class ValidationResult:
    """Result of validating SQL against ground truth rules."""

    is_valid: bool
    violations: list[RuleViolation] = field(default_factory=list)
    checked_rules: int = 0


@dataclass
class DocContext:
    """Full doc context for one alert + database."""

    alert_type: str
    database_id: str
    oracle_version: str
    syntax_docs: list[SyntaxDoc] = field(default_factory=list)
    rule_docs: list[RuleDoc] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)

    @property
    def has_docs(self) -> bool:
        return len(self.syntax_docs) > 0 or len(self.rule_docs) > 0


# ---------------------------------------------------------------------------
# RagManager
# ---------------------------------------------------------------------------


class RagManager:
    """Orchestrates ground truth doc loading, prompt formatting, SQL validation."""

    def __init__(self, policy_loader, environment_repo=None, settings=None):
        self._loader = policy_loader
        self._env_repo = environment_repo
        self._settings = settings
        self._config = self._load_config(settings)

        # Lazy import to avoid circular deps
        from sentri.rag.retriever import KeywordRetriever

        self._retriever = KeywordRetriever(self._get_docs_path())

    @staticmethod
    def _load_config(settings) -> DocConfig:
        """Build DocConfig from Settings.rag if available."""
        if settings is None:
            return DocConfig()
        rag_cfg = getattr(settings, "rag", None)
        if rag_cfg is None:
            return DocConfig()
        return DocConfig(
            enable_web_fetch=getattr(rag_cfg, "enable_web_fetch", False),
            enable_embeddings=getattr(rag_cfg, "enable_embeddings", False),
            cache_hours=getattr(rag_cfg, "cache_hours", 24),
            max_docs_in_prompt=getattr(rag_cfg, "max_docs_in_prompt", 5),
            default_version=getattr(rag_cfg, "default_version", "19c"),
            validate_sql=getattr(rag_cfg, "validate_sql", True),
        )

    def get_context(self, alert_type: str, database_id: str) -> DocContext:
        """Load version-matched docs for an alert + database."""
        version = self._resolve_version(database_id)
        syntax = self._retriever.get_syntax_docs(alert_type, version)
        rules = self._retriever.get_rule_docs(alert_type)

        # Optionally enrich local docs with web-fetched Oracle docs
        if self._config.enable_web_fetch and syntax:
            syntax = self._enrich_with_web(syntax)

        source_files = [d.path for d in syntax] + [r.rule_id for r in rules]

        logger.info(
            "RAG context for %s on %s: version=%s, %d syntax docs, %d rules",
            alert_type,
            database_id,
            version,
            len(syntax),
            len(rules),
        )

        return DocContext(
            alert_type=alert_type,
            database_id=database_id,
            oracle_version=version,
            syntax_docs=syntax,
            rule_docs=rules,
            source_files=source_files,
        )

    def format_for_prompt(self, ctx: DocContext) -> str:
        """Format doc context as human-readable text for LLM prompt injection."""
        if not ctx.has_docs:
            return ""

        parts = [f"## Verified Oracle Syntax Reference ({ctx.oracle_version})"]

        # Syntax docs
        for doc in ctx.syntax_docs:
            parts.append(f"\n### {doc.operation.replace('_', ' ').title()}")
            parts.append(doc.content)

        # Hard rules
        if ctx.rule_docs:
            parts.append("\n### Hard Rules (MUST follow)")
            for rule in ctx.rule_docs:
                parts.append(f"- **{rule.rule_id}** [{rule.severity}]: {rule.required_action}")

        return "\n".join(parts)

    def validate_sql(self, sql: str, alert_type: str, database_id: str) -> ValidationResult:
        """Validate generated SQL against ground truth hard rules."""
        from sentri.rag.validator import SQLValidator

        ctx = self.get_context(alert_type, database_id)
        validator = SQLValidator(self._env_repo)
        return validator.validate(sql, ctx.rule_docs, database_id)

    def reload(self) -> None:
        """Clear cached state (called on policy reload)."""
        from sentri.rag.retriever import KeywordRetriever

        self._retriever = KeywordRetriever(self._get_docs_path())
        logger.info("RAG manager reloaded")

    # ------------------------------------------------------------------
    # Web enrichment
    # ------------------------------------------------------------------

    def _enrich_with_web(self, docs: list[SyntaxDoc]) -> list[SyntaxDoc]:
        """Enrich local syntax docs with content from docs.oracle.com."""
        from sentri.rag.retriever import WebFetcher

        fetcher = WebFetcher(
            self._get_docs_path(),
            cache_hours=self._config.cache_hours,
        )
        enriched = []
        for doc in docs:
            try:
                enriched.append(fetcher.enrich_doc(doc))
            except Exception as e:
                logger.warning("Web enrichment failed for %s: %s", doc.path, e)
                enriched.append(doc)  # Keep original on failure
        return enriched

    # ------------------------------------------------------------------
    # Version resolution
    # ------------------------------------------------------------------

    def _resolve_version(self, database_id: str) -> str:
        """Resolve Oracle version for a database.

        Priority: live profile → env record → settings YAML → fallback.
        """
        # 1. Live profile (most accurate — from Agent 0 profiler)
        if self._env_repo:
            profile_json = self._env_repo.get_profile(database_id)
            if profile_json:
                version_str = _extract_version_from_profile(profile_json)
                if version_str:
                    normalized = normalize_version(version_str)
                    logger.debug(
                        "Version for %s from profile: %s → %s",
                        database_id,
                        version_str,
                        normalized,
                    )
                    return normalized

            # 2. Environment record (static, from sentri init)
            env = self._env_repo.get(database_id)
            if env and env.oracle_version:
                normalized = normalize_version(env.oracle_version)
                logger.debug(
                    "Version for %s from env record: %s → %s",
                    database_id,
                    env.oracle_version,
                    normalized,
                )
                return normalized

        # 3. Settings YAML (static config)
        if self._settings:
            db_cfg = self._settings.get_database(database_id)
            if db_cfg and db_cfg.oracle_version:
                normalized = normalize_version(db_cfg.oracle_version)
                logger.debug(
                    "Version for %s from settings: %s → %s",
                    database_id,
                    db_cfg.oracle_version,
                    normalized,
                )
                return normalized

        # 4. Fallback
        logger.debug(
            "No version info for %s, using default: %s",
            database_id,
            DEFAULT_VERSION,
        )
        return DEFAULT_VERSION

    def _get_docs_path(self):
        """Return the path to oracle docs directory."""
        # First try the runtime location (SENTRI_HOME/docs/oracle)
        if ORACLE_DOCS_DIR.exists():
            return ORACLE_DOCS_DIR

        # Fallback to bundled defaults
        from sentri.config.paths import get_default_policies_path

        bundled = get_default_policies_path() / "docs" / "oracle"
        if bundled.exists():
            return bundled

        # Last resort: use the loader's base path
        docs_path = self._loader._base_path / "docs" / "oracle"
        return docs_path


# ---------------------------------------------------------------------------
# Version helpers (module-level, testable)
# ---------------------------------------------------------------------------


def normalize_version(raw: str) -> str:
    """Map an Oracle version string to a folder name.

    Examples:
        '19.12.0.0.0' → '19c'
        '21.3.0.0.0'  → '21c'
        '23.4.0.0.0'  → '23ai'
        '12.2.0.1.0'  → '12c'
        '19c'          → '19c'  (already normalized)
        '23ai'         → '23ai' (already normalized)
    """
    if not raw:
        return DEFAULT_VERSION

    raw = raw.strip()

    # Already a known folder name?
    if raw in KNOWN_VERSIONS:
        return raw

    # Extract major version number (first component before '.')
    match = re.match(r"(\d+)", raw)
    if match:
        major = match.group(1)
        if major in _VERSION_MAP:
            return _VERSION_MAP[major]

    logger.warning("Cannot normalize Oracle version '%s', using default", raw)
    return DEFAULT_VERSION


def _extract_version_from_profile(profile_json: str) -> Optional[str]:
    """Extract Oracle version from a DatabaseProfile JSON blob.

    Looks in db_config.instance_info[0].version (set by Agent 0 profiler).
    """
    try:
        data = json.loads(profile_json)
        # DatabaseProfile stores everything in db_config
        db_config = data.get("db_config", data)
        instance_info = db_config.get("instance_info", [])
        if instance_info and isinstance(instance_info, list):
            return instance_info[0].get("version")
    except (json.JSONDecodeError, IndexError, AttributeError, TypeError):
        pass
    return None
