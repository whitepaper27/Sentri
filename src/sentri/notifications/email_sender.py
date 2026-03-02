"""SMTP email sender for approval notifications."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("sentri.notifications.email")


def _send_smtp(
    smtp_server: str,
    smtp_port: int,
    from_addr: str,
    to_addrs: list[str],
    msg: MIMEMultipart,
    username: str = "",
    password: str = "",
    use_tls: bool = True,
) -> bool:
    """Low-level SMTP send. Returns True on success."""
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.sendmail(from_addr, to_addrs, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        logger.error("SMTP send failed: %s", e)
        return False


def send_approval_email(
    smtp_server: str,
    smtp_port: int,
    from_addr: str,
    to_addr: str,
    workflow_id: str,
    database: str,
    alert_type: str,
    message_body: str,
    username: str = "",
    password: str = "",
    use_tls: bool = True,
) -> bool:
    """Send an approval request email (legacy).

    Returns True on success, False on failure.
    """
    if not smtp_server or not to_addr:
        logger.debug("Email not configured, skipping")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[URGENT] Sentri approval needed: {alert_type} on {database}"
    msg["From"] = from_addr
    msg["To"] = to_addr

    text_part = MIMEText(message_body, "plain")
    msg.attach(text_part)

    html_body = message_body.replace("\n", "<br>")
    html_part = MIMEText(f"<html><body>{html_body}</body></html>", "html")
    msg.attach(html_part)

    ok = _send_smtp(
        smtp_server,
        smtp_port,
        from_addr,
        [to_addr],
        msg,
        username,
        password,
        use_tls,
    )
    if ok:
        logger.info("Approval email sent to %s for workflow %s", to_addr, workflow_id)
    return ok


def send_approval_request_email(
    smtp_server: str,
    smtp_port: int,
    from_addr: str,
    to_addrs: list[str],
    workflow_id: str,
    database_id: str,
    alert_type: str,
    environment: str,
    forward_sql: str,
    rollback_sql: str,
    risk_level: str,
    confidence: float,
    reasons: list[str],
    username: str = "",
    password: str = "",
    use_tls: bool = True,
) -> bool:
    """Send a rich approval request email with [WF:xxxxxxxx] tracking tag.

    The subject line contains [WF:<short_id>] so Scout can match replies
    back to workflows. DBAs reply with APPROVED or DENIED.

    Returns True on success, False on failure.
    """
    if not smtp_server or not to_addrs:
        logger.debug("Email not configured, skipping approval request")
        return False

    short_id = workflow_id[:8]
    subject = f"[SENTRI] Approval needed: {alert_type} on {database_id} [WF:{short_id}]"

    # Plain text body
    reasons_text = "\n".join(f"  - {r}" for r in reasons) if reasons else "  (none)"
    text_body = f"""SENTRI APPROVAL REQUEST
======================

Workflow:    {workflow_id}
Database:    {database_id}
Environment: {environment}
Alert Type:  {alert_type}
Risk Level:  {risk_level}
Confidence:  {confidence:.0%}

Safety Mesh Reasons:
{reasons_text}

Proposed SQL:
{forward_sql}

Rollback SQL:
{rollback_sql}

---
To approve, reply with: APPROVED
To deny, reply with: DENIED

Or use the CLI:
  sentri approve {workflow_id[:8]}
  sentri approve {workflow_id[:8]} --deny
