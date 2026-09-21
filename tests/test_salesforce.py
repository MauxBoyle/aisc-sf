import json
from datetime import UTC, datetime

import pytest
import requests

from aisc_salesforce.dictionary import ExportField
from aisc_salesforce.salesforce import (
    HTTP_METHOD_OPERATIONS,
    SalesforceClient,
    SalesforceError,
    SalesforceSession,
    get_credentials,
    get_oauth_url,
    request_access_token,
)
from aisc_salesforce.salesforce_audit import SalesforceAuditWriter, caller_from_argv


class Response:
    def __init__(self, payload, ok=True, text="", status_code=None):
        self.payload, self.ok, self.text = payload, ok, text
        self.status_code = (
            status_code if status_code is not None else (200 if ok else 400)
        )

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, *args, **kwargs):
        self.calls.append(("post", args, kwargs))
        return self.responses.pop(0)

    def get(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return self.responses.pop(0)

    def patch(self, *args, **kwargs):
        self.calls.append(("patch", args, kwargs))
        return self.responses.pop(0)


def audit_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def audit_writer(tmp_path):
    return SalesforceAuditWriter(
        tmp_path / "salesforce",
        run_id="test-run-id",
        caller="aisc_salesforce snapshot",
        now=lambda: datetime(2026, 9, 21, 12, 30, tzinfo=UTC),
    )


def credentials():
    return {
        "SF_CLIENT_ID": "id",
        "SF_CLIENT_SECRET": "secret",
    }


def test_token_request_uses_client_credentials():
    session = Session(
        [
            Response(
                {
                    "instance_url": "https://example.my.salesforce.com",
                    "access_token": "access",
                }
            )
        ]
    )
    auth = request_access_token(credentials(), session=session)
    assert auth.instance_url == "https://example.my.salesforce.com"
    assert session.calls[0][2]["data"] == {
        "grant_type": "client_credentials",
        "client_id": "id",
        "client_secret": "secret",
    }


def test_oauth_url_accepts_org_or_full_token_url():
    assert (
        get_oauth_url({"SF_LOGIN_URL": "https://aisc.my.salesforce.com"})
        == "https://aisc.my.salesforce.com/services/oauth2/token"
    )
    assert (
        get_oauth_url(
            {"SF_LOGIN_URL": "https://aisc.my.salesforce.com/services/oauth2/token"}
        )
        == "https://aisc.my.salesforce.com/services/oauth2/token"
    )
    with pytest.raises(SalesforceError, match="valid HTTPS"):
        get_oauth_url({"SF_LOGIN_URL": "not a URL"})


def test_missing_credentials_and_auth_error_are_clear():
    with pytest.raises(SalesforceError, match="SF_CLIENT_SECRET"):
        get_credentials({"SF_CLIENT_ID": "id"})
    with pytest.raises(SalesforceError, match="rejected authentication"):
        request_access_token(
            credentials(),
            session=Session([Response({"error_description": "bad login"}, ok=False)]),
        )


def test_network_error_is_wrapped():
    class NetworkSession:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("offline")

    with pytest.raises(SalesforceError, match="Could not reach"):
        request_access_token(credentials(), session=NetworkSession())


def test_query_all_follows_pages_and_preserves_none():
    session = Session(
        [
            Response(
                {
                    "done": False,
                    "records": [{"Name": "First", "Phone": None}],
                    "nextRecordsUrl": "/services/data/v60.0/query/next",
                }
            ),
            Response({"done": True, "records": [{"Name": "Second", "Phone": "123"}]}),
        ]
    )
    client = SalesforceClient(
        SalesforceSession("https://example", "access"), session=session
    )
    records = client.query_all(
        "Account", [ExportField("Name", "name"), ExportField("Phone", "phone")]
    )
    assert records[0]["Phone"] is None
    assert len(records) == 2
    assert session.calls[0][2]["params"]["q"] == "SELECT Name, Phone FROM Account"
    assert session.calls[1][1][0] == "https://example/services/data/v60.0/query/next"


def test_query_failure_names_object():
    client = SalesforceClient(
        SalesforceSession("https://example", "access"),
        session=Session([Response([{"message": "bad field"}], ok=False)]),
    )
    with pytest.raises(SalesforceError, match="Account.*bad field"):
        client.query_all("Account", [ExportField("Nope", "nope")])


def test_filtered_query_supports_sorting_and_pagination():
    session = Session(
        [
            Response(
                {
                    "done": False,
                    "records": [{"Id": "one"}],
                    "nextRecordsUrl": "/next-page",
                }
            ),
            Response({"done": True, "records": [{"Id": "two"}]}),
        ]
    )
    client = SalesforceClient(SalesforceSession("https://example", "token"), session)

    records = client.query_records(
        "Case",
        ["Id", "Subject"],
        where="AccountId = 'account'",
        order_by="CreatedDate DESC",
    )

    assert records == [{"Id": "one"}, {"Id": "two"}]
    assert session.calls[0][2]["params"]["q"] == (
        "SELECT Id, Subject FROM Case WHERE AccountId = 'account' "
        "ORDER BY CreatedDate DESC"
    )
    assert session.calls[1][1][0] == "https://example/next-page"


def test_describe_object_uses_v60_and_returns_field_types():
    session = Session(
        [
            Response(
                {
                    "fields": [
                        {"name": "Status", "type": "picklist"},
                        {"name": "Tags__c", "type": "multipicklist"},
                        {"name": "Subject", "type": "string"},
                    ]
                }
            )
        ]
    )
    client = SalesforceClient(SalesforceSession("https://example", "token"), session)

    assert client.describe_object("Case") == {
        "Status": "picklist",
        "Tags__c": "multipicklist",
        "Subject": "string",
    }
    assert session.calls[0][1][0] == (
        "https://example/services/data/v60.0/sobjects/Case/describe"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"fields": "not a list"},
        {"fields": [{"name": "Status"}]},
        {"fields": [{"name": "", "type": "picklist"}]},
        {
            "fields": [
                {"name": "Status", "type": "picklist"},
                {"name": "Status", "type": "string"},
            ]
        },
    ],
)
def test_describe_object_rejects_malformed_metadata(payload):
    client = SalesforceClient(
        SalesforceSession("https://example", "token"),
        Session([Response(payload)]),
    )

    with pytest.raises(SalesforceError, match="Describe response for Case"):
        client.describe_object("Case")


