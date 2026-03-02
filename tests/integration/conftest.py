"""Integration test fixtures — real Oracle database via Docker.

Oracle config from sentri.yaml:
  name: sentri-dev
  connection_string: oracle://system@localhost:1521/FREEPDB1
  username: system
  password: Oracle123
  environment: DEV
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip all integration tests if Oracle is not reachable
# ---------------------------------------------------------------------------


def _oracle_available() -> bool:
    try:
        sock = socket.create_connection(("localhost", 1521), timeout=3)
        sock.close()
        return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


pytestmark = pytest.mark.skipif(
    not _oracle_available(),
    reason="Docker Oracle not available on localhost:1521",
)

# ---------------------------------------------------------------------------
# Oracle connection constants (match sentri.yaml)
# ---------------------------------------------------------------------------

ORACLE_HOST = "localhost"
ORACLE_PORT = 1521
ORACLE_SERVICE = "FREEPDB1"
ORACLE_USER = "system"
ORACLE_PASSWORD = "Oracle123"
ORACLE_DSN = f"{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}"
ORACLE_CONN_STRING = f"oracle://{ORACLE_USER}@{ORACLE_HOST}:{ORACLE_PORT}/{ORACLE_SERVICE}"

# Sentri database config name (must match what Scout extracts from email)
TEST_DB_NAME = "sentri-dev"
TEST_DB_ENV = "DEV"


# ---------------------------------------------------------------------------
# Session-scoped raw Oracle connection (for test verification SQL)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def oracle_conn():
    """Raw oracledb connection for running verification queries."""
    import oracledb

    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)
    yield conn
    conn.close()


def run_oracle(oracle_conn, sql, params=None, commit=False):
    """Execute SQL on Docker Oracle and return rows as list[dict]."""
    cursor = oracle_conn.cursor()
    if params:
        cursor.execute(sql, params)
    else:
        cursor.execute(sql)
    if cursor.description:
        cols = [c[0].lower() for c in cursor.description]
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
        cursor.close()
        return rows
    cursor.close()
    if commit:
        oracle_conn.commit()
    return []


def run_oracle_ddl(oracle_conn, sql):
    """Execute DDL on Docker Oracle (no result set)."""
    cursor = oracle_conn.cursor()
    cursor.execute(sql)
    cursor.close()


# ---------------------------------------------------------------------------
# Per-test SQLite database (same pattern as unit tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def int_db(tmp_path):
    from sentri.db.connection import Database

    db = Database(tmp_path / "int_test.db")
    db.initialize_schema()
    yield db
    db.close()


@pytest.fixture
def int_workflow_repo(int_db):
    from sentri.db.workflow_repo import WorkflowRepository

    return WorkflowRepository(int_db)


@pytest.fixture
def int_audit_repo(int_db):
    from sentri.db.audit_repo import AuditRepository

    return AuditRepository(int_db)


@pytest.fixture
def int_environment_repo(int_db):
    from sentri.db.environment_repo import EnvironmentRepository

    return EnvironmentRepository(int_db)


# ---------------------------------------------------------------------------
# Policy loader + Settings (real policies, real DB config)
# ---------------------------------------------------------------------------


@pytest.fixture
def int_policy_loader():
    from sentri.policy.loader import PolicyLoader

    policies_path = Path(__file__).parent.parent.parent / "src" / "sentri" / "_default_policies"
    return PolicyLoader(policies_path)


@pytest.fixture
def int_settings():
    from sentri.config.settings import DatabaseConfig, Settings

    s = Settings()
    s.databases = [
        DatabaseConfig(
            name=TEST_DB_NAME,
            connection_string=ORACLE_CONN_STRING,
            environment=TEST_DB_ENV,
            password=ORACLE_PASSWORD,
            username=ORACLE_USER,
            aliases=["sentri-dev", "FREEPDB1"],
        ),
    ]
    return s


# ---------------------------------------------------------------------------
# AgentContext wired to real Oracle
# ---------------------------------------------------------------------------


@pytest.fixture
def int_oracle_pool():
    from sentri.oracle.connection_pool import OracleConnectionPool

    pool = OracleConnectionPool()
    yield pool


@pytest.fixture
def int_context(
    int_db,
    int_workflow_repo,
    int_audit_repo,
    int_environment_repo,
    int_policy_loader,
    int_settings,
    int_oracle_pool,
):
    from sentri.agents.base import AgentContext
    from sentri.core.models import EnvironmentRecord

    int_environment_repo.upsert(
        EnvironmentRecord(
            database_id=TEST_DB_NAME,
            database_name="FREEPDB1",
            environment=TEST_DB_ENV,
            connection_string=ORACLE_CONN_STRING,
        )
    )

    return AgentContext(
        db=int_db,
        workflow_repo=int_workflow_repo,
        audit_repo=int_audit_repo,
        environment_repo=int_environment_repo,
        policy_loader=int_policy_loader,
        settings=int_settings,
        oracle_pool=int_oracle_pool,
    )


# ---------------------------------------------------------------------------
# Safety Mesh + Agents
# ---------------------------------------------------------------------------


@pytest.fixture
def int_safety_mesh(int_context):
    from sentri.orchestrator.safety_mesh import SafetyMesh
    from sentri.policy.alert_patterns import AlertPatterns
    from sentri.policy.rules_engine import RulesEngine

    rules = RulesEngine(int_context.policy_loader)
    alerts = AlertPatterns(int_context.policy_loader)
    return SafetyMesh(
        rules_engine=rules,
        db=int_context.db,
        workflow_repo=int_context.workflow_repo,
        audit_repo=int_context.audit_repo,
        alert_patterns=alerts,
    )


@pytest.fixture
def int_scout(int_context):
    from sentri.agents.scout import ScoutAgent

    scout = ScoutAgent(int_context)
    scout.load_patterns()
    return scout


@pytest.fixture
def int_auditor(int_context, int_oracle_pool):
    from sentri.agents.auditor import AuditorAgent

    return AuditorAgent(int_context, int_oracle_pool)


@pytest.fixture
def int_researcher(int_context):
    from sentri.agents.researcher import ResearcherAgent

    return ResearcherAgent(int_context)  # NoOp LLM → template fallback


@pytest.fixture
def int_executor(int_context, int_oracle_pool):
    from sentri.agents.executor import ExecutorAgent

    return ExecutorAgent(int_context, int_oracle_pool)


@pytest.fixture
def int_storage_agent(int_context, int_safety_mesh, int_auditor, int_researcher, int_executor):
    from sentri.agents.storage_agent import StorageAgent

    return StorageAgent(
        context=int_context,
        safety_mesh=int_safety_mesh,
        auditor=int_auditor,
        researcher=int_researcher,
        executor=int_executor,
    )


@pytest.fixture
def int_sql_tuning_agent(int_context, int_safety_mesh):
    from sentri.agents.sql_tuning_agent import SQLTuningAgent

    return SQLTuningAgent(context=int_context, safety_mesh=int_safety_mesh)


@pytest.fixture
def int_rca_agent(int_context, int_safety_mesh):
    from sentri.agents.rca_agent import RCAAgent

    return RCAAgent(context=int_context, safety_mesh=int_safety_mesh)


@pytest.fixture
def int_supervisor(int_context, int_storage_agent, int_sql_tuning_agent, int_rca_agent):
    import threading

    from sentri.orchestrator.supervisor import Supervisor

    alert_event = threading.Event()
    supervisor = Supervisor(int_context, alert_event)
    supervisor.register_agent("storage_agent", int_storage_agent)
    supervisor.register_agent("sql_tuning_agent", int_sql_tuning_agent)
    supervisor.register_agent("rca_agent", int_rca_agent)
    return supervisor
