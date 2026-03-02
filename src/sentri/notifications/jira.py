"""JIRA integration - STUB for POC.

Future: Auto-create tickets, link to workflows, auto-close on completion.
POC: Logs the intent to create a ticket.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("sentri.notifications.jira")


def create_ticket(
    jira_url: str,
    project: str,
    workflow_id: str,
    summary: str,
    description: str,
) -> str | None:
    """STUB - Create a JIRA ticket for a workflow.

    Returns ticket key (e.g., "DBA-1234") or None.
    """
    if not jira_url:
        logger.debug("JIRA not configured, skipping ticket creation")
        return None

    logger.info(
        "STUB: Would create JIRA ticket in %s/%s: %s (workflow=%s)",
        jira_url,
        project,
        summary,
        workflow_id,
    )
    return None


def update_ticket(jira_url: str, ticket_key: str, status: str, comment: str) -> bool:
    """STUB - Update a JIRA ticket."""
    if not jira_url or not ticket_key:
        return False

    logger.info("STUB: Would update JIRA %s -> %s: %s", ticket_key, status, comment)
    return False
