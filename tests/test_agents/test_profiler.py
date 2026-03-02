"""Tests for profiler datetime/Decimal serialization fix."""

import json
from datetime import datetime
from decimal import Decimal

from sentri.core.models import DatabaseProfile


class TestDatabaseProfileSerialization:
    """Verify DatabaseProfile.to_json() handles Oracle types."""

    def test_datetime_in_db_config(self):
        """Oracle DATE/TIMESTAMP columns come back as datetime objects."""
        profile = DatabaseProfile(
            database_id="test-db",
            db_config={
                "instance_info": [
                    {
                        "instance_name": "ORCL",
                        "startup_time": datetime(2026, 2, 15, 8, 30, 0),
                        "host_name": "dbhost01",
                    }
                ],
                "db_identity": [
                    {
                        "name": "TESTDB",
                        "created": datetime(2025, 1, 1, 12, 0, 0),
                    }
                ],
            },
        )
        json_str = profile.to_json()
        parsed = json.loads(json_str)

        assert parsed["db_config"]["instance_info"][0]["startup_time"] == "2026-02-15T08:30:00"
        assert parsed["db_config"]["db_identity"][0]["created"] == "2025-01-01T12:00:00"

    def test_decimal_in_db_config(self):
        """Oracle NUMBER columns come back as Decimal objects."""
        profile = DatabaseProfile(
            database_id="test-db",
            db_config={
                "db_size": [{"total_gb": Decimal("45.67")}],
                "sga_info": [
                    {"name": "Fixed Size", "size_mb": Decimal("2.5")},
                ],
            },
        )
        json_str = profile.to_json()
        parsed = json.loads(json_str)

        assert parsed["db_config"]["db_size"][0]["total_gb"] == 45.67
        assert parsed["db_config"]["sga_info"][0]["size_mb"] == 2.5

    def test_mixed_oracle_types(self):
        """Real-world mix of datetime + Decimal + str + int."""
        profile = DatabaseProfile(
            database_id="prod-db",
            db_config={
                "instance_info": [
                    {
                        "instance_name": "PROD",
                        "startup_time": datetime(2026, 2, 10, 6, 0, 0),
                        "version": "19.22.0.0.0",
                        "status": "OPEN",
                    }
                ],
                "datafiles": [
                    {
                        "file_name": "/opt/oracle/oradata/users01.dbf",
                        "size_mb": Decimal("500"),
                        "max_mb": Decimal("32768"),
                        "autoextensible": "YES",
                    }
                ],
            },
        )
        json_str = profile.to_json()
        parsed = json.loads(json_str)

        assert parsed["db_config"]["instance_info"][0]["startup_time"] == "2026-02-10T06:00:00"
        assert parsed["db_config"]["datafiles"][0]["size_mb"] == 500.0

    def test_roundtrip_without_oracle_types(self):
        """Profile without Oracle types still round-trips correctly."""
        profile = DatabaseProfile(
            database_id="test-db",
            db_type="OLTP",
            db_size_gb=10.5,
            omf_enabled=True,
        )
        json_str = profile.to_json()
        restored = DatabaseProfile.from_json(json_str)

        assert restored.database_id == "test-db"
        assert restored.db_type == "OLTP"
        assert restored.db_size_gb == 10.5
        assert restored.omf_enabled is True
