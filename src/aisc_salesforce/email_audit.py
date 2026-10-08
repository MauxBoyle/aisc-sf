"""Read-only helpers for reviewing how Contact email addresses match names."""

from __future__ import annotations

import csv
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

CONTACT_EMAIL_FIELDS = ("Id", "FirstName", "MiddleName", "LastName", "Suffix", "Email")
REPORT_COLUMNS = (
    "contact_id",
    "first_name",
    "middle_name",
    "last_name",
    "suffix",
    "email",
    "email_local_part",
    "email_domain",
    "local_part_pattern",
    "name_match_status",
    "account_kind",
    "domain_kind",
    "review_reason",
    "duplicate_email_count",
    "duplicate_email_contact_ids",
)
ROLE_LOCAL_PARTS = frozenset(
    {
        "ap",
        "ar",
        "accounting",
        "accounts_payable",
        "billing",
        "info",
        "invoices",
        "qa",
        "qc",
    }
)


@dataclass(frozen=True)
class EmailAuditConfig:
    """The small, versioned lists used by the email assessment."""

    name_variants: dict[str, frozenset[str]]
    consumer_domains: frozenset[str]


@dataclass(frozen=True)
class EmailAssessment:
    """A review signal, not a claim that an address belongs to someone."""

    local_part_pattern: str
    name_match_status: str
    account_kind: str
    domain_kind: str
    review_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["review_reasons"] = list(self.review_reasons)
        return result


def _fold(value: Any) -> str:
    """Make a comparison key that ignores accents and common email separators."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return "".join(char for char in text.casefold() if char.isalnum())


def _clean(value: Any) -> str:
    return str(value or "").strip()


def load_name_variants(path: Path) -> dict[str, frozenset[str]]:
    """Load ``canonical_name,variant`` records into normalized lookup groups."""
    groups: dict[str, set[str]] = {}
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != ["canonical_name", "variant"]:
            raise ValueError(
                "Name variants CSV must have canonical_name,variant headers."
            )
        for row in reader:
            canonical = _fold(row.get("canonical_name"))
            variant = _fold(row.get("variant"))
            if canonical and variant:
                group = groups.setdefault(canonical, {canonical})
                group.add(variant)
    # Every nickname should work in either direction.
    lookup: dict[str, frozenset[str]] = {}
    for values in groups.values():
        frozen = frozenset(values)
        lookup.update({value: frozen for value in frozen})
    return lookup


def load_consumer_domains(path: Path) -> frozenset[str]:
    """Load one lower-case domain per line, ignoring comments and blank lines."""
    return frozenset(
        line.strip().casefold()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def load_email_audit_config(config_dir: Path | None = None) -> EmailAuditConfig:
    """Load the repository's editable audit configuration files."""
    directory = config_dir or Path(__file__).resolve().parents[2] / "config"
    return EmailAuditConfig(
        load_name_variants(directory / "email_name_variants.csv"),
        load_consumer_domains(directory / "consumer_email_domains.txt"),
    )


