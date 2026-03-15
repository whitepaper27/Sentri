"""Agent 1: The Scout - Email parser and alert detector."""

from __future__ import annotations

import email
import email.header
import imaplib
import re
import threading
from datetime import datetime, timezone
from typing import Optional

from sentri.core.constants import WorkflowStatus
from sentri.core.models import AuditRecord, Suggestion, Workflow
from sentri.policy.alert_patterns import AlertPatterns

from .base import AgentContext, BaseAgent


class ScoutAgent(BaseAgent):
    """Monitor IMAP inbox for DBA alerts, parse them into workflow suggestions."""

    def __init__(self, context: AgentContext):
        super().__init__("scout", context)
        self._stop_event = threading.Event()
        self._alert_event = threading.Event()
        self._patterns: AlertPatterns | None = None

    @property
    def alert_event(self) -> threading.Event:
        """Event signaled when new alerts are detected (for orchestrator)."""
        return self._alert_event

    def load_patterns(self) -> None:
        """Load all alert regex patterns from policy files."""
        self._patterns = AlertPatterns(self.context.policy_loader)
        compiled = self._patterns.get_all_patterns()
        self.logger.info("Loaded %d alert patterns", len(compiled))

    def run_loop(self, poll_interval: int = 60) -> None:
        """Background thread entry point. Polls IMAP every poll_interval seconds."""
        self.load_patterns()
        self.logger.info("Scout started, polling every %ds", poll_interval)

        while not self._stop_event.is_set():
            try:
                emails = self._fetch_unread_emails()
                for msg in emails:
                    # Dedup: skip emails already processed (survives restarts)
                    msg_id = msg.get("Message-ID", "")
                    if self._is_already_processed(msg_id):
                        self.logger.debug("Skipping already-processed email: %s", msg_id)
                        continue

                    # Check for approval reply BEFORE alert matching
                    if self._check_approval_reply(msg, msg_id):
                        continue

                    suggestion = self._parse_email(msg)
                    if suggestion:
                        self._save_suggestion(suggestion)
                        self._mark_processed(msg_id)
                        self._alert_event.set()
            except imaplib.IMAP4.error as e:
                self.logger.error("IMAP error: %s", e)
            except Exception as e:
                self.logger.error("Scout loop error: %s", e)

            self._stop_event.wait(timeout=poll_interval)

        self.logger.info("Scout stopped")

    def stop(self) -> None:
        """Signal the background loop to stop."""
        self._stop_event.set()

    def process(self, workflow_id: str) -> dict:
        """Not used for Scout (runs in background). Interface compliance only."""
        return {"status": "not_applicable", "agent": "scout"}

    def process_raw_email(self, subject: str, body: str) -> Optional[str]:
        """Parse a raw email subject+body directly. Returns workflow_id if matched.

        Useful for testing without IMAP.
        """
        if not self._patterns:
            self.load_patterns()

        suggestion = self._match_alert(subject, body)
        if suggestion:
            return self._save_suggestion(suggestion)
        return None

    def _is_already_processed(self, message_id: str) -> bool:
        """Check if this email was already processed (persisted in cache table)."""
        if not message_id:
            return False
        rows = self.context.db.execute_read(
            "SELECT 1 FROM cache WHERE key = ?",
            (f"email:{message_id}",),
        )
        return len(rows) > 0

    def _mark_processed(self, message_id: str) -> None:
        """Record that this email has been processed (survives restarts)."""
        if message_id:
            self.context.db.execute_write(
                "INSERT OR IGNORE INTO cache (key, value) VALUES (?, ?)",
                (f"email:{message_id}", "processed"),
            )

    # Regex patterns for approval reply detection
    _WF_TAG_RE = re.compile(r"\[WF:([a-f0-9]{8})\]", re.IGNORECASE)
    _DECISION_RE = re.compile(r"\b(APPROVED|DENIED)\b", re.IGNORECASE)

    def _check_approval_reply(self, msg, msg_id: str) -> bool:
        """Check if email is an approval reply (contains [WF:xxxxxxxx]).

        If matched:
        - APPROVED → transition AWAITING_APPROVAL → APPROVED, wake Supervisor
        - DENIED → transition AWAITING_APPROVAL → DENIED → COMPLETED

        Returns True if this was an approval reply (consumed), False otherwise.
        """
        subject = self._decode_header(msg.get("Subject", ""))
        wf_match = self._WF_TAG_RE.search(subject)
        if not wf_match:
            return False

        short_id = wf_match.group(1).lower()
        body = self._get_email_body(msg)
        full_text = f"{subject}\n{body}"

        decision_match = self._DECISION_RE.search(full_text)
        if not decision_match:
            self.logger.info(
                "Approval reply detected for WF:%s but no APPROVED/DENIED found",
                short_id,
            )
            self._mark_processed(msg_id)
            return True

        decision = decision_match.group(1).upper()

        # Find workflow by short_id prefix match
        workflow = self._find_workflow_by_short_id(short_id)
        if not workflow:
            self.logger.warning(
                "Approval reply for WF:%s but no matching workflow found",
                short_id,
            )
            self._mark_processed(msg_id)
            return True

        if workflow.status != WorkflowStatus.AWAITING_APPROVAL.value:
            self.logger.info(
                "Approval reply for WF:%s but workflow status is %s (not AWAITING_APPROVAL)",
                short_id,
                workflow.status,
            )
            self._mark_processed(msg_id)
            return True

        # Extract approver from email
        from_addr = self._decode_header(msg.get("From", "unknown"))

        now_iso = datetime.now(timezone.utc).isoformat()

        if decision == "APPROVED":
            self.context.workflow_repo.update_status(
                workflow.id,
                WorkflowStatus.APPROVED.value,
                approved_by=from_addr,
                approved_at=now_iso,
            )
            self.context.audit_repo.create(
                AuditRecord(
                    workflow_id=workflow.id,
                    action_type="APPROVAL_DECISION",
                    action_sql=workflow.execution_plan or "",
                    database_id=workflow.database_id,
                    environment=workflow.environment,
                    executed_by="scout",
                    approved_by=from_addr,
                    result="APPROVED",
                    evidence="channel=email",
                )
            )
            self.logger.info(
                "Workflow %s APPROVED by %s (via email reply)",
                workflow.id,
                from_addr,
            )
            self._alert_event.set()  # Wake Supervisor to execute
        else:
            # Extract denial reason from text after "DENIED" keyword
            denied_idx = full_text.upper().find("DENIED")
            reason_text = ""
            if denied_idx >= 0:
                reason_text = full_text[denied_idx + 6 :].split("\n")[0].strip(" -:,")

            # DENIED — let Supervisor handle completion/escalation
            self.context.workflow_repo.update_status(
                workflow.id,
                WorkflowStatus.DENIED.value,
                approved_by=from_addr,
                approved_at=now_iso,
            )
            evidence = "channel=email"
            if reason_text:
                evidence += f",denied_reason={reason_text}"
            self.context.audit_repo.create(
                AuditRecord(
                    workflow_id=workflow.id,
                    action_type="APPROVAL_DECISION",
                    action_sql=workflow.execution_plan or "",
                    database_id=workflow.database_id,
                    environment=workflow.environment,
                    executed_by="scout",
                    approved_by=from_addr,
                    result="DENIED",
                    evidence=evidence,
                )
            )
            self.logger.info(
                "Workflow %s DENIED by %s (via email reply, reason: %s)",
                workflow.id,
                from_addr,
                reason_text or "(none)",
            )
            self._alert_event.set()  # Wake Supervisor to handle denial

        self._mark_processed(msg_id)
        return True

    def _find_workflow_by_short_id(self, short_id: str) -> Optional[Workflow]:
        """Find a workflow whose ID starts with the given 8-char prefix."""
        rows = self.context.db.execute_read(
            "SELECT id FROM workflows WHERE id LIKE ? LIMIT 1",
            (f"{short_id}%",),
        )
        if rows:
            return self.context.workflow_repo.get(rows[0]["id"])
        return None

    def _fetch_unread_emails(self) -> list:
        """Connect to IMAP, fetch UNSEEN messages."""
        cfg = self.context.settings.email
        if not cfg.imap_server or not cfg.username:
            self.logger.debug("Email not configured, skipping fetch")
            return []

        mail = imaplib.IMAP4_SSL(cfg.imap_server, cfg.imap_port)
        try:
            mail.login(cfg.username, cfg.password)
            mail.select("INBOX")
            _, msg_nums = mail.search(None, "UNSEEN")
            messages = []
            for num in msg_nums[0].split():
                if not num:
                    continue
                _, data = mail.fetch(num, "(RFC822)")
                if data and data[0]:
                    msg = email.message_from_bytes(data[0][1])
                    messages.append(msg)
                    mail.store(num, "+FLAGS", "\\Seen")
            return messages
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    def _parse_email(self, msg) -> Optional[Suggestion]:
        """Match an email message against all alert patterns."""
        subject = self._decode_header(msg.get("Subject", ""))
        body = self._get_email_body(msg)
        return self._match_alert(subject, body)

    def _match_alert(self, subject: str, body: str) -> Optional[Suggestion]:
        """Try matching subject+body against all 5 alert patterns."""
        if not self._patterns:
            return None

        full_text = f"{subject}\n{body}"
        all_patterns = self._patterns.get_all_patterns()

        for alert_type, pattern in all_patterns.items():
            match = pattern.search(full_text)
            if match:
                extracted = self._extract_fields(match, alert_type)
                database_id = extracted.get("database_id", "UNKNOWN")

                self.logger.info("Matched alert: type=%s database=%s", alert_type, database_id)
                return Suggestion(
                    alert_type=alert_type,
                    database_id=database_id,
                    raw_email_subject=subject,
                    raw_email_body=body[:2000],  # Truncate body
                    extracted_data=extracted,
                )

        # No pattern matched — create unknown alert for LLM classification
        self.logger.info("No pattern matched for: %s — creating unknown alert", subject[:100])
        return Suggestion(
            alert_type="unknown",
            database_id="UNKNOWN",
            raw_email_subject=subject,
            raw_email_body=body[:2000],
            extracted_data={"raw_subject": subject, "raw_body": body[:2000]},
        )

    def _extract_fields(self, match: re.Match, alert_type: str) -> dict:
        """Extract named fields from regex match based on alert policy."""
        fields = {}
        field_rules = self._patterns.get_extracted_fields(alert_type)

        for rule in field_rules:
            # Parse rules like "`tablespace_name` = group(1) -- description"
            # Support both "=" and ":" as separator
            if "=" in rule:
                name, accessor = rule.split("=", 1)
            elif ":" in rule:
                name, accessor = rule.split(":", 1)
            else:
                continue
            name = name.strip().strip("`")
            accessor = accessor.strip()

            group_match = re.match(r"group\((\d+)\)", accessor)
            if group_match:
                group_num = int(group_match.group(1))
                try:
                    fields[name] = match.group(group_num)
                except IndexError:
                    fields[name] = None

        # Fallback: use positional groups if no rules matched
        if not fields and match.groups():
            for i, val in enumerate(match.groups(), 1):
                fields[f"group_{i}"] = val

        return fields

    def _save_suggestion(self, suggestion: Suggestion) -> str:
        """Create a new workflow in DETECTED status."""
        # Resolve database via aliases (normalizes to canonical config name)
        db_cfg = self.context.settings.resolve_database(suggestion.database_id)
        if db_cfg:
            suggestion.database_id = db_cfg.name
            environment = db_cfg.environment
        else:
            env_record = self.context.environment_repo.get(suggestion.database_id)
            environment = env_record.environment if env_record else "UNKNOWN"

        workflow = Workflow(
            alert_type=suggestion.alert_type,
            database_id=suggestion.database_id,
            environment=environment,
            status=WorkflowStatus.DETECTED.value,
            suggestion=suggestion.to_json(),
        )
        wf_id = self.context.workflow_repo.create(workflow)
        self.logger.info(
            "Created workflow %s: %s on %s", wf_id, suggestion.alert_type, suggestion.database_id
        )
        return wf_id

    @staticmethod
    def _decode_header(header: str) -> str:
        """Decode email header (handles encoded subjects)."""
        parts = email.header.decode_header(header)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return " ".join(decoded)

    @staticmethod
    def _get_email_body(msg) -> str:
        """Extract plain text body from an email message."""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""
