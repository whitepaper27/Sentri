"""sentri start - Start the Sentri daemon."""

from __future__ import annotations

import signal
import sys
import threading

import click
from rich.console import Console

console = Console()


@click.command("start")
@click.option("--foreground", is_flag=True, default=True, help="Run in foreground")
def start_cmd(foreground: bool):
    """Start the Sentri monitoring daemon."""
    from sentri.agents.base import AgentContext
    from sentri.agents.executor import ExecutorAgent
    from sentri.agents.scout import ScoutAgent
    from sentri.config.paths import DB_PATH, LOG_PATH, SENTRI_HOME
    from sentri.config.settings import Settings
    from sentri.db.audit_repo import AuditRepository
    from sentri.db.cache_repo import CacheRepository
    from sentri.db.connection import Database
    from sentri.db.environment_repo import EnvironmentRepository
    from sentri.db.workflow_repo import WorkflowRepository
    from sentri.logging_config import setup_logging
    from sentri.policy.loader import PolicyLoader

    # Check initialization
    if not SENTRI_HOME.exists():
        console.print("[red]Sentri not initialized. Run 'sentri init' first.[/red]")
        sys.exit(1)

    # Load settings
    settings = Settings.load()
    setup_logging(LOG_PATH, settings.monitoring.log_level)

    console.print("[bold blue]Sentri[/bold blue] - Starting daemon...\n")

    # Initialize database
    db = Database(DB_PATH)
    db.initialize_schema()

    # Create repositories
    workflow_repo = WorkflowRepository(db)
    audit_repo = AuditRepository(db)
    environment_repo = EnvironmentRepository(db)

    # Sync environment registry from YAML config (source of truth)
    _sync_environment_registry(settings, environment_repo)
    console.print("[green]Synced %d database(s)[/green] from config" % len(settings.databases))

    # Create policy loader
    policy_loader = PolicyLoader(SENTRI_HOME)

    # Create shared context
    context = AgentContext(
        db=db,
        workflow_repo=workflow_repo,
        audit_repo=audit_repo,
        environment_repo=environment_repo,
        policy_loader=policy_loader,
        settings=settings,
    )

    # Run database profiler in background (Agent 0)
    from sentri.agents.profiler import ProfilerAgent

    profiler = ProfilerAgent(context)

    def _run_profiler():
        import logging as _logging
        import time as _time

        _log = _logging.getLogger("sentri.agents.profiler")

        # Initial full profile
        try:
            profiles = profiler.profile_all()
            for db_id, p in profiles.items():
                console.print(
                    "[cyan]Profiled %s[/cyan]: type=%s, size=%.1fGB, OMF=%s"
                    % (db_id, p.db_type, p.db_size_gb, p.omf_enabled)
                )
        except Exception as e:
            console.print("[yellow]Profiler warning:[/yellow] %s" % e)

        # Scheduled re-profiling (runs in same daemon thread)
        refresh_hours = settings.monitoring.profile_refresh_hours
        if refresh_hours > 0:
            while True:
                _time.sleep(refresh_hours * 3600)
                try:
                    _log.info("Scheduled profile refresh starting...")
                    profiler.profile_all()
                    _log.info("Scheduled profile refresh complete")
                except Exception as e:
                    _log.warning("Scheduled profile refresh failed: %s", e)

    profiler_thread = threading.Thread(target=_run_profiler, daemon=True, name="profiler-agent")
    profiler_thread.start()

    # Create LLM providers (multi-provider architecture)
    from sentri.agents.researcher import ResearcherAgent
    from sentri.llm.provider import create_llm_provider

    learning_cfg = settings.learning

    # Researcher LLM (role-specific or default)
    researcher_pname = learning_cfg.get_researcher_provider()
    researcher_llm = create_llm_provider(
        provider_name=researcher_pname,
        api_key=learning_cfg.get_api_key(researcher_pname),
        model=learning_cfg.llm_model,
    )

    # Cost tracker (shared across all providers)
    cost_tracker = None
    if researcher_llm.is_available():
        from sentri.llm.cost_tracker import CostTracker

        cache_repo = CacheRepository(db)
        cost_tracker = CostTracker(cache_repo, learning_cfg.daily_cost_limit)
        console.print(
            "[green]Researcher LLM:[/green] %s (budget: $%.2f/day)"
            % (researcher_llm.name, learning_cfg.daily_cost_limit)
        )
    else:
        console.print("[dim]Researcher LLM: not configured — using template fallback[/dim]")

    # Judge LLMs: build list of available providers for diverse consensus
    judge_providers = []
    judge_cfg = learning_cfg.get_judge_provider()

    if judge_cfg.lower() == "diverse":
        # Use all available providers for true diverse consensus.
        # Check provider-specific keys directly (don't fall back to legacy
        # llm_api_key — that could create e.g. ClaudeProvider with a Gemini key).
        _provider_keys = {
            "claude": learning_cfg.claude_api_key,
            "openai": learning_cfg.openai_api_key,
            "gemini": learning_cfg.gemini_api_key,
        }
        for pname, key in _provider_keys.items():
            if key:
                p = create_llm_provider(pname, key, learning_cfg.llm_model)
                if p.is_available():
                    judge_providers.append(p)
        if judge_providers:
            names = [p.name for p in judge_providers]
            console.print("[green]Judge panel:[/green] diverse (%s)" % ", ".join(names))
    elif judge_cfg:
        # Use a specific provider for all judges
        jp = create_llm_provider(
            judge_cfg, learning_cfg.get_api_key(judge_cfg), learning_cfg.llm_model
        )
        if jp.is_available():
            judge_providers = [jp]
            console.print("[green]Judge panel:[/green] %s" % jp.name)

    if not judge_providers and researcher_llm.is_available():
        # Fall back to using the researcher's provider for judging
        judge_providers = [researcher_llm]

    if not judge_providers:
        console.print("[dim]Judge panel: not configured — human review required[/dim]")

    # Create agents
    scout = ScoutAgent(context)
    executor = ExecutorAgent(context)
    researcher = ResearcherAgent(context, llm_provider=researcher_llm, cost_tracker=cost_tracker)

    from sentri.agents.analyst import AnalystAgent

    analyst = AnalystAgent(
        context,
        llm_provider=researcher_llm,
        judge_providers=judge_providers,
    )
    if learning_cfg.enabled:
        console.print("[green]Learning engine:[/green] enabled")
    else:
        console.print("[dim]Learning: disabled (observations only)[/dim]")

    # -- v5.0: Safety Mesh + Supervisor + Specialist Agents --
    from sentri.agents.proactive_agent import ProactiveAgent
    from sentri.agents.rca_agent import RCAAgent
    from sentri.agents.sql_tuning_agent import SQLTuningAgent
    from sentri.agents.storage_agent import StorageAgent
    from sentri.orchestrator.safety_mesh import SafetyMesh
    from sentri.orchestrator.supervisor import Supervisor
    from sentri.policy.alert_patterns import AlertPatterns

    # Create Safety Mesh (structural enforcement for all agent actions)
    from sentri.policy.environment_config import EnvironmentConfig
    from sentri.policy.rules_engine import RulesEngine

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
    )
    console.print(
        "[green]Safety Mesh:[/green] 5-check structural enforcement active (policy-driven)"
    )

    # Create InvestigationStore (persist agent analysis as .md files)
    from sentri.config.paths import INVESTIGATIONS_DIR
    from sentri.memory.investigation_store import InvestigationStore

    investigation_store = InvestigationStore(INVESTIGATIONS_DIR)
    console.print("[green]Investigation Store:[/green] %s" % INVESTIGATIONS_DIR)

    # Create NotificationRouter (v5.1b — dispatches to all configured adapters)
    from sentri.notifications.router import NotificationRouter

    notification_router = NotificationRouter.from_settings(settings)
    console.print(
        "[green]Notifications:[/green] %d adapter(s) configured" % notification_router.adapter_count
    )

    # Create specialist agents
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
    )

    sql_tuning_agent = SQLTuningAgent(
        context,
        safety_mesh=safety_mesh,
        llm_provider=researcher_llm,
        cost_tracker=cost_tracker,
        investigation_store=investigation_store,
        notification_router=notification_router,
    )

    rca_agent = RCAAgent(
        context,
        safety_mesh=safety_mesh,
        llm_provider=researcher_llm,
        cost_tracker=cost_tracker,
        investigation_store=investigation_store,
        notification_router=notification_router,
    )

    # Create Supervisor (deterministic router — replaces Orchestrator)
    supervisor = Supervisor(
        context,
        scout.alert_event,
        notification_router=notification_router,
    )
    supervisor.register_agent("storage_agent", storage_agent)
    supervisor.register_agent("sql_tuning_agent", sql_tuning_agent)
    supervisor.register_agent("rca_agent", rca_agent)
    console.print("[green]Supervisor:[/green] 3 specialist agents registered")

    # Create ProactiveAgent (scheduled health checks)
    proactive_agent = ProactiveAgent(context, alert_event=scout.alert_event)

    # Graceful shutdown handler
    def shutdown(signum, frame):
        console.print("\n[yellow]Shutting down...[/yellow]")
        scout.stop()
        supervisor.stop()
        proactive_agent.stop()

    signal.signal(signal.SIGINT, shutdown)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, shutdown)

    # Start Scout in background thread
    scout_thread = threading.Thread(
        target=scout.run_loop,
        kwargs={"poll_interval": settings.monitoring.scout_poll_interval},
        daemon=True,
        name="scout-agent",
    )
    scout_thread.start()
    console.print(
        "[green]Scout agent started[/green] (polling every %ds)"
        % settings.monitoring.scout_poll_interval
    )

    # Start ProactiveAgent in background thread
    proactive_thread = threading.Thread(
        target=proactive_agent.run_loop,
        kwargs={"poll_interval": 300},
        daemon=True,
        name="proactive-agent",
    )
    proactive_thread.start()
    console.print("[green]Proactive agent started[/green] (polling every 300s)")

    console.print(
        "[green]Supervisor starting[/green] (polling every %ds)"
        % settings.monitoring.orchestrator_poll_interval
    )
    console.print("\nPress Ctrl+C to stop.\n")

    # Run Supervisor in main thread
    try:
        supervisor.run(poll_interval=settings.monitoring.orchestrator_poll_interval)
    except KeyboardInterrupt:
        pass

    # Clean shutdown
    scout.stop()
    supervisor.stop()
    proactive_agent.stop()
    scout_thread.join(timeout=5)
    proactive_thread.join(timeout=5)
    db.close()

    console.print("[bold green]Sentri stopped gracefully.[/bold green]")


def _sync_environment_registry(settings, environment_repo):
    """Populate environment_registry from YAML config (source of truth).

    Called on every startup so YAML changes are reflected immediately.
    """
    from sentri.core.constants import ENVIRONMENT_AUTONOMY, Environment
    from sentri.core.models import EnvironmentRecord

    for db_cfg in settings.databases:
        autonomy = db_cfg.autonomy_level
        if not autonomy:
            try:
                autonomy = ENVIRONMENT_AUTONOMY.get(Environment(db_cfg.environment), "SUPERVISED")
            except ValueError:
                autonomy = "SUPERVISED"

        environment_repo.upsert(
            EnvironmentRecord(
                database_id=db_cfg.name,
                database_name=db_cfg.name,
                environment=db_cfg.environment,
                connection_string=db_cfg.connection_string,
                oracle_version=db_cfg.oracle_version or None,
                architecture=db_cfg.architecture or None,
                autonomy_level=autonomy,
                critical_schemas=db_cfg.critical_schemas or None,
                business_owner=db_cfg.business_owner or None,
                dba_owner=db_cfg.dba_owner or None,
            )
        )
