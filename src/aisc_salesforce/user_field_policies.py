"""Pure, deterministic field policies for participant Salesforce Users."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from .contact_resolution import normalize_email

DEFAULT_TIME_ZONE = "America/Chicago"
FIXED_LOCALE = "en_US"
FIXED_LANGUAGE = "en_US"
FIXED_EMAIL_ENCODING = "UTF-8"

_US_COUNTRIES = {"united states", "united states of america", "usa", "us"}
_CANADIAN_COUNTRIES = {"canada", "ca"}
_US_TIME_ZONES = {
    "AL": "America/Chicago",
    "AK": "America/Anchorage",
    "AZ": "America/Phoenix",
    "AR": "America/Chicago",
    "CA": "America/Los_Angeles",
    "CO": "America/Denver",
    "CT": "America/New_York",
    "DE": "America/New_York",
    "DC": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "HI": "Pacific/Honolulu",
    "ID": "America/Boise",
    "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis",
    "IA": "America/Chicago",
    "KS": "America/Chicago",
    "KY": "America/New_York",
    "LA": "America/Chicago",
    "ME": "America/New_York",
    "MD": "America/New_York",
    "MA": "America/New_York",
    "MI": "America/Detroit",
    "MN": "America/Chicago",
    "MS": "America/Chicago",
    "MO": "America/Chicago",
    "MT": "America/Denver",
    "NE": "America/Chicago",
    "NV": "America/Los_Angeles",
    "NH": "America/New_York",
    "NJ": "America/New_York",
    "NM": "America/Denver",
    "NY": "America/New_York",
    "NC": "America/New_York",
    "ND": "America/Chicago",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "OR": "America/Los_Angeles",
    "PA": "America/New_York",
    "RI": "America/New_York",
    "SC": "America/New_York",
    "SD": "America/Chicago",
    "TN": "America/Chicago",
    "TX": "America/Chicago",
    "UT": "America/Denver",
    "VT": "America/New_York",
    "VA": "America/New_York",
    "WA": "America/Los_Angeles",
    "WV": "America/New_York",
    "WI": "America/Chicago",
    "WY": "America/Denver",
}
_CANADIAN_TIME_ZONES = {
    "AB": "America/Edmonton",
    "BC": "America/Vancouver",
    "MB": "America/Winnipeg",
    "NB": "America/Moncton",
    "NL": "America/St_Johns",
    "NS": "America/Halifax",
    "NT": "America/Yellowknife",
    "NU": "America/Iqaluit",
    "ON": "America/Toronto",
    "PE": "America/Halifax",
    "QC": "America/Montreal",
    "SK": "America/Regina",
    "YT": "America/Whitehorse",
}
_US_STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_CANADIAN_PROVINCE_NAMES = {
    "alberta": "AB",
    "british columbia": "BC",
    "manitoba": "MB",
    "new brunswick": "NB",
    "newfoundland and labrador": "NL",
    "nova scotia": "NS",
    "northwest territories": "NT",
    "nunavut": "NU",
    "ontario": "ON",
    "prince edward island": "PE",
    "quebec": "QC",
    "saskatchewan": "SK",
    "yukon": "YT",
}


def contact_first_name(contact: Mapping[str, Any]) -> str:
    """Return the trimmed Contact first name."""
    return _text(contact.get("FirstName"))


def contact_last_name(contact: Mapping[str, Any]) -> str:
    """Return the trimmed Contact last name."""
    return _text(contact.get("LastName"))


def contact_email(contact: Mapping[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    """Return the normalized email, comparison value, and validation warnings."""
    email, comparison, warnings = normalize_email(contact.get("Email"))
    return email, comparison, tuple(warnings)


def normalized_name_component(value: Any) -> str:
    """Casefold and retain only Unicode letters and digits for identifiers."""
    return "".join(
        character for character in _text(value).casefold() if character.isalnum()
    )


def username_from_email(email: str) -> str:
    """Use the normalized Contact email as the Salesforce username."""
    return email


def alias(first_name: Any, last_name: Any, clock: Callable[[], date]) -> str:
    """Build the eight-character Salesforce Alias policy value."""
    return f"{normalized_name_component(last_name)[:5]}{normalized_name_component(first_name)[:1]}{clock():%y}"[
        :8
    ]


def community_nickname(
    first_name: Any,
    last_name: Any,
    clock: Callable[[], date],
    *,
    required: bool,
) -> str | None:
    """Build the optional Community Nickname policy value."""
    if not required:
        return None
    return f"{normalized_name_component(last_name)[:10]}{normalized_name_component(first_name)[:1]}{clock():%y}"


def time_zone(contact: Mapping[str, Any]) -> str:
    """Map a North American mailing address, or use the Chicago fallback."""
    country = _text(contact.get("MailingCountry")).casefold()
    state = _text(contact.get("MailingState")).casefold()
    if country in _US_COUNTRIES:
        return _US_TIME_ZONES.get(
            _US_STATE_NAMES.get(state, state.upper()), DEFAULT_TIME_ZONE
        )
    if country in _CANADIAN_COUNTRIES:
        return _CANADIAN_TIME_ZONES.get(
            _CANADIAN_PROVINCE_NAMES.get(state, state.upper()), DEFAULT_TIME_ZONE
        )
    return DEFAULT_TIME_ZONE


def fixed_localization_fields() -> dict[str, str]:
    """Return the fixed Salesforce localization settings for participant Users."""
    return {
        "LocaleSidKey": FIXED_LOCALE,
        "LanguageLocaleKey": FIXED_LANGUAGE,
        "EmailEncodingKey": FIXED_EMAIL_ENCODING,
    }


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