def assess_email(
    first_name: Any,
    middle_name: Any,
    last_name: Any,
    suffix: Any,
    email: Any,
    *,
    config: EmailAuditConfig | None = None,
) -> EmailAssessment:
    """Assess one address without changing it or trying to prove ownership."""
    del suffix  # A suffix does not normally appear in an email local part.
    config = config or load_email_audit_config()
    raw_email = _clean(email)
    if not raw_email:
        return EmailAssessment(
            "missing", "not_applicable", "unknown", "unknown", ("missing_email",)
        )
    if raw_email.count("@") != 1:
        return EmailAssessment(
            "unmatched", "not_applicable", "unknown", "unknown", ("malformed_email",)
        )
    local, domain = (piece.strip() for piece in raw_email.split("@"))
    if (
        not local
        or not _is_valid_domain(domain)
        or any(char.isspace() for char in raw_email)
        or "." not in domain
    ):
        return EmailAssessment(
            "unmatched", "not_applicable", "unknown", "unknown", ("malformed_email",)
        )
    local_key = _fold(local)
    domain_key = domain.casefold()
    domain_kind = (
        "consumer" if domain_key in config.consumer_domains else "organization"
    )
    if not local_key:
        return EmailAssessment(
            "unmatched", "not_applicable", "unknown", domain_kind, ("malformed_email",)
        )
    if local.casefold() == "postmaster" or local.casefold().startswith("verify-"):
        return _special("system", domain_kind, "system_address")
    if local.casefold() in ROLE_LOCAL_PARTS:
        return _special("role", domain_kind, "role_address")
    if local_key.isdigit():
        return _special("numeric_id", domain_kind, "numeric_id")

    expected = _expected_local_parts(first_name, middle_name, last_name, config)
    exact_patterns = [
        pattern for pattern, values in expected.items() if local.casefold() in values
    ]
    normalized_patterns = [
        pattern
        for pattern, values in expected.items()
        if local_key in {_fold(value) for value in values}
    ]
    all_values = {value for values in expected.values() for value in values}
    variant_values = _expected_local_parts(
        first_name, middle_name, last_name, config, variants=True
    )
    variant_patterns = [
        pattern
        for pattern, values in variant_values.items()
        if local_key in {_fold(value) for value in values}
    ]
    reasons: list[str] = ["consumer_domain"] if domain_kind == "consumer" else []
    if exact_patterns:
        if len(exact_patterns) > 1:
            reasons.append("ambiguous_name_match")
            return EmailAssessment(
                "unmatched", "ambiguous", "person", domain_kind, tuple(reasons)
            )
        return EmailAssessment(
            exact_patterns[0], "exact", "person", domain_kind, tuple(reasons)
        )
    if normalized_patterns:
        if len(normalized_patterns) > 1:
            reasons.append("ambiguous_name_match")
            return EmailAssessment(
                "unmatched", "ambiguous", "person", domain_kind, tuple(reasons)
            )
        return EmailAssessment(
            normalized_patterns[0],
            "normalized_exact",
            "person",
            domain_kind,
            tuple(reasons),
        )
    if variant_patterns:
        if len(variant_patterns) > 1:
            reasons.append("ambiguous_name_match")
            return EmailAssessment(
                "unmatched", "ambiguous", "person", domain_kind, tuple(reasons)
            )
        reasons.append("name_variant")
        return EmailAssessment(
            variant_patterns[0], "name_variant", "person", domain_kind, tuple(reasons)
        )
    near_patterns = [
        pattern
        for pattern, values in expected.items()
        if any(_one_edit_apart(local_key, _fold(value)) for value in values)
    ]
    if near_patterns:
        reasons.append("possible_name_typo")
        return EmailAssessment(
            near_patterns[0], "near_match", "person", domain_kind, tuple(reasons)
        )
    if not all_values:
        reasons.append("name_unavailable")
        return EmailAssessment(
            "unmatched", "not_applicable", "person", domain_kind, tuple(reasons)
        )
    reasons.append("name_no_match")
    return EmailAssessment(
        "unmatched", "no_match", "person", domain_kind, tuple(reasons)
    )


def _special(kind: str, domain_kind: str, reason: str) -> EmailAssessment:
    reasons = [reason]
    if domain_kind == "consumer":
        reasons.append("consumer_domain")
    return EmailAssessment(kind, "not_applicable", kind, domain_kind, tuple(reasons))


def _is_valid_domain(domain: str) -> bool:
    """Apply a deliberately small, safe domain check for a review report."""
    labels = domain.split(".")
    return (
        len(labels) > 1
        and all(
            label and not label.startswith("-") and not label.endswith("-")
            for label in labels
        )
        and all(
            all(character.isalnum() or character == "-" for character in label)
            for label in labels
        )
    )


