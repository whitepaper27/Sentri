"""Shared test fixtures for Sentri tests."""

from pathlib import Path

import pytest

from sentri.agents.base import AgentContext
from sentri.config.settings import DatabaseConfig, Settings
from sentri.core.models import EnvironmentRecord
from sentri.db.audit_repo import AuditRepository
from sentri.db.cache_repo import CacheRepository
from sentri.db.connection import Database
from sentri.db.environment_repo import EnvironmentRepository
from sentri.db.workflow_repo import WorkflowRepository
from sentri.policy.loader import PolicyLoader


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database with schema initialized."""
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.initialize_schema()
    yield db
    db.close()


@pytest.fixture
def workflow_repo(tmp_db):
    return WorkflowRepository(tmp_db)


@pytest.fixture
def audit_repo(tmp_db):
    return AuditRepository(tmp_db)


@pytest.fixture
def environment_repo(tmp_db):
    return EnvironmentRepository(tmp_db)


@pytest.fixture
def cache_repo(tmp_db):
    return CacheRepository(tmp_db)


@pytest.fixture
def policy_loader():
    """PolicyLoader pointed at the bundled default policies."""
    policies_path = Path(__file__).parent.parent / "src" / "sentri" / "_default_policies"
    return PolicyLoader(policies_path)


@pytest.fixture
def settings():
    """Default settings for testing (with databases matching test environments)."""
    s = Settings()
    s.databases = [
        DatabaseConfig(
            name="DEV-DB-01",
            connection_string="oracle://sentri_agent@dev-db-01:1521/DEVDB",
            environment="DEV",
            username="sentri_admin",
            aliases=["DEVDB", "dev-db-01"],
        ),
        DatabaseConfig(
            name="UAT-DB-03",
            connection_string="oracle://sentri_agent@uat-db-03:1521/UATDB",
            environment="UAT",
            aliases=["UATDB", "uat-db-03"],
        ),
        DatabaseConfig(
            name="PROD-DB-07",
            connection_string="oracle://sentri_agent@prod-scan:1521/PRODDB",
            environment="PROD",
            username="sentri_ro",
            aliases=["PRODDB", "prod-db-07", "PROD07"],
        ),
    ]
    return s


@pytest.fixture
def agent_context(tmp_db, workflow_repo, audit_repo, environment_repo, policy_loader, settings):
    """Complete AgentContext for testing agents."""
    # Register test environments
    for db_id, name, env, conn in [
        ("DEV-DB-01", "DEVDB", "DEV", "oracle://sentri_agent@dev-db-01:1521/DEVDB"),
        ("UAT-DB-03", "UATDB", "UAT", "oracle://sentri_agent@uat-db-03:1521/UATDB"),
        ("PROD-DB-07", "PRODDB", "PROD", "oracle://sentri_agent@prod-scan:1521/PRODDB"),
    ]:
        environment_repo.upsert(
            EnvironmentRecord(
                database_id=db_id,
                database_name=name,
                environment=env,
                connection_string=conn,
            )
        )

    return AgentContext(
        db=tmp_db,
        workflow_repo=workflow_repo,
        audit_repo=audit_repo,
        environment_repo=environment_repo,
        policy_loader=policy_loader,
        settings=settings,
    )
