from datetime import date

import pytest

from aisc_salesforce.monthly_snapshot_reminder import (
    REPORT_CATALOG,
    GmailReminderSender,
    MonthlySnapshotReminderError,
    load_recipients,
    render_reminder,
)


def test_catalog_contains_the_fixed_fifteen_reports_in_source_groups():
    assert len(REPORT_CATALOG) == 15
    assert [report.source for report in REPORT_CATALOG] == [
        *["Salesforce"] * 3,
        *["iMIS"] * 2,
        *["Tableau"] * 10,
    ]
    assert REPORT_CATALOG[7].filename_template == "Applications_allStatus_YYMMDD"


def test_render_reminder_substitutes_date_and_lists_manual_work():
    rendered = render_reminder(date(2026, 9, 30))

    assert "Monthly snapshot checklist — 2026-09-30" in rendered
    assert "Salesforce:" in rendered
    assert "iMIS:" in rendered
    assert "Tableau:" in rendered
    assert "CertStatusBR_260930.csv — Not Run" in rendered
    assert "Applications_allStatus_260930 — Not Run" in rendered
    assert rendered.count("Not Run") == 16  # Fifteen statuses plus the instructions.
    assert "move it to the appropriate shared Google Drive folder" in rendered


def test_load_recipients_ignores_comments_and_removes_case_insensitive_duplicates(tmp_path):
    recipients_file = tmp_path / "recipients.txt"
    recipients_file.write_text("# comment\nfirst@example.org\n\nFIRST@example.org\nsecond@example.org\n")

    assert load_recipients(recipients_file) == ("first@example.org", "second@example.org")


@pytest.mark.parametrize("contents", ["not-an-email\n", "\n# comment\n"])
def test_load_recipients_rejects_invalid_or_empty_files(tmp_path, contents):
    recipients_file = tmp_path / "recipients.txt"
    recipients_file.write_text(contents)

    with pytest.raises(MonthlySnapshotReminderError):
        load_recipients(recipients_file)


def test_gmail_sender_routes_live_message_to_recipients_and_private_sender_copy():
    calls = {}

    class FakeSmtp:
        def __init__(self, host, port, *, timeout):
            calls["connection"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, username, password):
            calls["login"] = (username, password)

        def send_message(self, message, *, from_addr, to_addrs):
            calls["message"] = message
            calls["route"] = (from_addr, to_addrs)

    GmailReminderSender("sender@example.org", "app-password", smtp_factory=FakeSmtp).send(
        run_date=date(2026, 9, 30), recipients=("team@example.org",)
    )

    assert calls["connection"] == ("smtp.gmail.com", 465, 30)
    assert calls["login"] == ("sender@example.org", "app-password")
    assert calls["route"] == (
        "sender@example.org",
        ("team@example.org", "sender@example.org"),
    )
    assert calls["message"]["To"] == "team@example.org"


def test_gmail_sender_routes_test_message_only_to_sender():
    calls = {}

    class FakeSmtp:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, *args):
            pass

        def send_message(self, message, *, from_addr, to_addrs):
            calls["route"] = (from_addr, to_addrs)
            calls["to"] = message["To"]

    GmailReminderSender("sender@example.org", "app-password", smtp_factory=FakeSmtp).send(
        run_date=date(2026, 9, 30), recipients=("team@example.org",), test=True
    )

    assert calls == {
        "route": ("sender@example.org", ("sender@example.org",)),
        "to": "sender@example.org",
    }
