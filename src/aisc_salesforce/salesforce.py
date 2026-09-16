"""Salesforce OAuth2, SOQL, record, and Chatter helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from .dictionary import ExportField

OAUTH_URL = "https://login.salesforce.com/services/oauth2/token"
API_VERSION = "v60.0"
REQUIRED_CREDENTIALS = (
    "SF_CLIENT_ID",
    "SF_CLIENT_SECRET",
)


class SalesforceError(RuntimeError):
    """Salesforce rejected a request or could not be reached."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        salesforce_message: str | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.salesforce_message = salesforce_message


@dataclass(frozen=True)
class SalesforceSession:
    """The non-secret information needed to make API requests."""

    instance_url: str
    access_token: str


def get_credentials(environment: dict[str, str]) -> dict[str, str]:
    """Read required credentials, with a safe missing-value error."""
    missing = [
        name for name in REQUIRED_CREDENTIALS if not environment.get(name, "").strip()
    ]
    if missing:
        raise SalesforceError("Missing Salesforce configuration: " + ", ".join(missing))
    return {name: environment[name] for name in REQUIRED_CREDENTIALS}


def get_oauth_url(environment: dict[str, str]) -> str:
    """Return the configured token endpoint, or Salesforce's production endpoint.

    ``SF_LOGIN_URL`` can be either an org URL or the complete OAuth token URL.
    """
    login_url = environment.get("SF_LOGIN_URL", "").strip()
    if not login_url:
        return OAUTH_URL
    parsed = urlparse(login_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SalesforceError("SF_LOGIN_URL must be a valid HTTPS URL.")
    return (
        login_url.rstrip("/")
        if login_url.rstrip("/").endswith("/services/oauth2/token")
        else login_url.rstrip("/") + "/services/oauth2/token"
    )


def request_access_token(
    credentials: dict[str, str],
    session: requests.Session | Any = requests,
    oauth_url: str = OAUTH_URL,
) -> SalesforceSession:
    """Use Salesforce's client-credentials OAuth2 flow to get an access token."""
    payload = {
        "grant_type": "client_credentials",
        "client_id": credentials["SF_CLIENT_ID"],
        "client_secret": credentials["SF_CLIENT_SECRET"],
    }
    try:
        response = session.post(oauth_url, data=payload, timeout=30)
    except requests.RequestException as error:
        raise SalesforceError(
            f"Could not reach Salesforce for authentication: {error}"
        ) from error
    if not response.ok:
        raise SalesforceError(
            f"Salesforce rejected authentication: {_response_details(response)}"
        )
    try:
        data = response.json()
        return SalesforceSession(data["instance_url"], data["access_token"])
    except (KeyError, ValueError, TypeError) as error:
        raise SalesforceError(
            "Salesforce authentication response was incomplete."
        ) from error


class SalesforceClient:
    """A small Salesforce REST client used by the application services."""

    def __init__(
        self, auth: SalesforceSession, session: requests.Session | Any = requests
    ):
        self.auth = auth
        self.session = session
        self.headers = {"Authorization": f"Bearer {auth.access_token}"}

    def query_all(
        self, object_name: str, fields: list[ExportField]
    ) -> list[dict[str, Any]]:
        """Return all rows for an object, following Salesforce pagination links."""
        return self.query_records(object_name, [field.api_name for field in fields])

    def query_records(
        self,
        object_name: str,
        fields: list[str],
        *,
        where: str | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query selected fields, optionally filtering and sorting the records."""
        field_names = ", ".join(fields)
        soql = f"SELECT {field_names} FROM {object_name}"
        if where:
            soql += f" WHERE {where}"
        if order_by:
            soql += f" ORDER BY {order_by}"
        url = f"{self.auth.instance_url}/services/data/{API_VERSION}/query"
        params: dict[str, str] | None = {"q": soql}
        records: list[dict[str, Any]] = []
        while url:
            response = self._request(
                "get",
                url,
                action=f"query {object_name}",
                params=params,
            )
            try:
                payload = response.json()
                records.extend(payload.get("records", []))
                done = payload["done"]
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise SalesforceError(
                    f"Invalid Salesforce query response for {object_name}."
                ) from error
            next_url = payload.get("nextRecordsUrl")
            url = self._absolute_url(next_url) if not done and next_url else None
            if not done and not next_url:
                raise SalesforceError(
                    f"Salesforce query for {object_name} ended without a next page."
                )
            params = None
        return records

    def describe_object(self, object_name: str) -> dict[str, str]:
        """Return the API name and Salesforce type of every object field."""
        url = (
            f"{self.auth.instance_url}/services/data/{API_VERSION}"
            f"/sobjects/{object_name}/describe"
        )
        response = self._request("get", url, action=f"describe {object_name}")
        try:
            payload = response.json()
            raw_fields = payload["fields"]
            if not isinstance(raw_fields, list):
                raise TypeError
            field_types: dict[str, str] = {}
            for raw_field in raw_fields:
                if not isinstance(raw_field, dict):
                    raise TypeError
                name = raw_field["name"]
                field_type = raw_field["type"]
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(field_type, str)
                    or not field_type
                    or name in field_types
                ):
                    raise TypeError
                field_types[name] = field_type
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise SalesforceError(
                f"Invalid Salesforce Describe response for {object_name}."
            ) from error
        return field_types

    def create_record(self, object_name: str, values: dict[str, Any]) -> str:
        """Create one Salesforce record and return its new record ID."""
        url = (
            f"{self.auth.instance_url}/services/data/{API_VERSION}"
            f"/sobjects/{object_name}"
        )
        response = self._request(
            "post", url, action=f"create {object_name}", json=values
        )
        try:
            record_id = response.json()["id"]
        except (ValueError, KeyError, TypeError) as error:
            raise SalesforceError(
                f"Salesforce create response for {object_name} was incomplete."
            ) from error
        return record_id

    def update_record(
        self, object_name: str, record_id: str, values: dict[str, Any]
    ) -> None:
        """Update fields on one Salesforce record."""
        url = (
            f"{self.auth.instance_url}/services/data/{API_VERSION}"
            f"/sobjects/{object_name}/{record_id}"
        )
        self._request(
            "patch", url, action=f"update {object_name} {record_id}", json=values
        )

    def get_record(
        self, object_name: str, record_id: str, fields: list[str]
    ) -> dict[str, Any]:
        """Retrieve selected fields from one Salesforce record."""
        url = (
            f"{self.auth.instance_url}/services/data/{API_VERSION}"
            f"/sobjects/{object_name}/{record_id}"
        )
        response = self._request(
            "get",
            url,
            action=f"retrieve {object_name} {record_id}",
            params={"fields": ",".join(fields)},
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise SalesforceError(
                f"Invalid Salesforce response for {object_name} {record_id}."
            ) from error
        if not isinstance(payload, dict):
            raise SalesforceError(
                f"Invalid Salesforce response for {object_name} {record_id}."
            )
        return payload

    def get_feed_messages(self, record_id: str) -> list[str]:
        """Return text from every Chatter element in a record feed."""
        url: str | None = (
            f"{self.auth.instance_url}/services/data/{API_VERSION}/connect/"
            f"communities/internal/chatter/feeds/record/{record_id}/feed-elements"
        )
        messages: list[str] = []
        while url:
            response = self._request(
                "get", url, action=f"read Chatter feed for {record_id}"
            )
            try:
                payload = response.json()
                elements = payload.get("elements", [])
                for element in elements:
                    segments = element.get("body", {}).get("messageSegments", [])
                    message = "".join(
                        segment.get("text", "")
                        for segment in segments
                        if segment.get("type") == "Text"
                    )
                    messages.append(message)
                next_page = payload.get("nextPageUrl")
            except (ValueError, AttributeError, TypeError) as error:
                raise SalesforceError(
                    f"Invalid Chatter feed response for {record_id}."
                ) from error
            url = self._absolute_url(next_page) if next_page else None
        return messages

    def post_feed_message(self, record_id: str, message: str) -> None:
        """Post a plain-text Chatter message to a record's feed."""
        url = (
            f"{self.auth.instance_url}/services/data/{API_VERSION}/connect/"
            "communities/internal/chatter/feed-elements"
        )
        payload = {
            "body": {"messageSegments": [{"type": "Text", "text": message}]},
            "feedElementType": "FeedItem",
            "subjectId": record_id,
        }
        self._request(
            "post",
            url,
            action=f"post Chatter message to {record_id}",
            json=payload,
        )

    def send_external_email_test(self, recipient: str) -> None:
        """Ask the restricted Apex endpoint to send its fixed test email.

        The request deliberately contains only a recipient. Apex owns the
        allowlist and all email content, so this client cannot supply arbitrary
        external-email text.
        """
        url = (
            f"{self.auth.instance_url}/services/apexrest/"
            "aisc-external-email-test"
        )
        self._request(
            "post",
            url,
            action="send external-email proof-of-concept test",
            json={"recipient": recipient},
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        action: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        headers = self.headers
        if json is not None:
            headers = {**headers, "Content-Type": "application/json"}
        kwargs: dict[str, Any] = {"headers": headers, "timeout": 30}
        if params is not None:
            kwargs["params"] = params
        if json is not None:
            kwargs["json"] = json
        try:
            response = getattr(self.session, method)(url, **kwargs)
        except requests.RequestException as error:
            raise SalesforceError(f"Could not {action}: {error}") from error
        if not response.ok:
            details, error_code, salesforce_message = _response_error(response)
            raise SalesforceError(
                f"Salesforce failed to {action}: {details}",
                error_code=error_code,
                salesforce_message=salesforce_message,
            )
        return response

    def _absolute_url(self, url: str) -> str:
        if not isinstance(url, str):
            raise SalesforceError("Salesforce pagination URL was invalid.")
        if url.startswith("https://"):
            return url
        return f"{self.auth.instance_url}{url}"


def _response_details(response: Any) -> str:
    return _response_error(response)[0]


def _response_error(response: Any) -> tuple[str, str | None, str | None]:
    """Return readable and structured details from a Salesforce error response."""
    try:
        details = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}", None, None
    if isinstance(details, list) and details:
        details = details[0]
    if isinstance(details, dict):
        salesforce_message = details.get("message") or details.get("error_description")
        error_code = details.get("errorCode") or details.get("error")
        return (
            str(salesforce_message or details),
            str(error_code) if error_code else None,
            str(salesforce_message) if salesforce_message else None,
        )
    return str(details), None, None
