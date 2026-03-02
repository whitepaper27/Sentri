"""Tests for approval request email sending."""

from __future__ import annotations

from unittest.mock import patch

from sentri.notifications.email_sender import (
    send_approval_request_email,
    send_timeout_notification_email,
)


class TestSendApprovalRequestEmail:
    """Test send_approval_request_email()."""

    def test_subject_format_contains_wf_tag(self):
        """Subject should contain [WF:xxxxxxxx] for reply detection."""
        with patch("sentri.notifications.email_sender._send_smtp", return_value=True) as mock:
            send_approval_request_email(
                smtp_server="smtp.test.com",
                smtp_port=587,
                from_addr="sentri@test.com",
                to_addrs=["dba@test.com"],
                workflow_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                database_id="DEV-DB-01",
                alert_type="cpu_high",
                environment="DEV",
                forward_sql="SELECT 1",
                rollback_sql="N/A",
                risk_level="LOW",
                confidence=0.85,
                reasons=["No rollback SQL"],
            )
            msg = mock.call_args[0][4]  # 5th positional arg is the MIMEMultipart
            assert "[WF:a1b2c3d4]" in msg["Subject"]
            assert "[SENTRI]" in msg["Subject"]
            assert "cpu_high" in msg["Subject"]
            assert "DEV-DB-01" in msg["Subject"]

    def test_multi_recipient(self):
        """Should send to all recipients."""
        with patch("sentri.notifications.email_sender._send_smtp", return_value=True) as mock:
            result = send_approval_request_email(
                smtp_server="smtp.test.com",
                smtp_port=587,
                from_addr="sentri@test.com",
                to_addrs=["dba1@test.com", "dba2@test.com"],
                workflow_id="a1b2c3d4-full-id",
                database_id="PROD-DB-07",
                alert_type="tablespace_full",
                environment="PROD",
                forward_sql="ALTER TABLESPACE ...",
                rollback_sql="DROP DATAFILE ...",
                risk_level="HIGH",
                confidence=0.90,
                reasons=["DDL in PROD requires approval"],
            )
            assert result is True
            call_args = mock.call_args[0]
            assert call_args[3] == ["dba1@test.com", "dba2@test.com"]

    def test_html_body_contains_sql(self):
        """HTML body should contain the proposed SQL and rollback."""
        with patch("sentri.notifications.email_sender._send_smtp", return_value=True) as mock:
            send_approval_request_email(
                smtp_server="smtp.test.com",
                smtp_port=587,
                from_addr="sentri@test.com",
                to_addrs=["dba@test.com"],
                workflow_id="abcdef12-3456",
                database_id="UAT-DB-03",
                alert_type="temp_full",
                environment="UAT",
                forward_sql="ALTER TABLESPACE TEMP ADD TEMPFILE",
                rollback_sql="DROP TEMPFILE",
                risk_level="MEDIUM",
                confidence=0.75,
                reasons=["DDL in UAT with risk=MEDIUM"],
            )
            msg = mock.call_args[0][4]
            # Check both parts exist
            parts = list(msg.walk())
            html_parts = [p for p in parts if p.get_content_type() == "text/html"]
            assert len(html_parts) == 1
            html = html_parts[0].get_payload()
            assert "ALTER TABLESPACE TEMP ADD TEMPFILE" in html
            assert "DROP TEMPFILE" in html

    def test_skips_when_not_configured(self):
        """Should return False if SMTP not configured."""
        result = send_approval_request_email(
            smtp_server="",
            smtp_port=587,
            from_addr="",
            to_addrs=[],
            workflow_id="test",
            database_id="test",
            alert_type="test",
            environment="DEV",
            forward_sql="",
            rollback_sql="",
            risk_level="LOW",
            confidence=0.0,
            reasons=[],
        )
        assert result is False

    def test_smtp_failure_returns_false(self):
        """Should return False if SMTP send fails."""
        with patch("sentri.notifications.email_sender._send_smtp", return_value=False):
            result = send_approval_request_email(
                smtp_server="smtp.test.com",
                smtp_port=587,
                from_addr="sentri@test.com",
                to_addrs=["dba@test.com"],
                workflow_id="testid",
                database_id="DEV-DB-01",
                alert_type="cpu_high",
                environment="DEV",
                forward_sql="SELECT 1",
                rollback_sql="N/A",
                risk_level="LOW",
                confidence=0.5,
                reasons=[],
            )
            assert result is False


class TestSendTimeoutNotificationEmail:
    """Test send_timeout_notification_email()."""

    def test_timeout_email_subject_format(self):
        """Subject should contain [WF:] tag and 'timed out'."""
        with patch("sentri.notifications.email_sender._send_smtp", return_value=True) as mock:
            result = send_timeout_notification_email(
                smtp_server="smtp.test.com",
                smtp_port=587,
                from_addr="sentri@test.com",
                to_addrs=["dba@test.com"],
                workflow_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                database_id="DEV-DB-01",
                alert_type="cpu_high",
                environment="DEV",
                elapsed_seconds=7200.0,
                timeout_seconds=3600,
            )
            assert result is True
            msg = mock.call_args[0][4]
            assert "[WF:a1b2c3d4]" in msg["Subject"]
            assert "timed out" in msg["Subject"]
            assert "cpu_high" in msg["Subject"]

    def test_timeout_email_skips_when_not_configured(self):
        """Should return False if SMTP not configured."""
        result = send_timeout_notification_email(
            smtp_server="",
            smtp_port=587,
            from_addr="",
            to_addrs=[],
            workflow_id="test",
            database_id="test",
            alert_type="test",
            environment="DEV",
            elapsed_seconds=0,
            timeout_seconds=0,
        )
        assert result is False