def test_record_create_update_and_retrieve_use_salesforce_rest_api():
    session = Session(
        [
            Response({"id": "500-created", "success": True}),
            Response({}, ok=True),
            Response({"Id": "500-created", "CaseNumber": "1234"}),
        ]
    )
    client = SalesforceClient(SalesforceSession("https://example", "token"), session)

    assert client.create_record("Case", {"Subject": "Test"}) == "500-created"
    client.update_record("Case", "500-created", {"Status": "Pending"})
    record = client.get_record("Case", "500-created", ["Id", "CaseNumber"])

    assert record["CaseNumber"] == "1234"
    assert session.calls[0] == (
        "post",
        ("https://example/services/data/v60.0/sobjects/Case",),
        {
            "headers": {
                "Authorization": "Bearer token",
                "Content-Type": "application/json",
            },
            "json": {"Subject": "Test"},
            "timeout": 30,
        },
    )
    assert session.calls[1][0] == "patch"
    assert session.calls[2][2]["params"] == {"fields": "Id,CaseNumber"}


def test_feed_messages_follow_pages_and_post_with_subject_id():
    session = Session(
        [
            Response(
                {
                    "elements": [
                        {
                            "body": {
                                "messageSegments": [{"type": "Text", "text": "First"}]
                            }
                        }
                    ],
                    "nextPageUrl": "/feed-next",
                }
            ),
            Response(
                {
                    "elements": [
                        {
                            "body": {
                                "messageSegments": [{"type": "Text", "text": "Second"}]
                            }
                        }
                    ]
                }
            ),
            Response({"id": "feed-item"}),
        ]
    )
    client = SalesforceClient(SalesforceSession("https://example", "token"), session)

    assert client.get_feed_messages("500-case") == ["First", "Second"]
    client.post_feed_message("500-case", "A message")

    assert session.calls[1][1][0] == "https://example/feed-next"
    assert session.calls[2][2]["json"] == {
        "body": {"messageSegments": [{"type": "Text", "text": "A message"}]},
        "feedElementType": "FeedItem",
        "subjectId": "500-case",
    }


