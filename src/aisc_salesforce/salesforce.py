"""Read-only Salesforce OAuth2 and SOQL helpers."""

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
    """A client that performs only SOQL GET requests."""

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
        field_names = ", ".join(field.api_name for field in fields)
        url = f"{self.auth.instance_url}/services/data/{API_VERSION}/query"
        params: dict[str, str] | None = {
            "q": f"SELECT {field_names} FROM {object_name}"
        }
        records: list[dict[str, Any]] = []
        while url:
            try:
                response = self.session.get(
                    url, headers=self.headers, params=params, timeout=30
                )
            except requests.RequestException as error:
                raise SalesforceError(
                    f"Could not query {object_name}: {error}"
                ) from error
            if not response.ok:
                raise SalesforceError(
                    f"Salesforce query failed for {object_name}: {_response_details(response)}"
                )
            try:
                payload = response.json()
                records.extend(payload.get("records", []))
                done = payload["done"]
            except (ValueError, KeyError, TypeError) as error:
                raise SalesforceError(
                    f"Invalid Salesforce query response for {object_name}."
                ) from error
            next_url = payload.get("nextRecordsUrl")
            url = (
                f"{self.auth.instance_url}{next_url}" if not done and next_url else None
            )
            if not done and not next_url:
                raise SalesforceError(
                    f"Salesforce query for {object_name} ended without a next page."
                )
            params = None
        return records


def _response_details(response: Any) -> str:
    try:
        details = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(details, list) and details:
        details = details[0]
    if isinstance(details, dict):
        return str(
            details.get("message") or details.get("error_description") or details
        )
    return str(details)
