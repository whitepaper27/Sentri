"""DDL schema for the Sentri SQLite database."""

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Workflows: central tracking for each alert
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    alert_type TEXT NOT NULL,
    database_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DETECTED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    suggestion TEXT,
    verification TEXT,
    execution_plan TEXT,
    execution_result TEXT,

    approved_by TEXT,
    approved_at TIMESTAMP,
    approval_timeout TIMESTAMP,

    metadata TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);
CREATE INDEX IF NOT EXISTS idx_workflows_database ON workflows(database_id);
CREATE INDEX IF NOT EXISTS idx_workflows_created ON workflows(created_at);

-- Audit log: append-only, immutable
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    action_type TEXT NOT NULL,
    action_sql TEXT,
    database_id TEXT NOT NULL,
    environment TEXT NOT NULL,

    executed_by TEXT NOT NULL,
    approved_by TEXT,

    result TEXT NOT NULL,
    error_message TEXT,

    evidence TEXT,
    change_ticket TEXT,

    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_database ON audit_log(database_id);
CREATE INDEX IF NOT EXISTS idx_audit_workflow ON audit_log(workflow_id);

-- Environment registry: database inventory
CREATE TABLE IF NOT EXISTS environment_registry (
    database_id TEXT PRIMARY KEY,
    database_name TEXT NOT NULL,
    environment TEXT NOT NULL,
    oracle_version TEXT,
    architecture TEXT,
    connection_string TEXT NOT NULL,

    autonomy_level TEXT,
    critical_schemas TEXT,

    business_owner TEXT,
    dba_owner TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_verified TIMESTAMP
);

-- Cache: generic key-value with TTL
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);

-- Resource locks: prevent concurrent operations on same target
CREATE TABLE IF NOT EXISTS locks (
    resource_key TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

-- Record initial schema version
INSERT OR IGNORE INTO schema_version (version) VALUES (1);
"""