def test_write_and_feed_http_errors_are_wrapped():
    client = SalesforceClient(
        SalesforceSession("https://example", "token"),
        Session([Response([{"message": "not allowed"}], ok=False)]),
    )

    with pytest.raises(SalesforceError, match="create Case.*not allowed"):
        client.create_record("Case", {"Subject": "Test"})

    feed_client = SalesforceClient(
        SalesforceSession("https://example", "token"),
        Session([Response("server broke", ok=False, text="server broke")]),
    )
    with pytest.raises(SalesforceError, match="post Chatter.*server broke"):
        feed_client.post_feed_message("500-case", "Test")


def test_salesforce_error_preserves_duplicate_rule_details():
    session = Session(
        [
            Response(
                [
                    {
                        "message": "Use one of these records?",
                        "errorCode": "DUPLICATES_DETECTED",
                    }
                ],
                ok=False,
            )
        ]
    )
    client = SalesforceClient(SalesforceSession("https://example", "token"), session)

    with pytest.raises(SalesforceError, match="Use one of these") as caught:
        client.create_record("Contact", {"LastName": "Smith"})

    assert caught.value.error_code == "DUPLICATES_DETECTED"
    assert caught.value.salesforce_message == "Use one of these records?"


def test_audit_logs_direct_retrieval_metadata_without_request_content(tmp_path):
    writer = audit_writer(tmp_path)
    client = SalesforceClient(
        SalesforceSession("https://example", "secret-token"),
        Session([Response({"Id": "500-case", "Subject": "private subject"})]),
        audit_writer=writer,
    )

    client.get_record("Case", "500-case", ["Id", "Subject"])

    path = tmp_path / "salesforce" / "salesforce-audit-2026-09-21.jsonl"
    event = audit_events(path)[0]
    assert event == {
        "caller": "aisc_salesforce snapshot",
        "http_method": "GET",
        "http_status": 200,
        "object_type": "Case",
        "operation": "retrieve",
        "record_id": "500-case",
        "run_id": "test-run-id",
        "success": True,
        "timestamp": "2026-09-21T12:30:00Z",
    }
    assert "secret-token" not in path.read_text()
    assert "private subject" not in path.read_text()
    assert "Subject" not in path.read_text()


def test_audit_logs_query_records_and_failures_without_soql_or_error_message(tmp_path):
    writer = audit_writer(tmp_path)
    client = SalesforceClient(
        SalesforceSession("https://example", "token"),
        Session([Response({"done": True, "records": [{"Id": "one"}, {"Id": "two"}]})]),
        audit_writer=writer,
    )

    client.query_records("Case", ["Id", "Subject"], where="Subject = 'secret'")

    failed = SalesforceClient(
        SalesforceSession("https://example", "token"),
        Session(
            [
                Response(
                    [{"message": "private Salesforce error", "errorCode": "BAD_FIELD"}],
                    ok=False,
                )
            ]
        ),
        audit_writer=writer,
    )
    with pytest.raises(SalesforceError):
        failed.query_records("Case", ["Subject"], where="Subject = 'secret'")

    events = audit_events(tmp_path / "salesforce" / "salesforce-audit-2026-09-21.jsonl")
    assert [
        (event["record_id"], event["record_count"], event["success"])
        for event in events[:2]
    ] == [
        ("one", 1, True),
        ("two", 1, True),
    ]
    assert events[2] == {
        "caller": "aisc_salesforce snapshot",
        "error_code": "BAD_FIELD",
        "error_type": "SalesforceError",
        "http_method": "GET",
        "http_status": 400,
        "object_type": "Case",
        "operation": "query",
        "run_id": "test-run-id",
        "success": False,
        "timestamp": "2026-09-21T12:30:00Z",
    }
    contents = (
        tmp_path / "salesforce" / "salesforce-audit-2026-09-21.jsonl"
    ).read_text()
    assert "SELECT" not in contents
    assert "private Salesforce error" not in contents
    assert "secret" not in contents


