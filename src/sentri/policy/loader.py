"""Parse .md policy files into structured Python dicts.

Policy files use YAML frontmatter + Markdown sections with fenced code blocks.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("sentri.policy.loader")


class PolicyLoader:
    """Load and parse .md policy files from the Sentri runtime directory."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self._cache: dict[str, dict] = {}

    def load(self, category: str, name: str) -> dict:
        """Load a policy file by category and name.

        Args:
            category: Subdirectory (brain, agents, alerts, environments, workflows)
            name: Filename without .md extension
        """
        cache_key = f"{category}/{name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        path = self.base_path / category / f"{name}.md"
        if not path.exists():
            logger.warning("Policy file not found: %s", path)
            return {}

        raw = path.read_text(encoding="utf-8")
        parsed = parse_policy_md(raw)
        parsed["_source_path"] = str(path)
        self._cache[cache_key] = parsed
        return parsed

    def load_alert(self, alert_type: str) -> dict:
        """Load an alert pattern policy."""
        return self.load("alerts", alert_type)

    def load_brain(self, name: str) -> dict:
        """Load a brain policy."""
        return self.load("brain", name)

    def load_agent(self, name: str) -> dict:
        """Load an agent configuration."""
        return self.load("agents", name)

    def load_environment(self, name: str) -> dict:
        """Load an environment configuration."""
        return self.load("environments", name)

    def load_workflow(self, name: str) -> dict:
        """Load a workflow specification."""
        return self.load("workflows", name)

    def load_check(self, check_type: str) -> dict:
        """Load a health check definition from checks/."""
        return self.load("checks", check_type)

    def reload(self) -> None:
        """Clear the cache, forcing files to be re-read from disk."""
        self._cache.clear()
        logger.info("Policy cache cleared")

    def write_policy(self, category: str, name: str, content: str) -> Path:
        """Write content to a policy .md file and invalidate cache.

        Returns the path of the written file.
        """
        path = self.base_path / category / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        # Invalidate cache for this file
        cache_key = f"{category}/{name}"
        self._cache.pop(cache_key, None)

        logger.info("Policy file written: %s", path)
        return path

    def load_all_alerts(self) -> dict[str, dict]:
        """Load all alert policy files from the alerts directory (recursive).

        Scans alerts/*.md AND alerts/**/*.md (e.g., alerts/oracle/*.md,
        alerts/postgres/*.md) for multi-database support.
        """
        alerts_dir = self.base_path / "alerts"
        result = {}
        if not alerts_dir.exists():
            return result
        for path in sorted(alerts_dir.rglob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            if ".backup" in path.parts:
                continue
            # Build category path relative to base (e.g., "alerts" or "alerts/oracle")
            rel_dir = path.parent.relative_to(self.base_path)
            category = str(rel_dir).replace("\\", "/")
            name = path.stem
            result[name] = self.load(category, name)
        return result

    def load_all_checks(self) -> dict[str, dict]:
        """Load all health check policy files from the checks directory (recursive).

        Scans checks/*.md AND checks/**/*.md for multi-database support.
        """
        checks_dir = self.base_path / "checks"
        result = {}
        if not checks_dir.exists():
            return result
        for path in sorted(checks_dir.rglob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            if ".backup" in path.parts:
                continue
            rel_dir = path.parent.relative_to(self.base_path)
            category = str(rel_dir).replace("\\", "/")
            name = path.stem
            result[name] = self.load(category, name)
        return result


def parse_policy_md(content: str) -> dict:
    """Parse a policy markdown file into a structured dict.

    Returns a dict with:
    - 'frontmatter': parsed YAML frontmatter (dict)
    - Section names (lowercase, underscored) as keys
    - Code blocks extracted by language tag
    """
    result: dict = {}

    # 1. Extract YAML frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        try:
            result["frontmatter"] = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError:
            result["frontmatter"] = {}
        content = content[fm_match.end() :]
    else:
        result["frontmatter"] = {}

    # 2. Parse sections
    current_section: Optional[str] = None
    current_lines: list[str] = []

    for line in content.split("\n"):
        # Detect ## headers as section boundaries
        header_match = re.match(r"^##\s+(.+)$", line)
        if header_match:
            # Save previous section
            if current_section:
                result[current_section] = _parse_section_content("\n".join(current_lines))
            current_section = _normalize_key(header_match.group(1))
            current_lines = []
        else:
            current_lines.append(line)

    # Save last section
    if current_section:
        result[current_section] = _parse_section_content("\n".join(current_lines))

    return result


def _normalize_key(header: str) -> str:
    """Convert a section header to a dict key: lowercase, spaces to underscores."""
    key = header.strip().lower()
    key = re.sub(r"[^a-z0-9\s]", "", key)
    key = re.sub(r"\s+", "_", key)
    return key


def _parse_section_content(content: str) -> dict | str:
    """Parse the content under a section header.

    Extracts:
    - Fenced code blocks (keyed by language)
    - Bullet lists (as list of strings)
    - Plain text
    """
    result: dict = {}
    code_blocks: list[dict] = []
    bullets: list[str] = []
    plain_lines: list[str] = []

    # Extract fenced code blocks
    code_pattern = re.compile(r"```(\w*)\s*\n(.*?)```", re.DOTALL)
    remaining = content

    for match in code_pattern.finditer(content):
        lang = match.group(1).strip() or "text"
        code = match.group(2).strip()
        code_blocks.append({"language": lang, "code": code})
        if lang not in result:
            result[lang] = code
        else:
            # Multiple blocks of same language: make a list
            existing = result[lang]
            if isinstance(existing, list):
                existing.append(code)
            else:
                result[lang] = [existing, code]

    # Remove code blocks for bullet/text parsing
    remaining = code_pattern.sub("", content)

    # Extract bullet points
    for line in remaining.split("\n"):
        bullet_match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bullet_match:
            bullets.append(bullet_match.group(1).strip())
        elif line.strip():
            plain_lines.append(line.strip())

    if bullets:
        result["items"] = bullets
    if plain_lines:
        result["text"] = "\n".join(plain_lines)

    # If only plain text with no code blocks or bullets, return as string
    if not code_blocks and not bullets and plain_lines:
        return "\n".join(plain_lines)

    # If only a single code block and nothing else, simplify
    if len(code_blocks) == 1 and not bullets and not plain_lines:
        return result

    return result if result else content.strip()
