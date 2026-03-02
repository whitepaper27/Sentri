"""Tests for SMTP configuration fields in Settings."""

from __future__ import annotations

from sentri.config.settings import ApprovalConfig, EmailConfig, Settings


class TestEmailConfigSMTP:
    """Test SMTP fields in EmailConfig."""

    def test_default_smtp_fields(self):
        """SMTP fields should have sensible defaults."""
        cfg = EmailConfig()
        assert cfg.smtp_server == ""
        assert cfg.smtp_port == 587
        assert cfg.use_tls is True

    def test_smtp_from_dict(self):
        """_from_dict should parse SMTP fields."""
        raw = {
            "email": {
                "imap_server": "imap.gmail.com",
                "imap_port": 993,
                "username": "test@gmail.com",
                "password": "secret",
                "use_ssl": True,
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 465,
                "use_tls": False,
            }
        }
        settings = Settings._from_dict(raw)
        assert settings.email.smtp_server == "smtp.gmail.com"
        assert settings.email.smtp_port == 465
        assert settings.email.use_tls is False

    def test_smtp_defaults_when_missing(self):
        """Missing SMTP fields should use defaults."""
        raw = {
            "email": {
                "imap_server": "imap.test.com",
                "username": "test@test.com",
            }
        }
        settings = Settings._from_dict(raw)
        assert settings.email.smtp_server == ""
        assert settings.email.smtp_port == 587
        assert settings.email.use_tls is True


class TestApprovalConfigEmail:
    """Test email approval fields in ApprovalConfig."""

    def test_default_approval_email_fields(self):
        """Email approval fields should default to disabled."""
        cfg = ApprovalConfig()
        assert cfg.email_enabled is False
        assert cfg.approval_recipients == ""

    def test_approval_email_from_dict(self):
        """_from_dict should parse email approval fields."""
        raw = {
            "approvals": {
                "email_enabled": True,
                "approval_recipients": "dba@company.com, lead@company.com",
                "approval_timeout": 7200,
            }
        }
        settings = Settings._from_dict(raw)
        assert settings.approvals.email_enabled is True
        assert settings.approvals.approval_recipients == "dba@company.com, lead@company.com"
        assert settings.approvals.approval_timeout == 7200

    def test_approval_email_defaults_when_missing(self):
        """Missing email approval fields should use defaults."""
        raw = {
            "approvals": {
                "slack_webhook_url": "https://hooks.slack.com/test",
            }
        }
        settings = Settings._from_dict(raw)
        assert settings.approvals.email_enabled is False
        assert settings.approvals.approval_recipients == ""
