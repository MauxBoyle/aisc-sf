from aisc_salesforce import app


def test_preview_does_not_load_credentials_and_honors_date(monkeypatch):
    output = []
    monkeypatch.setattr(
        app,
        "_load_dotenv",
        lambda path: (_ for _ in ()).throw(AssertionError("preview must not load .env")),
    )

    assert app.main(["monthly-snapshot-reminder", "--run-date", "2026-09-30"], output_fn=output.append) == 0

    assert "CertStatusBR_260930.csv — Not Run" in output[0]
    assert output[-1] == "Preview only; no email was sent."


def test_invalid_run_date_returns_safe_failure(capsys):
    assert app.main(["monthly-snapshot-reminder", "--run-date", "09-30-2026"]) == 1
    assert "--run-date must use YYYY-MM-DD" in capsys.readouterr().err


def test_test_requires_send(capsys):
    assert app.main(["monthly-snapshot-reminder", "--test"]) == 1
    assert "--test requires --send" in capsys.readouterr().err


def test_send_requires_email_configuration(monkeypatch, capsys):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app.os, "environ", {})

    assert app.main(["monthly-snapshot-reminder", "--send"]) == 1
    assert "Missing email configuration" in capsys.readouterr().err


def test_test_and_live_send_route_expected_values(monkeypatch):
    sent = []
    output = []
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(
        app.os,
        "environ",
        {"EMAIL_USERNAME": "sender@example.org", "EMAIL_APP_PASSWORD": "secret"},
    )
    monkeypatch.setattr(app, "load_recipients", lambda path: ("team@example.org",))

    class Sender:
        def __init__(self, username, password):
            assert (username, password) == ("sender@example.org", "secret")

        def send(self, *, run_date, recipients, test):
            sent.append((run_date.isoformat(), recipients, test))

    monkeypatch.setattr(app, "GmailReminderSender", Sender)

    assert app.main(["monthly-snapshot-reminder", "--send", "--test", "--run-date", "2026-09-30"], output_fn=output.append) == 0
    assert app.main(["monthly-snapshot-reminder", "--send", "--run-date", "2026-09-30"], output_fn=output.append) == 0

    assert sent == [
        ("2026-09-30", ("team@example.org",), True),
        ("2026-09-30", ("team@example.org",), False),
    ]


def test_send_failure_has_generic_message(monkeypatch, capsys):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(
        app.os,
        "environ",
        {"EMAIL_USERNAME": "sender@example.org", "EMAIL_APP_PASSWORD": "secret"},
    )
    monkeypatch.setattr(app, "load_recipients", lambda path: ("team@example.org",))

    class Sender:
        def __init__(self, *args):
            pass

        def send(self, **kwargs):
            raise RuntimeError("provider said password is secret")

    monkeypatch.setattr(app, "GmailReminderSender", Sender)

    assert app.main(["monthly-snapshot-reminder", "--send"]) == 1
    error = capsys.readouterr().err
    assert "Email delivery was not completed" in error
    assert "secret" not in error
