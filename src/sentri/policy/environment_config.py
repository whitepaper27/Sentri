"""Load environment configuration from policy .md files."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sentri.core.constants import AutonomyLevel, Environment

from .loader import PolicyLoader

logger = logging.getLogger("sentri.policy.environment")


@dataclass
class AutonomyOverride:
    """A per-database autonomy level override from environments/*.md."""

    level: AutonomyLevel
    reason: str = ""
    approved_by: str = ""
    expires: Optional[datetime] = None


class EnvironmentConfig:
    """Read environment policies from .md files."""

    def __init__(self, policy_loader: PolicyLoader):
        self._loader = policy_loader

    def get_autonomy_override(self, database_id: str) -> Optional[AutonomyOverride]:
        """Get per-database autonomy override if configured and not expired.

        Reads `autonomy_override`, `override_reason`, `override_approved_by`,
        and `override_expires` from the environments/*.md frontmatter.

        Returns None if:
        - No environment policy file found for this database
        - No `autonomy_override` key in frontmatter
        - Override has expired (past override_expires date)
        """
        policy = self._find_env_policy(database_id)
        if not policy:
            return None

        fm = policy.get("frontmatter", {})
        override_str = fm.get("autonomy_override", "")
        if not override_str:
            return None

        # Parse the override level
        try:
            level = AutonomyLevel(override_str)
        except ValueError:
            logger.warning(
                "Invalid autonomy_override '%s' for %s — ignoring",
                override_str,
                database_id,
            )
            return None

        # Check expiration
        expires_str = fm.get("override_expires", "")
        expires_dt = None
        if expires_str:
            try:
                expires_dt = datetime.strptime(str(expires_str), "%Y-%m-%d").replace(
                    tzinfo=timezone.utc,
                )
                now = datetime.now(timezone.utc)

                if now > expires_dt:
                    logger.info(
                        "Autonomy override for %s expired on %s — reverting to default",
                        database_id,
                        expires_str,
                    )
                    return None

                # Warn if within 7 days of expiry
                days_until = (expires_dt - now).days
                if days_until <= 7:
                    logger.warning(
                        "Autonomy override for %s expires in %d days (on %s)",
                        database_id,
                        days_until,
                        expires_str,
                    )
            except ValueError:
                logger.warning(
                    "Invalid override_expires date '%s' for %s — ignoring expiry",
                    expires_str,
                    database_id,
                )

        return AutonomyOverride(
            level=level,
            reason=fm.get("override_reason", ""),
            approved_by=fm.get("override_approved_by", ""),
            expires=expires_dt,
        )

    def get_autonomy_level(self, database_id: str) -> AutonomyLevel:
        """Determine the autonomy level for a database from its environment file."""
        policy = self._find_env_policy(database_id)
        if not policy:
            logger.warning("No env policy for %s, defaulting to ADVISORY", database_id)
            return AutonomyLevel.ADVISORY

        fm = policy.get("frontmatter", {})
        level = fm.get("autonomy_level", "ADVISORY")
        try:
            return AutonomyLevel(level)
        except ValueError:
            return AutonomyLevel.ADVISORY

    def get_environment(self, database_id: str) -> Environment:
        """Get the environment tier for a database."""
        policy = self._find_env_policy(database_id)
        if not policy:
            logger.warning("No env policy for %s, defaulting to PROD", database_id)
            return Environment.PROD

        fm = policy.get("frontmatter", {})
        env = fm.get("environment", "PROD")
        try:
            return Environment(env)
        except ValueError:
            return Environment.PROD

    def get_critical_schemas(self, database_id: str) -> list[str]:
        """Get the list of critical schemas for a database."""
        policy = self._find_env_policy(database_id)
        if not policy:
            return []

        section = policy.get("critical_schemas", {})
        if isinstance(section, dict):
            return section.get("items", [])
        return []

    def _find_env_policy(self, database_id: str) -> Optional[dict]:
        """Find the environment policy file for a database_id.

        Maps database_id to filename: PROD-DB-07 -> prod_db_07
        """
        filename = database_id.lower().replace("-", "_")
        policy = self._loader.load_environment(filename)
        if policy:
            return policy

        # Try loading all environment files and matching by frontmatter
        env_dir = self._loader.base_path / "environments"
        if not env_dir.exists():
            return None

        for path in env_dir.glob("*.md"):
            if path.name.lower() == "readme.md":
                continue
            p = self._loader.load("environments", path.stem)
            fm = p.get("frontmatter", {})
            if fm.get("database_id") == database_id:
                return p
        return None
