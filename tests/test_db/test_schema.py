"""Test database schema initialization."""


def test_schema_creates_tables(tmp_db):
    """Verify all expected tables exist after schema init."""
    rows = tmp_db.execute_read("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    table_names = {row["name"] for row in rows}

    assert "workflows" in table_names
    assert "audit_log" in table_names
    assert "environment_registry" in table_names
    assert "cache" in table_names
    assert "locks" in table_names
    assert "schema_version" in table_names


def test_schema_version(tmp_db):
    """Verify schema version is set."""
    row = tmp_db.execute_read_one("SELECT version FROM schema_version")
    assert row is not None
    assert row["version"] == 1


def test_schema_idempotent(tmp_db):
    """Calling initialize_schema twice should not fail."""
    tmp_db.initialize_schema()  # Already called in fixture, call again
    row = tmp_db.execute_read_one("SELECT MAX(version) as max_v FROM schema_version")
    assert row["max_v"] >= 1  # Base schema + any applied migrations
