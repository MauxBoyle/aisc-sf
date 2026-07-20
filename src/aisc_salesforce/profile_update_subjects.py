"""Parse and build Salesforce Case subjects for Profile Updates."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

AISC_PREFIX = "AISC Profile Update for "
LEGACY_RECEIVED_MARKER = "Profile Update Received"
SALESFORCE_SUBJECT_LIMIT = 255

_AISC_PAIR = re.compile(r"^(?P<name>.+?)\s+(?P<date>\d{2}-\d{2}-\d{2})$")
_LEGACY_WITH_DATE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?P<separator>:| -)\s*"
    r"Profile Update Received for (?P<body>.+)$",
    re.IGNORECASE,
)
_LEGACY_SHORT_DATE = re.compile(
    r"^(?P<date>\d{2}-\d{2}-\d{2}) -\s*"
    r"Profile Update Received for (?P<body>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProfileUpdateReference:
    """One exact Profile Update identifier and its received date."""

    name: str
    received_date: date


@dataclass(frozen=True)
class AiscProfileUpdateSubject:
    """The structured information stored in a modern AISC Case subject."""

    account_name: str
    updates: tuple[ProfileUpdateReference, ...]


def build_aisc_profile_update_subject(
    account_name: str, updates: Iterable[ProfileUpdateReference]
) -> str:
    """Build and validate a modern AISC Profile Update Case subject."""
    clean_account = account_name.strip()
    references = tuple(updates)
    if not clean_account:
        raise ValueError("Account Name is missing.")
    if not references:
        raise ValueError("At least one Profile Update is required.")

    pairs = []
    for reference in references:
        clean_name = reference.name.strip()
        if not clean_name:
            raise ValueError("Profile Update Name is missing.")
        pairs.append(f"{clean_name} {reference.received_date.strftime('%y-%m-%d')}")
    subject = f"{AISC_PREFIX}{clean_account} - {' / '.join(pairs)}"
    validate_subject_length(subject)
    return subject


def parse_aisc_profile_update_subject(
    subject: Any,
) -> AiscProfileUpdateSubject | None:
    """Parse a modern subject, returning ``None`` when its grammar is invalid."""
    text = _clean_text(subject)
    if not text.casefold().startswith(AISC_PREFIX.casefold()):
        return None
    body = text[len(AISC_PREFIX) :]
    if " - " not in body:
        return None
    account_name, pair_text = body.rsplit(" - ", 1)
    account_name = account_name.strip()
    if not account_name:
        return None

    updates = []
    for raw_pair in pair_text.split("/"):
        match = _AISC_PAIR.fullmatch(raw_pair.strip())
        if match is None:
            return None
        received_date = _parse_date(match.group("date"), "%y-%m-%d")
        name = match.group("name").strip()
        if received_date is None or not name:
            return None
        updates.append(ProfileUpdateReference(name, received_date))
    if not updates:
        return None
    return AiscProfileUpdateSubject(account_name, tuple(updates))


def parse_legacy_profile_update_subject(
    subject: Any,
) -> AiscProfileUpdateSubject | None:
    """Parse a supported dated legacy subject for the correction command."""
    text = _clean_text(subject)
    match = _LEGACY_WITH_DATE.fullmatch(text)
    date_format = "%Y-%m-%d"
    if match is None:
        match = _LEGACY_SHORT_DATE.fullmatch(text)
        date_format = "%y-%m-%d"
    if match is None:
        return None

    received_date = _parse_date(match.group("date"), date_format)
    parts = _legacy_body_parts(match.group("body"))
    if received_date is None or parts is None:
        return None
    account_name, names = parts
    return AiscProfileUpdateSubject(
        account_name,
        tuple(ProfileUpdateReference(name, received_date) for name in names),
    )


def is_received_profile_update_subject(subject: Any) -> bool:
    """Return whether a Case is a modern or legacy received Profile Update."""
    return parse_aisc_profile_update_subject(subject) is not None or bool(
        _legacy_names(subject)
    )


def subject_has_profile_update(subject: Any, update_name: str) -> bool:
    """Match one complete update identifier, ignoring case and outer whitespace."""
    wanted = update_name.strip().casefold()
    if not wanted:
        return False
    parsed = parse_aisc_profile_update_subject(subject)
    names = (
        [reference.name for reference in parsed.updates]
        if parsed is not None
        else _legacy_names(subject)
    )
    return any(name.strip().casefold() == wanted for name in names)


def append_profile_update(subject: str, update_name: str, received_date: date) -> str:
    """Append an update/date pair while leaving legacy subject text legacy."""
    clean_name = update_name.strip()
    if not clean_name:
        raise ValueError("Profile Update Name is missing.")

    parsed = parse_aisc_profile_update_subject(subject)
    if parsed is not None:
        return build_aisc_profile_update_subject(
            parsed.account_name,
            (*parsed.updates, ProfileUpdateReference(clean_name, received_date)),
        )
    if _legacy_names(subject):
        updated = f"{subject.strip()} / {clean_name}"
        validate_subject_length(updated)
        return updated
    raise ValueError("Case Subject is not a received Profile Update subject.")


def validate_subject_length(subject: str) -> None:
    """Reject a Case subject that exceeds Salesforce's 255-character limit."""
    if len(subject) > SALESFORCE_SUBJECT_LIMIT:
        raise ValueError(
            f"Case Subject is {len(subject)} characters; "
            f"Salesforce allows {SALESFORCE_SUBJECT_LIMIT}."
        )


def _legacy_names(subject: Any) -> list[str]:
    text = _clean_text(subject)
    if LEGACY_RECEIVED_MARKER.casefold() not in text.casefold():
        return []
    if " - " not in text:
        return []
    names_text = text.rsplit(" - ", 1)[1]
    return [name.strip() for name in names_text.split("/") if name.strip()]


def _legacy_body_parts(body: str) -> tuple[str, list[str]] | None:
    if " - " not in body:
        return None
    account_name, names_text = body.rsplit(" - ", 1)
    names = [name.strip() for name in names_text.split("/") if name.strip()]
    if not account_name.strip() or not names:
        return None
    return account_name.strip(), names


def _parse_date(value: str, date_format: str) -> date | None:
    try:
        return datetime.strptime(value, date_format).date()
    except ValueError:
        return None


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
