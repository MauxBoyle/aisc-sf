import pytest
import requests

from aisc_salesforce.dictionary import ExportField
from aisc_salesforce.salesforce import (
    SalesforceClient,
    SalesforceError,
    SalesforceSession,
    get_credentials,
    get_oauth_url,
    request_access_token,
)


class Response:
    def __init__(self, payload, ok=True, text=""):
        self.payload, self.ok, self.text, self.status_code = payload, ok, text, 400

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
