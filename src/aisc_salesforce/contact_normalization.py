"""Canonical formatting for submitted Profile Update contact values."""

from __future__ import annotations

import re
from typing import Any

# Add a new spelling here when it must keep non-Title-Case capitalization.
# Matching is case-insensitive and applies only to a complete word/token, so
# changing this tuple never requires changing the normalization algorithm.
CONTACT_CASE_EXCEPTIONS = (
    "CEO",
    "CFO",
    "COO",
    "CTO",
    "VP",
    "HR",
    "QA",
    "QC",
    "QMS",
    "AISC",
    "IT",
    "ISO",
    "AWS",
    "API",
    "AI",
    "PhD",
    "MBA",
    "iOS",
    "macOS",
    "McDonald",
    "MacKenzie",
    "O'Connor",
)

_CASE_EXCEPTION_LOOKUP = {
    exception.casefold(): exception for exception in CONTACT_CASE_EXCEPTIONS
}
_WORD_TOKEN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_PHONE_EXTENSION = re.compile(
    r"\s*(?:extension|ext\.?|x|#)\s*(\d+)\s*$",
    re.IGNORECASE,
)
_NORTH_AMERICAN_PHONE = re.compile(
    r"^(?:(?:\+1|1)[ .\-/]*)?"
    r"(?:\(\s*(\d{3})\s*\)|(\d{3}))"
    r"[ .\-/]*(\d{3})[ .\-/]*(\d{4})$"
)


def normalize_proper_case(value: Any) -> str:
    """Trim and Proper Case a submitted name or title."""
    text = _text(value)

    def canonical_word(match: re.Match[str]) -> str:
        word = match.group()
        return _CASE_EXCEPTION_LOOKUP.get(word.casefold(), word.title())

    return _WORD_TOKEN.sub(canonical_word, text)


def normalize_email(value: Any) -> str:
    """Trim and lowercase a submitted email, even when it is invalid."""
    return _text(value).lower()


def normalize_phone(value: Any) -> str:
    """Format a recognizable North American phone and preserve other input."""
    text = _text(value)
    if not text:
        return ""

    extension = ""
    phone_text = text
    extension_match = _PHONE_EXTENSION.search(phone_text)
    if extension_match is not None:
        extension = extension_match.group(1)
        phone_text = phone_text[: extension_match.start()].rstrip()

    phone_match = _NORTH_AMERICAN_PHONE.fullmatch(phone_text)
    if phone_match is None:
        return text

    area_code = phone_match.group(1) or phone_match.group(2)
    formatted = (
        f"{area_code}.{phone_match.group(3)}.{phone_match.group(4)}"
    )
    return f"{formatted} x{extension}" if extension else formatted


def normalize_contact_value(field_name: str, value: Any) -> str:
    """Normalize one submitted Contact field by its internal field name."""
    if field_name in {"first_name", "last_name", "title", "name"}:
        return normalize_proper_case(value)
    if field_name == "email":
        return normalize_email(value)
    if field_name == "phone":
        return normalize_phone(value)
    return _text(value)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()