"""

    # HTML body
    reasons_html = "".join(f"<li>{r}</li>" for r in reasons) if reasons else "<li>(none)</li>"
    html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
<div style="background: #1a237e; color: white; padding: 16px 24px; border-radius: 8px 8px 0 0;">
  <h2 style="margin: 0;">SENTRI Approval Request</h2>
</div>
<div style="border: 1px solid #ddd; border-top: none; padding: 24px; border-radius: 0 0 8px 8px;">
  <table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
    <tr><td style="padding: 6px 12px; font-weight: bold; width: 130px;">Workflow</td>
        <td style="padding: 6px 12px;"><code>{workflow_id}</code></td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Database</td>
        <td style="padding: 6px 12px;">{database_id}</td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Environment</td>
        <td style="padding: 6px 12px;">{environment}</td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Alert Type</td>
        <td style="padding: 6px 12px;">{alert_type}</td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Risk Level</td>
        <td style="padding: 6px 12px; color: {'#c62828' if risk_level in ('HIGH','CRITICAL') else '#e65100' if risk_level == 'MEDIUM' else '#2e7d32'};">
          <strong>{risk_level}</strong></td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Confidence</td>
        <td style="padding: 6px 12px;">{confidence:.0%}</td></tr>
  </table>

  <h3 style="margin-top: 20px;">Safety Mesh Reasons</h3>
  <ul>{reasons_html}</ul>

  <h3>Proposed SQL</h3>
  <pre style="background: #f5f5f5; padding: 12px; border-radius: 4px; overflow-x: auto;">{forward_sql}</pre>

  <h3>Rollback SQL</h3>
  <pre style="background: #f5f5f5; padding: 12px; border-radius: 4px; overflow-x: auto;">{rollback_sql}</pre>

  <hr style="margin: 24px 0; border: none; border-top: 1px solid #ddd;">

  <p style="font-size: 16px;">
    <strong>To approve:</strong> Reply to this email with <code>APPROVED</code><br>
    <strong>To deny:</strong> Reply to this email with <code>DENIED</code>
  </p>
  <p style="color: #666; font-size: 13px;">
    CLI alternative: <code>sentri approve {workflow_id[:8]}</code> or
    <code>sentri approve {workflow_id[:8]} --deny</code>
  </p>
</div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Reply-To"] = from_addr

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    ok = _send_smtp(
        smtp_server,
        smtp_port,
        from_addr,
        to_addrs,
        msg,
        username,
        password,
        use_tls,
    )
    if ok:
        logger.info(
            "Approval request email sent to %s for workflow %s [WF:%s]",
            ", ".join(to_addrs),
            workflow_id,
            short_id,
        )
    return ok


def send_timeout_notification_email(
    smtp_server: str,
    smtp_port: int,
    from_addr: str,
    to_addrs: list[str],
    workflow_id: str,
    database_id: str,
    alert_type: str,
    environment: str,
    elapsed_seconds: float,
    timeout_seconds: int,
    username: str = "",
    password: str = "",
    use_tls: bool = True,
) -> bool:
    """Send notification that an approval request has timed out.

    Returns True on success, False on failure.
    """
    if not smtp_server or not to_addrs:
        logger.debug("Email not configured, skipping timeout notification")
        return False

    short_id = workflow_id[:8]
    elapsed_hours = elapsed_seconds / 3600
    timeout_hours = timeout_seconds / 3600
    subject = f"[SENTRI] Approval timed out: {alert_type} on {database_id} [WF:{short_id}]"

    text_body = f"""SENTRI APPROVAL TIMEOUT
======================

Workflow:    {workflow_id}
Database:    {database_id}
Environment: {environment}
Alert Type:  {alert_type}

The approval request has timed out after {elapsed_hours:.1f} hours
(timeout: {timeout_hours:.1f} hours).

The workflow has been ESCALATED and requires manual attention.

To resolve manually:
  sentri resolve {short_id} --reason "Fixed manually"

To escalate further:
  sentri resolve {short_id} --escalate
"""

    html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto;">
<div style="background: #b71c1c; color: white; padding: 16px 24px; border-radius: 8px 8px 0 0;">
  <h2 style="margin: 0;">SENTRI Approval Timed Out</h2>
</div>
<div style="border: 1px solid #ddd; border-top: none; padding: 24px; border-radius: 0 0 8px 8px;">
  <table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
    <tr><td style="padding: 6px 12px; font-weight: bold; width: 130px;">Workflow</td>
        <td style="padding: 6px 12px;"><code>{workflow_id}</code></td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Database</td>
        <td style="padding: 6px 12px;">{database_id}</td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Environment</td>
        <td style="padding: 6px 12px;">{environment}</td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Alert Type</td>
        <td style="padding: 6px 12px;">{alert_type}</td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Elapsed</td>
        <td style="padding: 6px 12px; color: #c62828;"><strong>{elapsed_hours:.1f} hours</strong></td></tr>
    <tr><td style="padding: 6px 12px; font-weight: bold;">Timeout</td>
        <td style="padding: 6px 12px;">{timeout_hours:.1f} hours</td></tr>
  </table>

  <p style="color: #c62828; font-size: 16px; font-weight: bold;">
    This workflow has been ESCALATED and requires manual attention.
  </p>

  <hr style="margin: 24px 0; border: none; border-top: 1px solid #ddd;">

  <p style="color: #666; font-size: 13px;">
    CLI: <code>sentri resolve {short_id} --reason "..."</code> or
    <code>sentri resolve {short_id} --escalate</code>
  </p>
</div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    ok = _send_smtp(
        smtp_server,
        smtp_port,
        from_addr,
        to_addrs,
        msg,
        username,
        password,
        use_tls,
    )
    if ok:
        logger.info(
            "Timeout notification sent to %s for workflow %s [WF:%s]",
            ", ".join(to_addrs),
            workflow_id,
            short_id,
        )
    return ok
