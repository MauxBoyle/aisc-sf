import json

from aisc_salesforce import app


def test_email_command_previews_without_salesforce_setup(monkeypatch, tmp_path):
    output = []
    monkeypatch.setattr(
        app,
        "_load_dotenv",
        lambda path: (_ for _ in ()).throw(AssertionError("must not authenticate")),
    )
    log_file = tmp_path / "attempts.jsonl"

    assert app.main(["send-external-email-test", "boyle@aisc.org", "--log-file", str(log_file)], output_fn=output.append) == 0

    assert output == [
        "Previewed the fixed test email for boyle@aisc.org; no email was sent.",
        "Subject: [TEST] AISC external-email proof of concept",
        "Body: This is a test of the AISC external-email proof of concept. "
        "No action is required. Please do not reply to this email.",
    ]
    assert json.loads(log_file.read_text())["outcome"] == "previewed"


def test_email_command_send_authenticates_and_uses_sender(monkeypatch, tmp_path):
    output = []
    calls = []
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app.os, "environ", {"SF_CLIENT_ID": "id", "SF_CLIENT_SECRET": "secret"})
    monkeypatch.setattr(app, "get_credentials", lambda values: values)
    monkeypatch.setattr(app, "get_oauth_url", lambda values: "token-url")
    monkeypatch.setattr(app, "request_access_token", lambda credentials, oauth_url: "auth")

    class Client:
        def __init__(self, auth):
            assert auth == "auth"

        def send_external_email_test(self, recipient):
            calls.append(recipient)

    monkeypatch.setattr(app, "SalesforceClient", Client)
    log_file = tmp_path / "attempts.jsonl"

    assert app.main(["send-external-email-test", "boyle@aisc.org", "--send", "--log-file", str(log_file)], output_fn=output.append) == 0

    assert calls == ["boyle@aisc.org"]
    assert output == ["Sent the fixed test email to boyle@aisc.org."]
    assert json.loads(log_file.read_text())["outcome"] == "sent"


def test_email_command_logs_safe_failed_send(monkeypatch, tmp_path):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app.os, "environ", {"SF_CLIENT_ID": "id", "SF_CLIENT_SECRET": "secret"})
    monkeypatch.setattr(app, "get_credentials", lambda values: values)
    monkeypatch.setattr(app, "get_oauth_url", lambda values: "token-url")
    monkeypatch.setattr(app, "request_access_token", lambda credentials, oauth_url: "auth")

    class Client:
        def __init__(self, auth):
            pass

        def send_external_email_test(self, recipient):
            raise app.SalesforceError("provider response with token")

    monkeypatch.setattr(app, "SalesforceClient", Client)
    log_file = tmp_path / "attempts.jsonl"

    assert app.main(["send-external-email-test", "boyle@aisc.org", "--send", "--log-file", str(log_file)]) == 1
    record = json.loads(log_file.read_text())
    assert record["outcome"] == "failed"
    assert record["error_category"] == "salesforce_error"
    assert "token" not in log_file.read_text().lower()
