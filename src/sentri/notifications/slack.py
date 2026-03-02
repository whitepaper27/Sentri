"""Slack webhook integration for notifications and approvals."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("sentri.notifications.slack")


def send_slack_message(webhook_url: str, message: str) -> bool:
    """Send a message to Slack via webhook.

    Args:
        webhook_url: Slack incoming webhook URL
        message: Message text (supports Slack markdown)

    Returns True on success, False on failure.
    """
    if not webhook_url:
        logger.debug("No Slack webhook configured, skipping")
        return False

    payload = json.dumps({"text": message}).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Slack message sent successfully")
                return True
            else:
                logger.warning("Slack returned status %d", resp.status)
                return False
    except urllib.error.URLError as e:
        logger.error("Slack webhook failed: %s", e)
        return False


def send_approval_request(
    webhook_url: str,
    workflow_id: str,
    database: str,
    alert_type: str,
    proposed_action: str,
    risk_level: str,
) -> bool:
    """Send a formatted approval request to Slack."""
    message = (
        f":warning: *Sentri Approval Request*\n\n"
        f"*Workflow*: `{workflow_id}`\n"
        f"*Database*: `{database}`\n"
        f"*Alert*: `{alert_type}`\n"
        f"*Risk*: `{risk_level}`\n\n"
        f"*Proposed Action*:\n```{proposed_action}```\n\n"
        f"Please approve or deny in the Sentri dashboard."
    )
    return send_slack_message(webhook_url, message)


def send_completion_notice(
    webhook_url: str,
    workflow_id: str,
    database: str,
    alert_type: str,
    result: str,
) -> bool:
    """Send execution completion notification to Slack."""
    emoji = ":white_check_mark:" if result == "SUCCESS" else ":x:"
    message = (
        f"{emoji} *Sentri Execution Complete*\n\n"
        f"*Workflow*: `{workflow_id}`\n"
        f"*Database*: `{database}`\n"
        f"*Alert*: `{alert_type}`\n"
        f"*Result*: `{result}`"
    )
    return send_slack_message(webhook_url, message)
