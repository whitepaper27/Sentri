"""Evaluation harness — bootstrap Sentri, reset state, create workflows.

Reuses the exact bootstrap pattern from demo_cmd.py:302-425.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import oracledb

from sentri.agents.base import AgentContext
from sentri.agents.executor import ExecutorAgent
from sentri.agents.researcher import ResearcherAgent
from sentri.agents.rca_agent import RCAAgent
from sentri.agents.sql_tuning_agent import SQLTuningAgent
from sentri.agents.storage_agent import StorageAgent
from sentri.config.paths import DB_PATH, INVESTIGATIONS_DIR, SENTRI_HOME
from sentri.config.settings import Settings
from sentri.core.constants import WorkflowStatus
from sentri.core.llm_interface import LLMProvider, NoOpLLMProvider
from sentri.core.models import Suggestion, Workflow
from sentri.db.audit_repo import AuditRepository
from sentri.db.cache_repo import CacheRepository
from sentri.db.connection import Database
from sentri.db.environment_repo import EnvironmentRepository
from sentri.db.workflow_repo import WorkflowRepository
from sentri.llm.provider import create_llm_provider
from sentri.memory.investigation_store import InvestigationStore
from sentri.notifications.router import NotificationRouter
from sentri.orchestrator.safety_mesh import SafetyMesh
from sentri.policy.alert_patterns import AlertPatterns
from sentri.policy.environment_config import EnvironmentConfig
from sentri.policy.loader import PolicyLoader
from sentri.policy.rules_engine import RulesEngine

logger = logging.getLogger("sentri.eval.harness")


# ---------------------------------------------------------------------------
# Instrumented LLM Provider — wraps real provider to count calls/tokens
# ---------------------------------------------------------------------------

class InstrumentedLLMProvider(LLMProvider):
    """Wraps a real LLM provider to track call count and token estimates."""

    def __init__(self, inner: LLMProvider):
        self._inner = inner
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @property
    def name(self) -> str:
        return f"instrumented({self._inner.name})"

    def is_available(self) -> bool:
        return self._inner.is_available()

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2048,
        **kwargs,
    ) -> str:
        self.call_count += 1
        # Rough token estimate: ~4 chars per token
        self.total_input_tokens += (len(prompt) + len(system_prompt)) // 4
        result = self._inner.generate(
            prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        self.total_output_tokens += len(result) // 4
        return result

    def generate_with_tools(self, messages, tools=None, system_prompt="", **kwargs):
        """Delegate tool-calling to inner provider."""
        self.call_count += 1
        # Estimate input tokens from messages list
        msg_text = " ".join(m.get("content", "") if isinstance(m, dict) else str(m) for m in (messages or []))
        self.total_input_tokens += (len(msg_text) + len(system_prompt)) // 4
        result = self._inner.generate_with_tools(
            messages, tools=tools, system_prompt=system_prompt, **kwargs
        )
        # Result is a ToolCallingResponse; estimate output from content
        if hasattr(result, "content") and result.content:
            self.total_output_tokens += len(result.content) // 4
        return result

    def reset_metrics(self):
        self.call_count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_sentri(
    mode: str = "full",
    provider_override: str = "",
    api_key_override: str = "",
    disabled_checks: Optional[set] = None,
    argue_mode: str = "full",
) -> tuple[AgentContext, dict, SafetyMesh, Optional[InstrumentedLLMProvider]]:
    """Initialize all Sentri infrastructure and agents.

    Args:
        mode: "full" for real LLM, "template" for NoOpLLMProvider.
        provider_override: If set, overrides sentri.yaml's llm_provider ("claude"/"openai"/"gemini").
        api_key_override: API key for the overridden provider. Required when provider_override is set.
        disabled_checks: Set of SafetyMesh check names to skip (ablation study use only).
            Valid names: policy_gate, conflict_detect, blast_radius, circuit_breaker, rollback_check
        argue_mode: Argue/select depth for specialist agents (ablation study use only).
            "full" = normal cost-gated (default), "single" = skip judge, "force_full" = always judge.

    Returns:
        (context, agents_dict, safety_mesh, instrumented_llm_or_None)
    """
    settings = Settings.load()

    # Override sentri.yaml LLM settings so eval scripts can specify the provider
    # directly (e.g. ANTHROPIC_API_KEY) regardless of what is configured in sentri.yaml.
    if provider_override and api_key_override:
        settings.learning.llm_provider = provider_override
        key_field = {"claude": "claude_api_key", "openai": "openai_api_key", "gemini": "gemini_api_key"}
        field = key_field.get(provider_override)
        if field:
            setattr(settings.learning, field, api_key_override)
            logger.info("bootstrap_sentri: provider overridden to %s", provider_override)

    db = Database(DB_PATH)
    db.initialize_schema()

    workflow_repo = WorkflowRepository(db)
    audit_repo = AuditRepository(db)
    environment_repo = EnvironmentRepository(db)

    # Sync environment registry
    from sentri.cli.start_cmd import _sync_environment_registry

    _sync_environment_registry(settings, environment_repo)

    policy_loader = PolicyLoader(SENTRI_HOME)

    context = AgentContext(
        db=db,
        workflow_repo=workflow_repo,
        audit_repo=audit_repo,
        environment_repo=environment_repo,
        policy_loader=policy_loader,
        settings=settings,
    )

    # Profile database
    from sentri.agents.profiler import ProfilerAgent

    profiler = ProfilerAgent(context)
    try:
        profiles = profiler.profile_all()
        for db_id, p in profiles.items():
            logger.info("Profiled %s: size=%.1fGB, OMF=%s", db_id, p.db_size_gb, p.omf_enabled)
    except Exception as e:
        logger.warning("Profile warning: %s", e)

    # LLM setup
    instrumented_llm: Optional[InstrumentedLLMProvider] = None

    if mode == "full":
        learning_cfg = settings.learning
        researcher_pname = learning_cfg.get_researcher_provider()
        real_llm = create_llm_provider(
            provider_name=researcher_pname,
            api_key=learning_cfg.get_api_key(researcher_pname),
            model=learning_cfg.llm_model,
        )
        if real_llm.is_available():
            instrumented_llm = InstrumentedLLMProvider(real_llm)
            researcher_llm: LLMProvider = instrumented_llm
            logger.info("LLM: %s (instrumented)", real_llm.name)
        else:
            researcher_llm = NoOpLLMProvider()
            logger.warning("LLM not available — falling back to template")
    else:
        researcher_llm = NoOpLLMProvider()
        logger.info("Template mode — using NoOpLLMProvider")

    cost_tracker = None
    if instrumented_llm and instrumented_llm.is_available():
        cache_repo = CacheRepository(db)
        from sentri.llm.cost_tracker import CostTracker

        cost_tracker = CostTracker(cache_repo, settings.learning.daily_cost_limit)

    # Create agents
    executor = ExecutorAgent(context)
    researcher = ResearcherAgent(context, llm_provider=researcher_llm, cost_tracker=cost_tracker)

    from sentri.agents.analyst import AnalystAgent

    analyst = AnalystAgent(context, llm_provider=researcher_llm, judge_providers=[])

    # Safety Mesh (ablation: disabled_checks skips specified checks)
    alert_patterns = AlertPatterns(policy_loader)
    rules_engine = RulesEngine(policy_loader)
    environment_config = EnvironmentConfig(policy_loader)
    safety_mesh = SafetyMesh(
        rules_engine=rules_engine,
        db=db,
        workflow_repo=workflow_repo,
        audit_repo=audit_repo,
        alert_patterns=alert_patterns,
        environment_config=environment_config,
        disabled_checks=disabled_checks,
    )

    # Investigation store + notification router
    investigation_store = InvestigationStore(INVESTIGATIONS_DIR)
    notification_router = NotificationRouter()

    # Build all 3 specialist agents (ablation: argue_mode controls judge depth)
    storage_agent = StorageAgent(
        context,
        safety_mesh=safety_mesh,
        researcher=researcher,
        executor=executor,
        analyst=analyst,
        llm_provider=researcher_llm,
        cost_tracker=cost_tracker,
        investigation_store=investigation_store,
        notification_router=notification_router,
        argue_mode=argue_mode,
    )

    sql_tuning_agent = SQLTuningAgent(
        context,
        safety_mesh=safety_mesh,
        llm_provider=researcher_llm,
        cost_tracker=cost_tracker,
        investigation_store=investigation_store,
        notification_router=notification_router,
        argue_mode=argue_mode,
    )

    rca_agent = RCAAgent(
        context,
        safety_mesh=safety_mesh,
        llm_provider=researcher_llm,
        cost_tracker=cost_tracker,
        investigation_store=investigation_store,
        notification_router=notification_router,
        argue_mode=argue_mode,
    )

    agents = {
        "storage_agent": storage_agent,
        "sql_tuning_agent": sql_tuning_agent,
        "rca_agent": rca_agent,
    }

    return context, agents, safety_mesh, instrumented_llm


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def reset_state(context: AgentContext, alert_type: str, database_id: str):
    """Delete workflows + audit_log + learning_observations for this alert/db combo.

    Clears circuit breaker + repeat alert rule so each run starts fresh.
    """
    db = context.db
    wf_subquery = "(SELECT id FROM workflows WHERE database_id = ? AND alert_type = ?)"
    params = (database_id, alert_type)

    db.execute_write(f"DELETE FROM audit_log WHERE workflow_id IN {wf_subquery}", params)
    db.execute_write(
        f"DELETE FROM learning_observations WHERE workflow_id IN {wf_subquery}", params
    )
    db.execute_write(
        "DELETE FROM workflows WHERE database_id = ? AND alert_type = ?", params
    )


def create_workflow(
    context: AgentContext,
    alert_type: str,
    database_id: str,
    environment: str,
    extracted_data: dict,
) -> str:
    """Create a workflow in DETECTED status with suggestion. Returns workflow_id."""
    subject = f"[EVAL] {alert_type} on {database_id}"
    body = f"Evaluation run: {alert_type} alert for {database_id}"

    suggestion = Suggestion(
        alert_type=alert_type,
        database_id=database_id,
        raw_email_subject=subject,
        raw_email_body=body,
        extracted_data=extracted_data,
    )

    workflow = Workflow(
        alert_type=alert_type,
        database_id=database_id,
        environment=environment,
        status=WorkflowStatus.DETECTED.value,
        suggestion=suggestion.to_json(),
    )

    workflow_id = context.workflow_repo.create(workflow)
    return workflow_id


# ---------------------------------------------------------------------------
# P3: Blocking chain helpers
# ---------------------------------------------------------------------------

def setup_blocking_chain(dsn: str, user: str, password: str) -> tuple:
    """Create a real blocking chain: conn_a holds a lock, conn_b is blocked.

    Returns (conn_a, conn_b, thread, blocker_sid).
    """
    conn_a = oracledb.connect(user=user, password=password, dsn=dsn)
    conn_a.autocommit = False

    # conn_a takes a row lock
    cursor_a = conn_a.cursor()
    cursor_a.execute("UPDATE sentri_demo.block_test SET val = 'locked_by_a' WHERE id = 1")

    # Get blocker SID
    cursor_a.execute("SELECT SYS_CONTEXT('USERENV', 'SID') FROM dual")
    blocker_sid = cursor_a.fetchone()[0]

    # conn_b tries to update the same row (will block)
    conn_b = oracledb.connect(user=user, password=password, dsn=dsn)
    conn_b.autocommit = False

    blocked_event = threading.Event()

    def _blocked_update():
        try:
            cursor_b = conn_b.cursor()
            cursor_b.execute("UPDATE sentri_demo.block_test SET val = 'locked_by_b' WHERE id = 1")
        except Exception:
            pass
        finally:
            blocked_event.set()

    thread = threading.Thread(target=_blocked_update, daemon=True)
    thread.start()

    # Give it a moment to enter blocked state
    time.sleep(2)

    return conn_a, conn_b, thread, str(blocker_sid)


def teardown_blocking_chain(conn_a, conn_b, thread):
    """Rollback both connections and clean up."""
    try:
        conn_a.rollback()
        conn_a.close()
    except Exception:
        pass
    try:
        conn_b.rollback()
        conn_b.close()
    except Exception:
        pass
    try:
        thread.join(timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Oracle connectivity check
# ---------------------------------------------------------------------------

def check_oracle_connection(settings: Settings) -> tuple[bool, str]:
    """Test Oracle connectivity. Returns (success, message)."""
    if not settings.databases:
        return False, "No databases configured in sentri.yaml"

    db_cfg = settings.databases[0]
    # Parse DSN from connection_string: oracle://user@host:port/service
    conn_str = db_cfg.connection_string
    if conn_str.startswith("oracle://"):
        conn_str = conn_str[len("oracle://"):]
    # Extract user and DSN
    if "@" in conn_str:
        user_part, dsn = conn_str.split("@", 1)
    else:
        user_part = db_cfg.username or "system"
        dsn = conn_str

    password = db_cfg.password
    user = db_cfg.username or user_part

    try:
        conn = oracledb.connect(user=user, password=password, dsn=dsn)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM dual")
        conn.close()
        return True, f"Connected to {db_cfg.name} ({dsn})"
    except Exception as e:
        return False, f"Failed to connect to {db_cfg.name}: {e}"