def _expected_local_parts(
    first: Any,
    middle: Any,
    last: Any,
    config: EmailAuditConfig,
    *,
    variants: bool = False,
) -> dict[str, set[str]]:
    first_key, middle_key, last_key = _fold(first), _fold(middle), _fold(last)
    if variants and first_key:
        firsts = config.name_variants.get(first_key, frozenset({first_key}))
    else:
        firsts = {first_key} if first_key else set()
    values: dict[str, set[str]] = {}

    def add(pattern: str, candidates: set[str]) -> None:
        values[pattern] = {candidate for candidate in candidates if candidate}

    if firsts and last_key:
        add("first_last", {f"{item}.{last_key}" for item in firsts})
        add("last_first", {f"{last_key}.{item}" for item in firsts})
        add("f_last", {f"{item[0]}.{last_key}" for item in firsts})
        add("first_l", {f"{item}.{last_key[0]}" for item in firsts})
        add("f_l", {f"{item[0]}.{last_key[0]}" for item in firsts})
        add("last_f", {f"{last_key}.{item[0]}" for item in firsts})
    if firsts:
        add("first_only", set(firsts))
    if last_key:
        add("last_only", {last_key})
    if firsts and middle_key and last_key:
        add(
            "three_initials",
            {f"{item[0]}{middle_key[0]}{last_key[0]}" for item in firsts},
        )
    return values


def _one_edit_apart(left: str, right: str) -> bool:
    """Return true for one insertion, deletion, replacement, or swap."""
    if left == right:
        return False
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        differences = [
            index
            for index, pair in enumerate(zip(left, right, strict=True))
            if pair[0] != pair[1]
        ]
        if len(differences) == 1:
            return True
        if len(differences) == 2:
            first, second = differences
            return (
                second == first + 1
                and left[first] == right[second]
                and left[second] == right[first]
            )
        return False
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = 0
    while index < len(shorter) and shorter[index] == longer[index]:
        index += 1
    return shorter[index:] == longer[index + 1 :]


class ContactEmailAuditService:
    """Fetch every Contact and turn independent assessments into report rows."""

    def __init__(self, client: Any, *, config: EmailAuditConfig | None = None):
        self.client = client
        self.config = config or load_email_audit_config()

    def build(self) -> list[dict[str, str]]:
        contacts = self.client.query_records(
            "Contact", list(CONTACT_EMAIL_FIELDS), order_by="Id ASC"
        )
        normalized_emails = [
            _clean(record.get("Email")).casefold() for record in contacts
        ]
        duplicates = Counter(value for value in normalized_emails if value)
        ids_by_email: dict[str, list[str]] = {}
        for record, normalized in zip(contacts, normalized_emails, strict=True):
            if normalized:
                ids_by_email.setdefault(normalized, []).append(_clean(record.get("Id")))
        rows = [self._row(record, duplicates, ids_by_email) for record in contacts]
        return sorted(rows, key=lambda row: row["contact_id"])

    def _row(
        self,
        record: dict[str, Any],
        duplicates: Counter[str],
        ids_by_email: dict[str, list[str]],
    ) -> dict[str, str]:
        email = _clean(record.get("Email"))
        local, domain = _email_parts(email)
        assessment = assess_email(
            record.get("FirstName"),
            record.get("MiddleName"),
            record.get("LastName"),
            record.get("Suffix"),
            email,
            config=self.config,
        )
        normalized = email.casefold()
        reasons = list(assessment.review_reasons)
        if duplicates.get(normalized, 0) > 1:
            reasons.append("duplicate_email")
        return {
            "contact_id": _clean(record.get("Id")),
            "first_name": _clean(record.get("FirstName")),
            "middle_name": _clean(record.get("MiddleName")),
            "last_name": _clean(record.get("LastName")),
            "suffix": _clean(record.get("Suffix")),
            "email": email,
            "email_local_part": local,
            "email_domain": domain,
            "local_part_pattern": assessment.local_part_pattern,
            "name_match_status": assessment.name_match_status,
            "account_kind": assessment.account_kind,
            "domain_kind": assessment.domain_kind,
            "review_reason": "|".join(reasons),
            "duplicate_email_count": str(duplicates.get(normalized, 0)),
            "duplicate_email_contact_ids": "|".join(ids_by_email.get(normalized, []))
            if duplicates.get(normalized, 0) > 1
            else "",
        }


def _email_parts(email: str) -> tuple[str, str]:
    if email.count("@") != 1:
        return "", ""
    return tuple(part.strip() for part in email.split("@"))  # type: ignore[return-value]


def write_contact_email_audit(rows: list[dict[str, str]], output_path: Path) -> Path:
    """Write the stable CSV report using UTF-8 and its parent directory."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return output_path
