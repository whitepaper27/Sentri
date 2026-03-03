"""Tests for notification adapters and router (v5.1b)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sentri.notifications.adapter import NotificationAdapter, NotificationContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx():
    """Standard notification context for tests."""
    return NotificationContext(
        workflow_id="abc12345-1111-2222-3333-444455556666",
        database_id="DEV-DB-01",
        alert_type="cpu_high",
        environment="DEV",
        risk_level="MEDIUM",
        confidence=0.75,
        forward_sql="-- investigate CPU",
        rollback_sql="N/A",
        reasons=["Confidence 0.75 < 0.80 — require approval"],
        elapsed_seconds=3700.0,
        timeout_seconds=3600,
    )


# ---------------------------------------------------------------------------
# NotificationContext
# ---------------------------------------------------------------------------


class TestNotificationContext:
    def test_short_id(self, ctx):
        """short_id returns first 8 chars."""
        assert ctx.short_id == "abc12345"

    def test_default_values(self):
        """Minimal context works."""
        ctx = NotificationContext(
            workflow_id="test-id",
            database_id="db",
            alert_type="tablespace_full",
            environment="DEV",
        )
        assert ctx.risk_level == ""
        assert ctx.confidence == 0.0
        assert ctx.reasons == []


# ---------------------------------------------------------------------------
# EmailAdapter
# ---------------------------------------------------------------------------


class TestEmailAdapter:
    def test_delegates_to_email_sender(self, ctx):
        """EmailAdapter delegates to send_approval_request_email."""
        from sentri.notifications.email_adapter import EmailAdapter

        adapter = EmailAdapter(
            smtp_server="smtp.test.com",
            smtp_port=587,
            from_addr="test@test.com",
            to_addrs=["dba@test.com"],
            username="test",
            password="pass",
        )

        with patch(
            "sentri.notifications.email_adapter.send_approval_request_email",
            return_value=True,
        ) as mock_send:
            result = adapter.send_approval_request(ctx)

        assert result is True
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args
        assert call_kwargs[1]["workflow_id"] == ctx.workflow_id
        assert call_kwargs[1]["database_id"] == ctx.database_id

    def test_timeout_delegates_to_email_sender(self, ctx):
        """EmailAdapter delegates timeout to send_timeout_notification_email."""
        from sentri.notifications.email_adapter import EmailAdapter

        adapter = EmailAdapter(
            smtp_server="smtp.test.com",
            smtp_port=587,
            from_addr="test@test.com",
            to_addrs=["dba@test.com"],
        )

        with patch(
            "sentri.notifications.email_adapter.send_timeout_notification_email",
            return_value=True,
        ) as mock_send:
            result = adapter.send_timeout_notification(ctx)

        assert result is True
        mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# SlackAdapter
# ---------------------------------------------------------------------------


class TestSlackAdapter:
    def test_delegates_to_slack(self, ctx):
        """SlackAdapter delegates to slack.send_approval_request."""
        from sentri.notifications.slack_adapter import SlackAdapter

        adapter = SlackAdapter("https://hooks.slack.com/test")

        with patch(
            "sentri.notifications.slack_adapter.send_approval_request",
            return_value=True,
        ) as mock_send:
            result = adapter.send_approval_request(ctx)

        assert result is True
        mock_send.assert_called_once()

    def test_timeout_sends_message(self, ctx):
        """SlackAdapter sends timeout as formatted message."""
        from sentri.notifications.slack_adapter import SlackAdapter

        adapter = SlackAdapter("https://hooks.slack.com/test")

        with patch(
            "sentri.notifications.slack_adapter.send_slack_message",
            return_value=True,
        ) as mock_send:
            result = adapter.send_timeout_notification(ctx)

        assert result is True
        msg = mock_send.call_args[0][1]
        assert "Timed Out" in msg
        assert ctx.database_id in msg


# ---------------------------------------------------------------------------
# WebhookAdapter
# ---------------------------------------------------------------------------


class TestWebhookAdapter:
    def test_posts_json_payload(self, ctx):
        """WebhookAdapter POSTs structured JSON."""
        from sentri.notifications.webhook_adapter import WebhookAdapter

        adapter = WebhookAdapter("https://webhook.test/endpoint")

        with patch("sentri.notifications.webhook_adapter.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            result = adapter.send_approval_request(ctx)

        assert result is True
        mock_open.assert_called_once()

    def test_empty_url_returns_false(self, ctx):
        """WebhookAdapter with empty URL returns False."""
        from sentri.notifications.webhook_adapter import WebhookAdapter

        adapter = WebhookAdapter("")
        assert adapter.send_approval_request(ctx) is False

    def test_completion_notice(self, ctx):
        """WebhookAdapter sends completion notice."""
        from sentri.notifications.webhook_adapter import WebhookAdapter

        ctx.result = "SUCCESS"
        adapter = WebhookAdapter("https://webhook.test/endpoint")

        with patch("sentri.notifications.webhook_adapter.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            result = adapter.send_completion_notice(ctx)

        assert result is True


# ---------------------------------------------------------------------------
# PagerDutyAdapter
# ---------------------------------------------------------------------------


class TestPagerDutyAdapter:
    def test_trigger_event(self, ctx):
        """PagerDutyAdapter triggers an incident."""
        from sentri.notifications.pagerduty_adapter import PagerDutyAdapter

        adapter = PagerDutyAdapter("test-routing-key")

        with patch("sentri.notifications.pagerduty_adapter.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 202
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            result = adapter.send_approval_request(ctx)

        assert result is True

    def test_empty_routing_key_returns_false(self, ctx):
        """PagerDutyAdapter with no routing key returns False."""
        from sentri.notifications.pagerduty_adapter import PagerDutyAdapter

        adapter = PagerDutyAdapter("")
        assert adapter.send_approval_request(ctx) is False

    def test_resolve_on_completion(self, ctx):
        """PagerDutyAdapter resolves incident on completion."""
        from sentri.notifications.pagerduty_adapter import PagerDutyAdapter

        ctx.result = "SUCCESS"
        adapter = PagerDutyAdapter("test-key")

        with patch("sentri.notifications.pagerduty_adapter.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 202
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            result = adapter.send_completion_notice(ctx)

        assert result is True


# ---------------------------------------------------------------------------
# NotificationRouter
# ---------------------------------------------------------------------------


class TestNotificationRouter:
    def test_dispatches_to_all_adapters(self, ctx):
        """Router dispatches to all registered adapters."""
        from sentri.notifications.router import NotificationRouter

        mock1 = MagicMock(spec=NotificationAdapter)
        mock1.send_approval_request.return_value = True
        mock2 = MagicMock(spec=NotificationAdapter)
        mock2.send_approval_request.return_value = True

        router = NotificationRouter()
        router.add_adapter(mock1)
        router.add_adapter(mock2)

        sent = router.send_approval_request(ctx)

        assert sent == 2
        mock1.send_approval_request.assert_called_once_with(ctx)
        mock2.send_approval_request.assert_called_once_with(ctx)

    def test_counts_successful_sends_only(self, ctx):
        """Router counts only successful sends."""
        from sentri.notifications.router import NotificationRouter

        mock_ok = MagicMock(spec=NotificationAdapter)
        mock_ok.send_approval_request.return_value = True
        mock_fail = MagicMock(spec=NotificationAdapter)
        mock_fail.send_approval_request.return_value = False

        router = NotificationRouter()
        router.add_adapter(mock_ok)
        router.add_adapter(mock_fail)

        sent = router.send_approval_request(ctx)
        assert sent == 1

    def test_handles_adapter_exception(self, ctx):
        """Router catches adapter exceptions and continues."""
        from sentri.notifications.router import NotificationRouter

        mock_crash = MagicMock(spec=NotificationAdapter)
        mock_crash.send_approval_request.side_effect = Exception("boom")
        mock_ok = MagicMock(spec=NotificationAdapter)
        mock_ok.send_approval_request.return_value = True

        router = NotificationRouter()
        router.add_adapter(mock_crash)
        router.add_adapter(mock_ok)

        sent = router.send_approval_request(ctx)
        assert sent == 1  # Only the working adapter counted

    def test_no_adapters_returns_zero(self, ctx):
        """Router with no adapters returns 0."""
        from sentri.notifications.router import NotificationRouter

        router = NotificationRouter()
        assert router.send_approval_request(ctx) == 0

    def test_from_settings_backwards_compatible(self):
        """from_settings reads legacy approvals config."""
        from sentri.config.settings import Settings
        from sentri.notifications.router import NotificationRouter

        s = Settings()
        s.approvals.email_enabled = True
        s.email.smtp_server = "smtp.test.com"
        s.email.smtp_port = 587
        s.email.username = "test@test.com"
        s.email.password = "pass"
        s.email.use_tls = True

        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 1  # EmailAdapter

    def test_from_settings_slack_adapter(self):
        """from_settings creates SlackAdapter from legacy config."""
        from sentri.config.settings import Settings
        from sentri.notifications.router import NotificationRouter

        s = Settings()
        s.approvals.slack_webhook_url = "https://hooks.slack.com/test"

        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 1  # SlackAdapter

    def test_from_settings_new_webhook_config(self):
        """from_settings creates WebhookAdapter from notifications config."""
        from sentri.config.settings import (
            NotificationAdapterConfig,
            NotificationsConfig,
            Settings,
        )
        from sentri.notifications.router import NotificationRouter

        s = Settings()
        s.notifications = NotificationsConfig(
            adapters=[
                NotificationAdapterConfig(
                    type="webhook",
                    enabled=True,
                    url="https://webhook.test/endpoint",
                ),
            ]
        )

        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 1  # WebhookAdapter

    def test_from_settings_disabled_adapter_skipped(self):
        """Disabled adapters are not registered."""
        from sentri.config.settings import (
            NotificationAdapterConfig,
            NotificationsConfig,
            Settings,
        )
        from sentri.notifications.router import NotificationRouter

        s = Settings()
        s.notifications = NotificationsConfig(
            adapters=[
                NotificationAdapterConfig(
                    type="webhook",
                    enabled=False,
                    url="https://webhook.test/endpoint",
                ),
            ]
        )

        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 0

    def test_from_settings_pagerduty_adapter(self):
        """from_settings creates PagerDutyAdapter from notifications config."""
        from sentri.config.settings import (
            NotificationAdapterConfig,
            NotificationsConfig,
            Settings,
        )
        from sentri.notifications.router import NotificationRouter

        s = Settings()
        s.notifications = NotificationsConfig(
            adapters=[
                NotificationAdapterConfig(
                    type="pagerduty",
                    enabled=True,
                    routing_key="test-key-123",
                ),
            ]
        )

        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 1

    def test_from_settings_multiple_adapters(self):
        """from_settings creates multiple adapters from mixed config."""
        from sentri.config.settings import (
            NotificationAdapterConfig,
            NotificationsConfig,
            Settings,
        )
        from sentri.notifications.router import NotificationRouter

        s = Settings()
        s.approvals.email_enabled = True
        s.email.smtp_server = "smtp.test.com"
        s.email.smtp_port = 587
        s.email.username = "test@test.com"
        s.email.password = "pass"
        s.approvals.slack_webhook_url = "https://hooks.slack.com/test"
        s.notifications = NotificationsConfig(
            adapters=[
                NotificationAdapterConfig(
                    type="webhook",
                    enabled=True,
                    url="https://webhook.test/endpoint",
                ),
                NotificationAdapterConfig(
                    type="pagerduty",
                    enabled=True,
                    routing_key="pd-key",
                ),
            ]
        )

        router = NotificationRouter.from_settings(s)
        # Email + Slack + Webhook + PagerDuty = 4
        assert router.adapter_count == 4

    def test_timeout_dispatches_to_all(self, ctx):
        """Router dispatches timeout notifications to all adapters."""
        from sentri.notifications.router import NotificationRouter

        mock1 = MagicMock(spec=NotificationAdapter)
        mock1.send_timeout_notification.return_value = True
        mock2 = MagicMock(spec=NotificationAdapter)
        mock2.send_timeout_notification.return_value = False

        router = NotificationRouter()
        router.add_adapter(mock1)
        router.add_adapter(mock2)

        sent = router.send_timeout_notification(ctx)
        assert sent == 1
        mock1.send_timeout_notification.assert_called_once_with(ctx)
        mock2.send_timeout_notification.assert_called_once_with(ctx)

    def test_completion_dispatches_to_all(self, ctx):
        """Router dispatches completion notices to all adapters."""
        from sentri.notifications.router import NotificationRouter

        mock1 = MagicMock(spec=NotificationAdapter)
        mock1.send_completion_notice.return_value = True

        router = NotificationRouter()
        router.add_adapter(mock1)

        ctx.result = "SUCCESS"
        sent = router.send_completion_notice(ctx)
        assert sent == 1


# ---------------------------------------------------------------------------
# Integration: NotificationRouter from YAML config
# ---------------------------------------------------------------------------


class TestNotificationRouterFromYaml:
    """Test NotificationRouter built from YAML config files."""

    def test_router_from_yaml_with_email(self, tmp_path):
        """Router from YAML with email_enabled creates EmailAdapter."""
        from sentri.config.settings import Settings
        from sentri.notifications.router import NotificationRouter

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "email:\n"
            "  smtp_server: smtp.test.com\n"
            "  smtp_port: 587\n"
            "  username: test@test.com\n"
            "  password: pass\n"
            "  use_tls: true\n"
            "approvals:\n"
            "  email_enabled: true\n",
            encoding="utf-8",
        )

        s = Settings.load(config)
        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 1

    def test_router_from_yaml_with_webhook(self, tmp_path):
        """Router from YAML with notifications.adapters creates WebhookAdapter."""
        from sentri.config.settings import Settings
        from sentri.notifications.router import NotificationRouter

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "notifications:\n"
            "  adapters:\n"
            "    - type: webhook\n"
            "      enabled: true\n"
            "      url: https://webhook.test/endpoint\n"
            "      headers:\n"
            "        Content-Type: application/json\n",
            encoding="utf-8",
        )

        s = Settings.load(config)
        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 1

    def test_router_from_yaml_with_pagerduty(self, tmp_path):
        """Router from YAML with pagerduty adapter."""
        from sentri.config.settings import Settings
        from sentri.notifications.router import NotificationRouter

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "notifications:\n"
            "  adapters:\n"
            "    - type: pagerduty\n"
            "      enabled: true\n"
            "      routing_key: test-routing-key\n",
            encoding="utf-8",
        )

        s = Settings.load(config)
        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 1

    def test_router_from_yaml_disabled_skipped(self, tmp_path):
        """Disabled adapters in YAML are not registered."""
        from sentri.config.settings import Settings
        from sentri.notifications.router import NotificationRouter

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "notifications:\n"
            "  adapters:\n"
            "    - type: webhook\n"
            "      enabled: false\n"
            "      url: https://webhook.test/endpoint\n",
            encoding="utf-8",
        )

        s = Settings.load(config)
        router = NotificationRouter.from_settings(s)
        assert router.adapter_count == 0

    def test_router_from_yaml_mixed_legacy_and_new(self, tmp_path):
        """Router from YAML with both legacy and new config."""
        from sentri.config.settings import Settings
        from sentri.notifications.router import NotificationRouter

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "email:\n"
            "  smtp_server: smtp.test.com\n"
            "  smtp_port: 587\n"
            "  username: test@test.com\n"
            "  password: pass\n"
            "  use_tls: true\n"
            "approvals:\n"
            "  email_enabled: true\n"
            "  slack_webhook_url: https://hooks.slack.com/test\n"
            "notifications:\n"
            "  adapters:\n"
            "    - type: webhook\n"
            "      enabled: true\n"
            "      url: https://webhook.test/endpoint\n",
            encoding="utf-8",
        )

        s = Settings.load(config)
        router = NotificationRouter.from_settings(s)
        # Email (legacy) + Slack (legacy) + Webhook (new) = 3
        assert router.adapter_count == 3

    def test_notifications_config_parsed_from_yaml(self, tmp_path):
        """NotificationsConfig is parsed correctly from YAML."""
        from sentri.config.settings import Settings

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "notifications:\n"
            "  adapters:\n"
            "    - type: webhook\n"
            "      enabled: true\n"
            "      url: https://webhook.test/endpoint\n"
            "      headers:\n"
            "        Authorization: Bearer token123\n"
            "    - type: pagerduty\n"
            "      enabled: false\n"
            "      routing_key: pd-key\n",
            encoding="utf-8",
        )

        s = Settings.load(config)
        assert len(s.notifications.adapters) == 2
        assert s.notifications.adapters[0].type == "webhook"
        assert s.notifications.adapters[0].enabled is True
        assert s.notifications.adapters[0].url == "https://webhook.test/endpoint"
        assert s.notifications.adapters[0].headers == {"Authorization": "Bearer token123"}
        assert s.notifications.adapters[1].type == "pagerduty"
        assert s.notifications.adapters[1].enabled is False
        assert s.notifications.adapters[1].routing_key == "pd-key"

    def test_empty_notifications_config(self, tmp_path):
        """Empty notifications section doesn't break loading."""
        from sentri.config.settings import Settings

        config = tmp_path / "config" / "sentri.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "databases:\n  - name: dev\n    environment: DEV\n",
            encoding="utf-8",
        )

        s = Settings.load(config)
        assert len(s.notifications.adapters) == 0
