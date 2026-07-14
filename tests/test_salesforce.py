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
