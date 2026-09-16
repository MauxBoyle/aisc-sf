import json

import pytest

from aisc_salesforce.external_email import (
    FIXED_TEST_MESSAGE,
    ExternalEmailRecipientError,
    ExternalEmailService,
    SalesforceExternalEmailSender,
    append_attempt_log,
)
from aisc_salesforce.salesforce import (
    SalesforceClient,
    SalesforceError,
    SalesforceSession,
)


class RecordingSender:
    def __init__(self):
        self.calls = []

    def send(self, recipient, message):
        self.calls.append((recipient, message))


def test_preview_uses_fixed_template_without_invoking_sender():
    sender = RecordingSender()

    result = ExternalEmailService(sender).attempt("boyle@aisc.org", send=False)

    assert result.outcome == "previewed"
    assert result.message is FIXED_TEST_MESSAGE
    assert sender.calls == []


@pytest.mark.parametrize("recipient", ["BOYLE@AISC.ORG", " boyle@aisc.org ", "person@example.com"])
def test_only_exact_approved_recipients_are_accepted(recipient):
    with pytest.raises(ExternalEmailRecipientError):
        ExternalEmailService().attempt(recipient, send=False)


def test_explicit_send_invokes_sender_with_only_fixed_template():
    sender = RecordingSender()

    result = ExternalEmailService(sender).attempt("mauxboyle@gmail.com", send=True)

    assert result.outcome == "sent"
    assert sender.calls == [("mauxboyle@gmail.com", FIXED_TEST_MESSAGE)]
    assert FIXED_TEST_MESSAGE.subject == "[TEST] AISC external-email proof of concept"
    assert "no action is required" in FIXED_TEST_MESSAGE.body.casefold()
    assert "do not reply" in FIXED_TEST_MESSAGE.body.casefold()


def test_failed_send_has_safe_error_category():
    class FailingSender:
        def send(self, recipient, message):
            raise SalesforceError("access token secret-value was rejected")

    result = ExternalEmailService(FailingSender()).attempt("maureen7780@yahoo.com", send=True)

    assert result.outcome == "failed"
    assert result.error_category == "salesforce_error"


def test_json_lines_log_contains_only_safe_fields(tmp_path):
    log_file = tmp_path / "attempts.jsonl"
    result = ExternalEmailService().attempt("boyle@aisc.org", send=False)

    append_attempt_log(log_file, result)

    record = json.loads(log_file.read_text())
    assert set(record) == {
        "timestamp",
        "recipient",
        "mode",
        "transport",
        "template_id",
        "outcome",
    }
    serialized = log_file.read_text().lower()
    assert "token" not in serialized
    assert "password" not in serialized
    assert "api_key" not in serialized


def test_salesforce_sender_posts_only_recipient_to_fixed_endpoint():
    class Client:
        def __init__(self):
            self.recipient = None

        def send_external_email_test(self, recipient):
            self.recipient = recipient

    client = Client()
    SalesforceExternalEmailSender(client).send("boyle@aisc.org", FIXED_TEST_MESSAGE)

    assert client.recipient == "boyle@aisc.org"


def test_salesforce_client_posts_recipient_to_apex_endpoint():
    calls = []

    class Response:
        ok = True

    class Session:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    client = SalesforceClient(SalesforceSession("https://example.test", "token"), Session())
    client.send_external_email_test("boyle@aisc.org")

    assert calls == [
        (
            "https://example.test/services/apexrest/aisc-external-email-test",
            {
                "headers": {
                    "Authorization": "Bearer token",
                    "Content-Type": "application/json",
                },
                "timeout": 30,
                "json": {"recipient": "boyle@aisc.org"},
            },
        )
    ]