def test_audit_logs_failed_direct_retrieval_without_salesforce_message(tmp_path):
    writer = audit_writer(tmp_path)
    client = SalesforceClient(
        SalesforceSession("https://example", "token"),
        Session(
            [
                Response(
                    [{"message": "do not store this", "errorCode": "NOT_FOUND"}],
                    ok=False,
                    status_code=404,
                )
            ]
        ),
        audit_writer=writer,
    )

    with pytest.raises(SalesforceError):
        client.get_record("Case", "missing-case", ["Id"])

    path = tmp_path / "salesforce" / "salesforce-audit-2026-09-21.jsonl"
    assert audit_events(path)[0] == {
        "caller": "aisc_salesforce snapshot",
        "error_code": "NOT_FOUND",
        "error_type": "SalesforceError",
        "http_method": "GET",
        "http_status": 404,
        "object_type": "Case",
        "operation": "retrieve",
        "record_id": "missing-case",
        "run_id": "test-run-id",
        "success": False,
        "timestamp": "2026-09-21T12:30:00Z",
    }
    assert "do not store this" not in path.read_text()


def test_audit_logs_mutations_and_chatter_operations(tmp_path):
    writer = audit_writer(tmp_path)
    session = Session(
        [
            Response({"id": "new-case"}),
            Response({}),
            Response({"elements": []}),
            Response({"id": "feed-item"}),
        ]
    )
    client = SalesforceClient(
        SalesforceSession("https://example", "token"), session, audit_writer=writer
    )

    assert client.create_record("Case", {"Subject": "sensitive"}) == "new-case"
    client.update_record("Case", "new-case", {"Status": "Pending"})
    assert client.get_feed_messages("new-case") == []
    client.post_feed_message("new-case", "sensitive message")

    events = audit_events(tmp_path / "salesforce" / "salesforce-audit-2026-09-21.jsonl")
    assert [
        (event["operation"], event["http_method"], event["record_id"])
        for event in events
    ] == [
        ("create", "POST", "new-case"),
        ("update", "PATCH", "new-case"),
        ("chatter_read", "GET", "new-case"),
        ("chatter_post", "POST", "new-case"),
    ]
    assert events[2]["record_count"] == 0
    assert "sensitive" not in str(events)


def test_audit_writer_uses_utc_rotation_retention_and_shared_run_id(tmp_path):
    directory = tmp_path / "salesforce"
    directory.mkdir()
    for day in range(1, 32):
        (directory / f"salesforce-audit-2026-08-{day:02}.jsonl").write_text("old\n")
    first = SalesforceAuditWriter(
        directory,
        caller="program command",
        now=lambda: datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
    )
    second = SalesforceAuditWriter(
        directory,
        caller="program command",
        now=lambda: datetime(2026, 9, 21, 0, 1, tzinfo=UTC),
    )

    first.write({"operation": "retrieve", "success": True})
    second.write({"operation": "retrieve", "success": True})

    assert len(list(directory.glob("salesforce-audit-*.jsonl"))) == 30
    events = audit_events(directory / "salesforce-audit-2026-09-21.jsonl")
    assert first.run_id == second.run_id
    assert {event["run_id"] for event in events} == {first.run_id}
    assert all(event["timestamp"].endswith("Z") for event in events)


def test_audit_writer_failures_do_not_interrupt_salesforce_work(tmp_path):
    writer = audit_writer(tmp_path)
    writer.directory = tmp_path / "not-a-directory"
    writer.directory.write_text("blocks audit directory creation")
    client = SalesforceClient(
        SalesforceSession("https://example", "token"),
        Session([Response({"Id": "500-case"})]),
        audit_writer=writer,
    )

    assert client.get_record("Case", "500-case", ["Id"]) == {"Id": "500-case"}


def test_caller_keeps_only_program_and_command():
    assert (
        caller_from_argv(
            ["/usr/bin/aisc_salesforce", "snapshot", "--token", "secret", "SELECT Id"]
        )
        == "aisc_salesforce snapshot"
    )
    assert (
        caller_from_argv(["/usr/bin/python", "https://bad.example/token"]) == "python"
    )
    assert HTTP_METHOD_OPERATIONS["DELETE"] == "delete"
