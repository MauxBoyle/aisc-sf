"""Build a read-only CSV for manually reviewing Audit Review outcomes."""

from __future__ import annotations

import csv
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

AUDIT_REVIEW_FIELDS = (
    "Name",
    "CreatedDate",
    "Cert_Account__c",
    "Cert_Account__r.Name",
    "Cert_CRG_Outcome__c",
    "CRG_Comments__c",
)

CSV_COLUMNS = (
    "audit_review",
    "audit_review_created_date",
    "account_name",
    "account_id",
    "crg_outcome",
    "crg_comments",
    "standard",
    "drop",
    "additional",
    "cautionary_letter",
    "scope",
    "needs_manual_review",
)


class ReviewOutcome(StrEnum):
    """The single classification assigned to an Audit Review's CRG text."""

    UNFLAGGED = "unflagged"
    STANDARD = "standard"
    DROP = "drop"
    ADDITIONAL = "additional"
    CAUTIONARY_LETTER = "cautionary_letter"
    SCOPE = "scope"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"


_DROP_PATTERN = re.compile(r"\b(?:drop\w*|withdraw\w*)\b", re.IGNORECASE)
_ADDITIONAL_PATTERN = re.compile(r"\b(?:additional|add'?l|addt'?l)\b", re.IGNORECASE)
_CAUTIONARY_PATTERN = re.compile(r"\bcautionary\b", re.IGNORECASE)
_CONDITIONAL_PATTERN = re.compile(r"\bconditional\b", re.IGNORECASE)
_CERTIFICATION_CODE_PATTERN = re.compile(
    r"\b(?:BU|CSE|ABR|IBR|SBR|HYDA|HYD|CPT|BEE|SEE|MEE|CCC|CCE)(?:[ -]?[123])?\b",
    re.IGNORECASE,
)
_SCOPE_LANGUAGE_PATTERN = re.compile(
    r"\b(?:add\w*|remove\w*|upgrade\w*|scope)\b", re.IGNORECASE
)
_GRANT_CODE_ONLY_PATTERN = re.compile(
    r"\bgrant\s+(?:BU|CSE|ABR|IBR|SBR|HYDA|HYD|CPT|BEE|SEE|MEE|CCC|CCE)(?:[ -]?[123])?\s+only\b",
    re.IGNORECASE,
)
_SCOPE_CHANGE_PATTERN = re.compile(
    r"\b(?:change|changing|changed)\s+(?:the\s+)?scope\b", re.IGNORECASE
)
_ADDITIONAL_AUDIT_PATTERN = re.compile(r"\badditional\s+audit\b", re.IGNORECASE)
_JOBSITE_PATTERN = re.compile(r"\bjob\s*-?\s*site\b", re.IGNORECASE)
_PART_B_PATTERN = re.compile(r"\bpart\s*-?\s*b\b", re.IGNORECASE)
_FAVORABLE_PATTERN = re.compile(
    r"\b(?:renew\s+certification|recommend\s+renewal|grant\s+certification)\b",
    re.IGNORECASE,
)
_STANDARD_OUTCOMES = frozenset(
    {"certification recommended", "certificate recommended", "certificate processed"}
)
_WITHDRAWAL_OUTCOME = "crg withdrawal"
_FOLLOW_UP_OUTCOME = "crg follow up needed"
_CLOSED_WITHOUT_CERTIFICATE_OUTCOME = "closed without certificate"


def two_year_cutoff(now: datetime) -> datetime:
    """Return the same UTC time two years earlier, including leap-day handling."""
    utc_now = now.astimezone(UTC)
    try:
        return utc_now.replace(year=utc_now.year - 2)
    except ValueError:
        # February 29 has no matching date in a non-leap year.
        return utc_now.replace(year=utc_now.year - 2, day=28)


