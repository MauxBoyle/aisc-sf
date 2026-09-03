"""Safe, fixed-content external-email proof-of-concept support."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .salesforce import SalesforceError

APPROVED_RECIPIENTS = frozenset(
    {"boyle@aisc.org", "mauxboyle@gmail.com", "maureen7780@yahoo.com"}
)


@dataclass(frozen=True)
class FixedTestEmailMessage:
    """The only email content this proof of concept is permitted to send."""

    template_id: str
    subject: str
    body: str


FIXED_TEST_MESSAGE = FixedTestEmailMessage(
    template_id="aisc-external-email-poc-v1",
    subject="[TEST] AISC external-email proof of concept",
    body=(
        "This is a test of the AISC external-email proof of concept. "
        "No action is required. Please do not reply to this email."
    ),
)


class EmailSender(Protocol):
    """A transport that can deliver the one permitted test message."""

    def send(self, recipient: str, message: FixedTestEmailMessage) -> None:
        """Send the approved fixed message to an approved recipient."""


class ExternalEmailRecipientError(ValueError):
    """The supplied recipient is not one of the explicitly approved addresses."""


@dataclass(frozen=True)
class ExternalEmailAttempt:
    """A safe summary of a preview or send attempt."""

    recipient: str
    mode: str
    transport: str
    template_id: str
    outcome: str
    message: FixedTestEmailMessage
    error_category: str | None = None


class ExternalEmailService:
    """Validate recipient addresses before previewing or sending anything."""

    def __init__(self, sender: EmailSender | None = None):
        self.sender = sender

    def attempt(self, recipient: str, *, send: bool) -> ExternalEmailAttempt:
        """Preview, or explicitly send, the single fixed test email."""
        if recipient not in APPROVED_RECIPIENTS:
            raise ExternalEmailRecipientError(
                "Recipient is not on the approved external-email test allowlist."
            )
        if not send:
            return ExternalEmailAttempt(
                recipient=recipient,
                mode="preview",
                transport="none",
                template_id=FIXED_TEST_MESSAGE.template_id,
                outcome="previewed",
                message=FIXED_TEST_MESSAGE,
            )
        if self.sender is None:
            raise RuntimeError("A sender is required when --send is used.")
        try:
            self.sender.send(recipient, FIXED_TEST_MESSAGE)
        except SalesforceError:
            return self._failed_attempt(recipient, "salesforce_error")
        except Exception:
            return self._failed_attempt(recipient, "sender_error")
        return ExternalEmailAttempt(
            recipient=recipient,
            mode="send",
            transport="salesforce_apex",
            template_id=FIXED_TEST_MESSAGE.template_id,
            outcome="sent",
            message=FIXED_TEST_MESSAGE,
        )

    @staticmethod
    def _failed_attempt(recipient: str, error_category: str) -> ExternalEmailAttempt:
        return ExternalEmailAttempt(
            recipient=recipient,
            mode="send",
            transport="salesforce_apex",
            template_id=FIXED_TEST_MESSAGE.template_id,
            outcome="failed",
            message=FIXED_TEST_MESSAGE,
            error_category=error_category,
        )


class SalesforceExternalEmailSender:
    """Send through Apex, which independently enforces the same limits."""

    def __init__(self, client: object):
        self.client = client

    def send(self, recipient: str, message: FixedTestEmailMessage) -> None:
        if message != FIXED_TEST_MESSAGE:
            raise ValueError("Only the fixed external-email test message is allowed.")
        self.client.send_external_email_test(recipient)  # type: ignore[attr-defined]


def append_attempt_log(path: Path, attempt: ExternalEmailAttempt) -> None:
    """Append a credential-free JSON Lines record for an approved attempt."""
    record: dict[str, str] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "recipient": attempt.recipient,
        "mode": attempt.mode,
        "transport": attempt.transport,
        "template_id": attempt.template_id,
        "outcome": attempt.outcome,
    }
    if attempt.error_category is not None:
        record["error_category"] = attempt.error_category
    with path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(record, sort_keys=True) + "\n")
