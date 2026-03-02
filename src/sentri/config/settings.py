"""Configuration management: YAML file + environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .paths import CONFIG_PATH

logger = logging.getLogger("sentri.config")


@dataclass
class EmailConfig:
    imap_server: str = ""
    imap_port: int = 993
    username: str = ""
    password: str = ""
    use_ssl: bool = True
    smtp_server: str = ""
    smtp_port: int = 587
    use_tls: bool = True


@dataclass
class DatabaseConfig:
    name: str = ""
    db_engine: str = "oracle"  # oracle, postgres, snowflake, sqlserver
    connection_string: str = ""  # oracle://user@host:port/service
    environment: str = ""  # DEV, UAT, PROD
    password: str = ""  # Set via env var, not in YAML
    username: str = ""  # Per-DB username override (else parsed from URL)
    aliases: list[str] = field(default_factory=list)  # Alternate names in emails
    autonomy_level: str = ""  # AUTONOMOUS, SUPERVISED, ADVISORY
    oracle_version: str = ""
    architecture: str = "STANDALONE"  # STANDALONE, CDB, RAC
    critical_schemas: str = ""  # Comma-separated
    business_owner: str = ""
    dba_owner: str = ""


@dataclass
class ApprovalConfig:
    slack_webhook_url: str = ""
    approval_timeout: int = 3600
    jira_url: str = ""
    jira_project: str = ""
    email_enabled: bool = False
    approval_recipients: str = ""  # Comma-separated; defaults to email.username


@dataclass
class MonitoringConfig:
    log_level: str = "INFO"
    scout_poll_interval: int = 60
    orchestrator_poll_interval: int = 10
    profile_refresh_hours: int = 24  # Re-profile databases every N hours (0 = startup only)


@dataclass
class LearningConfig:
    """v2.0: Learning engine configuration."""

    enabled: bool = False
    min_observations: int = 5  # Min observations before proposing changes
    judge_count: int = 3  # Number of LLM judges
    judge_agreement: int = 2  # Min judges that must agree
    monitoring_days: int = 30  # Days to monitor after an improvement

    # Default LLM provider (fallback when role-specific not set)
    llm_provider: str = ""  # "claude", "openai", "gemini", or "" for NoOp
    llm_api_key: str = ""  # Legacy single key (SENTRI_LLM_API_KEY)
    llm_model: str = ""  # Model override (e.g. "claude-sonnet-4-5-20250929")
    daily_cost_limit: float = 5.0  # USD per day

    # Per-provider API keys (set via env vars)
    claude_api_key: str = ""  # SENTRI_CLAUDE_API_KEY
    openai_api_key: str = ""  # SENTRI_OPENAI_API_KEY
    gemini_api_key: str = ""  # SENTRI_GEMINI_API_KEY

    # Role-specific provider overrides (fall back to llm_provider)
    researcher_provider: str = ""  # Provider for option generation
    judge_provider: str = ""  # Provider for proposal judging ("diverse" = use all)

    def get_api_key(self, provider_name: str) -> str:
        """Get the API key for a specific provider, falling back to llm_api_key."""
        provider = provider_name.lower().strip()
        key_map = {
            "claude": self.claude_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
        }
        return key_map.get(provider, "") or self.llm_api_key

    def get_researcher_provider(self) -> str:
        """Get the provider name for the researcher role."""
        return self.researcher_provider or self.llm_provider

    def get_judge_provider(self) -> str:
        """Get the provider name for the judge role."""
        return self.judge_provider or self.llm_provider


@dataclass
class NotificationAdapterConfig:
    """v5.1b: Configuration for a single notification adapter."""

    type: str = ""  # email, webhook, pagerduty
    enabled: bool = False
    url: str = ""  # Webhook URL
    routing_key: str = ""  # PagerDuty integration key
    headers: dict = field(default_factory=dict)


@dataclass
class NotificationsConfig:
    """v5.1b: Notification adapters configuration."""

    adapters: list[NotificationAdapterConfig] = field(default_factory=list)


@dataclass
class RagConfig:
    """v3.1: Ground Truth RAG configuration."""

    enable_web_fetch: bool = False  # Fetch docs from docs.oracle.com
    enable_embeddings: bool = False  # Semantic search (requires chromadb/faiss)
    cache_hours: int = 24  # Web fetch cache TTL
    max_docs_in_prompt: int = 5  # Max syntax docs injected into LLM prompt
    default_version: str = "19c"  # Fallback Oracle version
    validate_sql: bool = True  # Post-generation SQL validation against rules


@dataclass
class Settings:
    """Complete application settings."""

    email: EmailConfig = field(default_factory=EmailConfig)
    databases: list[DatabaseConfig] = field(default_factory=list)
    approvals: ApprovalConfig = field(default_factory=ApprovalConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)

    @classmethod
    def load(cls, config_path: Path | None = None) -> Settings:
        """Load settings from YAML file, overlaid with environment variables.

        Priority: env vars > YAML file > defaults
        """
        settings = cls()
        path = config_path or CONFIG_PATH

        # Load YAML if it exists
        if path.exists():
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                settings = cls._from_dict(raw)
                logger.info("Loaded config from %s", path)
            except Exception as e:
                logger.warning("Failed to load config from %s: %s", path, e)

        # Override with environment variables
        settings._apply_env_vars()
        return settings

    @classmethod
    def _from_dict(cls, raw: dict) -> Settings:
        s = cls()

        email = raw.get("email", {})
        if email:
            s.email = EmailConfig(
                imap_server=email.get("imap_server", ""),
                imap_port=email.get("imap_port", 993),
                username=email.get("username", ""),
                password=email.get("password", ""),
                use_ssl=email.get("use_ssl", True),
                smtp_server=email.get("smtp_server", ""),
                smtp_port=email.get("smtp_port", 587),
                use_tls=email.get("use_tls", True),
            )

        for db_cfg in raw.get("databases", []):
            aliases_raw = db_cfg.get("aliases", [])
            if isinstance(aliases_raw, str):
                aliases_raw = [a.strip() for a in aliases_raw.split(",") if a.strip()]
            s.databases.append(
                DatabaseConfig(
                    name=db_cfg.get("name", ""),
                    db_engine=db_cfg.get("db_engine", "oracle"),
                    connection_string=db_cfg.get("connection_string", ""),
                    environment=db_cfg.get("environment", ""),
                    password=db_cfg.get("password", ""),
                    username=db_cfg.get("username", ""),
                    aliases=aliases_raw,
                    autonomy_level=db_cfg.get("autonomy_level", ""),
                    oracle_version=db_cfg.get("oracle_version", ""),
                    architecture=db_cfg.get("architecture", "STANDALONE"),
                    critical_schemas=db_cfg.get("critical_schemas", ""),
                    business_owner=db_cfg.get("business_owner", ""),
                    dba_owner=db_cfg.get("dba_owner", ""),
                )
            )

        approvals = raw.get("approvals", {})
        if approvals:
            s.approvals = ApprovalConfig(
                slack_webhook_url=approvals.get("slack_webhook_url", ""),
                approval_timeout=approvals.get("approval_timeout", 3600),
                jira_url=approvals.get("jira_url", ""),
                jira_project=approvals.get("jira_project", ""),
                email_enabled=approvals.get("email_enabled", False),
                approval_recipients=approvals.get("approval_recipients", ""),
            )

        monitoring = raw.get("monitoring", {})
        if monitoring:
            s.monitoring = MonitoringConfig(
                log_level=monitoring.get("log_level", "INFO"),
                scout_poll_interval=monitoring.get("scout_poll_interval", 60),
                orchestrator_poll_interval=monitoring.get("orchestrator_poll_interval", 10),
                profile_refresh_hours=monitoring.get("profile_refresh_hours", 24),
            )

        learning = raw.get("learning", {})
        if learning:
            s.learning = LearningConfig(
                enabled=learning.get("enabled", False),
                min_observations=learning.get("min_observations", 5),
                judge_count=learning.get("judge_count", 3),
                judge_agreement=learning.get("judge_agreement", 2),
                monitoring_days=learning.get("monitoring_days", 30),
                llm_provider=learning.get("llm_provider", ""),
                llm_api_key=learning.get("llm_api_key", ""),
                llm_model=learning.get("llm_model", ""),
                daily_cost_limit=learning.get("daily_cost_limit", 5.0),
                claude_api_key=learning.get("claude_api_key", ""),
                openai_api_key=learning.get("openai_api_key", ""),
                gemini_api_key=learning.get("gemini_api_key", ""),
                researcher_provider=learning.get("researcher_provider", ""),
                judge_provider=learning.get("judge_provider", ""),
            )

        rag = raw.get("rag", {})
        if rag:
            s.rag = RagConfig(
                enable_web_fetch=rag.get("enable_web_fetch", False),
                enable_embeddings=rag.get("enable_embeddings", False),
                cache_hours=rag.get("cache_hours", 24),
                max_docs_in_prompt=rag.get("max_docs_in_prompt", 5),
                default_version=rag.get("default_version", "19c"),
                validate_sql=rag.get("validate_sql", True),
            )

        # v5.1b: Notification adapters
        notifications = raw.get("notifications", {})
        if notifications:
            adapter_list = []
            for ac in notifications.get("adapters", []):
                adapter_list.append(
                    NotificationAdapterConfig(
                        type=ac.get("type", ""),
                        enabled=ac.get("enabled", False),
                        url=ac.get("url", ""),
                        routing_key=ac.get("routing_key", ""),
                        headers=ac.get("headers", {}),
                    )
                )
            s.notifications = NotificationsConfig(adapters=adapter_list)

        return s

    def _apply_env_vars(self) -> None:
        """Override settings with SENTRI_* environment variables."""
        self.email.password = os.environ.get("SENTRI_EMAIL_PASSWORD", self.email.password)
        self.approvals.slack_webhook_url = os.environ.get(
            "SENTRI_SLACK_WEBHOOK_URL", self.approvals.slack_webhook_url
        )
        self.monitoring.log_level = os.environ.get("SENTRI_LOG_LEVEL", self.monitoring.log_level)

        # LLM API keys (per-provider)
        self.learning.claude_api_key = os.environ.get(
            "SENTRI_CLAUDE_API_KEY", self.learning.claude_api_key
        )
        self.learning.openai_api_key = os.environ.get(
            "SENTRI_OPENAI_API_KEY", self.learning.openai_api_key
        )
        self.learning.gemini_api_key = os.environ.get(
            "SENTRI_GEMINI_API_KEY", self.learning.gemini_api_key
        )
        # Legacy single key (backward compat)
        self.learning.llm_api_key = os.environ.get("SENTRI_LLM_API_KEY", self.learning.llm_api_key)

        # Database credentials: SENTRI_DB_<NAME>_PASSWORD / _USERNAME
        for db in self.databases:
            name_key = db.name.upper().replace("-", "_")
            db.password = os.environ.get(f"SENTRI_DB_{name_key}_PASSWORD", db.password)
            db.username = os.environ.get(f"SENTRI_DB_{name_key}_USERNAME", db.username)

    def get_database(self, name: str) -> Optional[DatabaseConfig]:
        """Find database config by exact name."""
        for db in self.databases:
            if db.name == name:
                return db
        return None

    def resolve_database(self, name: str) -> Optional[DatabaseConfig]:
        """Find database config by name OR alias (for email matching).

        Tries exact name match first, then case-insensitive alias match.
        """
        for db in self.databases:
            if db.name == name:
                return db
        name_lower = name.lower()
        for db in self.databases:
            if name_lower in [a.lower() for a in db.aliases]:
                return db
        return None

    def to_yaml(self) -> str:
        """Serialize settings to YAML (without secrets)."""
        data = {
            "email": {
                "imap_server": self.email.imap_server,
                "imap_port": self.email.imap_port,
                "username": self.email.username,
                "use_ssl": self.email.use_ssl,
                "# password": "Set via SENTRI_EMAIL_PASSWORD env var",
            },
            "databases": [
                {
                    "name": db.name,
                    "connection_string": db.connection_string,
                    "environment": db.environment,
                    "# password": f"Set via SENTRI_DB_{db.name.upper().replace('-', '_')}_PASSWORD env var",
                }
                for db in self.databases
            ],
            "approvals": {
                "slack_webhook_url": self.approvals.slack_webhook_url
                or "# Set via SENTRI_SLACK_WEBHOOK_URL env var",
                "approval_timeout": self.approvals.approval_timeout,
            },
            "monitoring": {
                "log_level": self.monitoring.log_level,
                "scout_poll_interval": self.monitoring.scout_poll_interval,
                "orchestrator_poll_interval": self.monitoring.orchestrator_poll_interval,
            },
        }
        return yaml.dump(data, default_flow_style=False, sort_keys=False)
