"""Build and deliver the monthly snapshot checklist reminder."""

from __future__ import annotations

import re
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from pathlib import Path

NOT_RUN = "Not Run"
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465
SMTP_TIMEOUT_SECONDS = 30
DEFAULT_RECIPIENTS_PATH = Path("config/monthly_snapshot_recipients.txt")


@dataclass(frozen=True)
class SnapshotReport:
    """One manually run report in the fixed monthly snapshot checklist."""

    source: str
    filename_template: str
    status: str = NOT_RUN

    def filename_for(self, run_date: date) -> str:
        """Return the report filename with ``YYMMDD`` replaced for this run."""
        return self.filename_template.replace("YYMMDD", run_date.strftime("%y%m%d"))


REPORT_CATALOG = (
    SnapshotReport("Salesforce", "CertStatusBR_YYMMDD.csv"),
    SnapshotReport("Salesforce", "NewLast90BR_YYMMDD.csv"),
    SnapshotReport("Salesforce", "ParticipantDetailBR_YYMMDD.csv"),
    SnapshotReport("iMIS", "imisSnapshotAbridged_YYMMDD.csv"),
    SnapshotReport("iMIS", "imisSnapshot_YYMMDD.csv"),
    SnapshotReport("Tableau", "Applications_YYMMDD.csv"),
    SnapshotReport("Tableau", "ApplicantsDetail_YYMMDD.csv"),
    SnapshotReport("Tableau", "Applications_allStatus_YYMMDD"),
    SnapshotReport("Tableau", "NewS-2p0_YYMMDD.csv"),
    SnapshotReport("Tableau", "AppStatusCertPcps_YYMMDD.csv"),
    SnapshotReport("Tableau", "StalledApplicants_YYMMDD.csv"),
    SnapshotReport("Tableau", "Audit_Count_YYMMDD.csv"),
    SnapshotReport("Tableau", "DurationDetail_YYMMDD.csv"),
    SnapshotReport("Tableau", "Certificates_YYMMDD.csv"),
    SnapshotReport("Tableau", "DropComments_YYMMDD.csv"),
)


class MonthlySnapshotReminderError(ValueError):
    """A reminder configuration or input is invalid."""


def render_reminder(run_date: date) -> str:
    """Render the fixed plain-text checklist for one calendar run date."""
    lines = [
        f"Monthly snapshot checklist — {run_date.isoformat()}",
        "",
        "All reports below still require manual work.",
    ]
    source = None
    for report in REPORT_CATALOG:
        if report.source != source:
            source = report.source
            lines.extend(("", f"{source}:"))
        lines.append(f"  - {report.filename_for(run_date)} — {report.status}")
    lines.extend(
        (
            "",
            "For every report marked Not Run: run the snapshot in its source, use the",
            "listed filename, and move it to the appropriate shared Google Drive folder.",
        )
    )
    return "\n".join(lines)


def reminder_subject(run_date: date) -> str:
    """Return the stable subject line for a checklist run."""
    return f"Monthly snapshot checklist — {run_date.isoformat()}"


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load_recipients(path: Path = DEFAULT_RECIPIENTS_PATH) -> tuple[str, ...]:
    """Read unique valid recipient addresses from a simple text file."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise MonthlySnapshotReminderError(
            f"Could not read recipient file: {path}"
        ) from error

    recipients: list[str] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        address = line.strip()
        if not address or address.startswith("#"):
            continue
        if not _EMAIL_PATTERN.fullmatch(address):
            raise MonthlySnapshotReminderError(
                f"Invalid email address in {path} on line {line_number}."
            )
        normalized = address.casefold()
        if normalized not in seen:
            recipients.append(address)
            seen.add(normalized)
    if not recipients:
        raise MonthlySnapshotReminderError(f"Recipient file contains no email addresses: {path}")
    return tuple(recipients)


def email_configuration(environment: dict[str, str]) -> tuple[str, str]:
    """Read the Gmail sender and app password from private configuration."""
    names = ("EMAIL_USERNAME", "EMAIL_APP_PASSWORD")
    missing = [name for name in names if not environment.get(name, "").strip()]
    if missing:
        raise MonthlySnapshotReminderError(
            "Missing email configuration: " + ", ".join(missing)
        )
    return tuple(environment[name].strip() for name in names)


class GmailReminderSender:
    """Deliver plain-text checklist messages through Gmail's SSL SMTP service."""

    def __init__(
        self,
        username: str,
        app_password: str,
        *,
        smtp_factory: Callable[..., smtplib.SMTP_SSL] = smtplib.SMTP_SSL,
    ):
        self.username = username
        self.app_password = app_password
        self.smtp_factory = smtp_factory

    def send(
        self, *, run_date: date, recipients: tuple[str, ...], test: bool = False
    ) -> None:
        """Send normally to recipients, or only back to the sender in test mode."""
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = self.username if test else ", ".join(recipients)
        message["Subject"] = reminder_subject(run_date)
        message.set_content(render_reminder(run_date))

        envelope_recipients = (self.username,) if test else _with_sender_bcc(
            recipients, self.username
        )
        with self.smtp_factory(
            GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS
        ) as connection:
            connection.login(self.username, self.app_password)
            connection.send_message(
                message, from_addr=self.username, to_addrs=envelope_recipients
            )


def _with_sender_bcc(recipients: tuple[str, ...], sender: str) -> tuple[str, ...]:
    """Add a private sender copy only when it is not already a recipient."""
    if sender.casefold() in {recipient.casefold() for recipient in recipients}:
        return recipients
    return (*recipients, sender)