def classify_outcome(outcome: Any, comments: Any) -> ReviewOutcome:
    """Classify combined CRG Outcome and Comments text into one review result.

    Empty text is unflagged. Text that does not satisfy a known outcome rule is
    intentionally sent to manual review so it is not silently overlooked.
    """
    outcome_text = outcome.strip() if isinstance(outcome, str) else ""
    comments_text = comments.strip() if isinstance(comments, str) else ""
    searchable_text = " ".join(
        value.strip() for value in (outcome, comments) if isinstance(value, str)
    ).strip()
    if not searchable_text:
        return ReviewOutcome.UNFLAGGED

    has_drop = bool(_DROP_PATTERN.search(searchable_text))
    has_additional = bool(_ADDITIONAL_PATTERN.search(searchable_text))
    has_cautionary = bool(_CAUTIONARY_PATTERN.search(searchable_text))
    has_conditional = bool(_CONDITIONAL_PATTERN.search(searchable_text))
    has_code = bool(_CERTIFICATION_CODE_PATTERN.search(searchable_text))
    has_scope = (
        has_code
        and bool(
            _SCOPE_LANGUAGE_PATTERN.search(searchable_text)
            or _GRANT_CODE_ONLY_PATTERN.search(searchable_text)
        )
    ) or bool(_SCOPE_CHANGE_PATTERN.search(comments_text))
    has_additional_audit_without_jobsite = bool(
        _ADDITIONAL_AUDIT_PATTERN.search(searchable_text)
        and not _JOBSITE_PATTERN.search(searchable_text)
    )
    normalized_outcome = outcome_text.casefold()

    if normalized_outcome == _WITHDRAWAL_OUTCOME:
        return ReviewOutcome.DROP
    if normalized_outcome in _STANDARD_OUTCOMES:
        if has_additional_audit_without_jobsite and has_code:
            return ReviewOutcome.ADDITIONAL
        if has_scope and has_cautionary:
            return ReviewOutcome.NEEDS_MANUAL_REVIEW
        if has_scope:
            return ReviewOutcome.SCOPE
        if has_cautionary and not has_conditional:
            return ReviewOutcome.CAUTIONARY_LETTER
        return ReviewOutcome.STANDARD
    if normalized_outcome == _CLOSED_WITHOUT_CERTIFICATE_OUTCOME:
        if has_additional_audit_without_jobsite:
            return ReviewOutcome.ADDITIONAL
        if has_cautionary and not has_conditional:
            return ReviewOutcome.CAUTIONARY_LETTER
        if (
            has_conditional
            or _PART_B_PATTERN.search(searchable_text)
            or _JOBSITE_PATTERN.search(searchable_text)
        ):
            return ReviewOutcome.STANDARD
        return ReviewOutcome.NEEDS_MANUAL_REVIEW
    if normalized_outcome == _FOLLOW_UP_OUTCOME:
        if has_additional_audit_without_jobsite:
            return ReviewOutcome.ADDITIONAL
        if has_drop:
            return ReviewOutcome.SCOPE if has_code else ReviewOutcome.DROP
        if has_scope and has_cautionary:
            return ReviewOutcome.NEEDS_MANUAL_REVIEW
        if has_scope:
            return ReviewOutcome.SCOPE
        if has_cautionary and not has_conditional:
            return ReviewOutcome.CAUTIONARY_LETTER
        return ReviewOutcome.NEEDS_MANUAL_REVIEW

    if has_drop and has_additional:
        return ReviewOutcome.NEEDS_MANUAL_REVIEW
    if has_additional_audit_without_jobsite and has_code:
        return ReviewOutcome.ADDITIONAL
    if has_scope and (has_drop or has_additional or has_cautionary):
        return ReviewOutcome.NEEDS_MANUAL_REVIEW
    if has_drop:
        return ReviewOutcome.DROP
    if has_additional:
        return ReviewOutcome.ADDITIONAL
    if has_cautionary and not has_conditional:
        return ReviewOutcome.CAUTIONARY_LETTER
    if has_scope:
        return ReviewOutcome.SCOPE
    if _FAVORABLE_PATTERN.search(searchable_text):
        return ReviewOutcome.UNFLAGGED
    return ReviewOutcome.NEEDS_MANUAL_REVIEW


def outcome_flags(
    outcome: Any, comments: Any
) -> tuple[bool, bool, bool, bool, bool, bool]:
    """Return mutually exclusive CSV flags for the classified CRG text."""
    classification = classify_outcome(outcome, comments)
    return (
        classification is ReviewOutcome.STANDARD,
        classification is ReviewOutcome.DROP,
        classification is ReviewOutcome.ADDITIONAL,
        classification is ReviewOutcome.CAUTIONARY_LETTER,
        classification is ReviewOutcome.SCOPE,
        classification is ReviewOutcome.NEEDS_MANUAL_REVIEW,
    )


def build_rows(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turn Salesforce Audit Review records into easy-to-check CSV rows."""
    rows: list[dict[str, Any]] = []
    for record in records:
        account = record.get("Cert_Account__r")
        account_name = account.get("Name", "") if isinstance(account, Mapping) else ""
        outcome = record.get("Cert_CRG_Outcome__c") or ""
        comments = record.get("CRG_Comments__c") or ""
        standard, drop, additional, cautionary_letter, scope, needs_manual_review = (
            outcome_flags(outcome, comments)
        )
        rows.append(
            {
                "audit_review": record.get("Name") or "",
                "audit_review_created_date": record.get("CreatedDate") or "",
                "account_name": account_name,
                "account_id": record.get("Cert_Account__c") or "",
                "crg_outcome": outcome,
                "crg_comments": comments,
                "standard": standard,
                "drop": drop,
                "additional": additional,
                "cautionary_letter": cautionary_letter,
                "scope": scope,
                "needs_manual_review": needs_manual_review,
            }
        )
    return rows


class AuditReviewOutcomeService:
    """Query the rolling two-year Audit Review window without changing Salesforce."""

    def __init__(self, client: Any, *, now: datetime | None = None):
        self.client = client
        self.now = now or datetime.now(UTC)

    def build(self) -> list[dict[str, Any]]:
        cutoff = two_year_cutoff(self.now).strftime("%Y-%m-%dT%H:%M:%SZ")
        records = self.client.query_records(
            "Cert_Audit_Review__c",
            list(AUDIT_REVIEW_FIELDS),
            where=f"CreatedDate >= {cutoff}",
            order_by="CreatedDate DESC",
        )
        return build_rows(records)


def write_audit_review_outcomes(
    rows: Sequence[Mapping[str, Any]],
    output_path: Path,
) -> Path:
    """Write the review rows to ``output_path`` and return that path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return output_path
