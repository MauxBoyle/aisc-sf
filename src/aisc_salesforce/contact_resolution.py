"""Conservative, reusable Contact matching for Profile Update submissions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BRACKET_SUFFIX_PATTERN = re.compile(r"\s*\[[^\[\]]*\]\s*$")
NON_NAME_PATTERN = re.compile(r"[^a-z0-9]+")

GENERIC_LOCAL_PARTS = frozenset(
    {
        "admin",
        "administration",
        "accounting",
        "ap",
        "billing",
        "certification",
        "contact",
        "customerservice",
        "finance",
        "hello",
        "info",
        "mail",
        "office",
        "quality",
        "reception",
        "sales",
        "service",
        "support",
        "team",
    }
)


class ContactResolutionClassification(StrEnum):
    """The four outcomes produced by conservative Contact matching."""

    USE_EXISTING = "use_existing"
    CREATE_NEW = "create_new"
    LIKELY_TYPO = "likely_typo"
    AMBIGUOUS = "ambiguous"


# Short public alias for callers that do not need the longer domain name.
ContactClassification = ContactResolutionClassification


@dataclass(frozen=True)
class ContactSource:
    """One place where an email appeared in a staged row."""

    kind: str
    role: str = ""
    submission_id: str = ""

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-safe representation."""
        return {
            "kind": self.kind,
            "role": self.role,
            "submission_id": self.submission_id,
        }


@dataclass
class ContactResolution:
    """A complete, auditable decision about one comparison email."""

    classification: ContactResolutionClassification
    normalized_email: str
    comparison_key: str
    sources: list[ContactSource] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_contact: dict[str, Any] | None = None
    reason: str = ""
    confidence: str = ""
    warnings: list[str] = field(default_factory=list)
    submitted: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the stable JSON contract used in staging and audit files."""
        return {
            "classification": self.classification.value,
            "normalized_email": self.normalized_email,
            "comparison_key": self.comparison_key,
            "sources": [source.as_dict() for source in self.sources],
            "candidates": [contact_snapshot(item) for item in self.candidates],
            "selected_contact": (
                contact_snapshot(self.selected_contact)
                if self.selected_contact is not None
                else None
            ),
            "reason": self.reason,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "submitted": dict(self.submitted),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ContactResolution:
        """Rebuild a resolution read from the staging CSV."""
        return cls(
            classification=ContactResolutionClassification(value["classification"]),
            normalized_email=str(value.get("normalized_email", "")),
            comparison_key=str(value.get("comparison_key", "")),
            sources=[
                ContactSource(
                    kind=str(item.get("kind", "")),
                    role=str(item.get("role", "")),
                    submission_id=str(item.get("submission_id", "")),
                )
                for item in value.get("sources", [])
                if isinstance(item, dict)
            ],
            candidates=[
                dict(item)
                for item in value.get("candidates", [])
                if isinstance(item, dict)
            ],
            selected_contact=(
                dict(value["selected_contact"])
                if isinstance(value.get("selected_contact"), dict)
                else None
            ),
            reason=str(value.get("reason", "")),
            confidence=str(value.get("confidence", "")),
            warnings=[str(item) for item in value.get("warnings", [])],
            submitted={
                str(key): str(item) for key, item in value.get("submitted", {}).items()
            },
        )


def normalize_email(value: Any) -> tuple[str, str, list[str]]:
    """Normalize an email and build its dot-insensitive comparison key.

    The comparison rule deliberately removes dots from every local-part, not
    only Gmail addresses. The normalized email keeps the dots for display and
    Salesforce writes.
    """
    normalized = str(value or "").strip().casefold()
    warnings: list[str] = []
    if not normalized:
        return "", "", warnings
    if not EMAIL_PATTERN.fullmatch(normalized):
        warnings.append(f"Invalid email address: {normalized!r}.")
        return normalized, "", warnings
    local_part, domain = normalized.rsplit("@", 1)
    return normalized, f"{local_part.replace('.', '')}@{domain}", warnings


def is_generic_mailbox(email: str) -> bool:
    """Return whether an address looks shared rather than person-specific."""
    normalized, _, _ = normalize_email(email)
    if not normalized:
        return False
    local_part = NON_NAME_PATTERN.sub("", normalized.rsplit("@", 1)[0])
    return local_part in GENERIC_LOCAL_PARTS


def comparison_name(value: Any) -> str:
    """Normalize a name after removing one trailing ``[suffix]`` marker."""
    without_suffix = BRACKET_SUFFIX_PATTERN.sub("", str(value or "").strip())
    return NON_NAME_PATTERN.sub("", without_suffix.casefold())


def name_local_part_patterns(first_name: Any, last_name: Any) -> set[str]:
    """Build deterministic email local-part patterns from names and initials."""
    first = comparison_name(first_name)
    last = comparison_name(last_name)
    if not first and not last:
        return set()
    if not first:
        return {last}
    if not last:
        return {first}
    return {
        first,
        last,
        first + last,
        last + first,
        first[0] + last,
        first + last[0],
        last + first[0],
        first[0] + last[0],
    }


def is_single_edit_or_transposition(left: str, right: str) -> bool:
    """Return whether two strings differ by exactly one simple typo."""
    if left == right:
        return False
    if len(left) == len(right):
        differences = [
            index
            for index, pair in enumerate(zip(left, right, strict=True))
            if pair[0] != pair[1]
        ]
        if len(differences) == 1:
            return True
        return (
            len(differences) == 2
            and differences[1] == differences[0] + 1
            and left[differences[0]] == right[differences[1]]
            and left[differences[1]] == right[differences[0]]
        )
    if abs(len(left) - len(right)) != 1:
        return False
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1 :] == shorter:
            return True
    return False


def family_account_ids(
    target_account: dict[str, Any] | None,
    accounts: list[dict[str, Any]],
) -> set[str]:
    """Return IDs for the target Account, its parent, and its siblings."""
    if not target_account:
        return set()
    target_id = str(target_account.get("Id") or "").strip()
    parent_id = str(target_account.get("ParentId") or "").strip()
    if not target_id:
        return set()
    result = {target_id}
    if not parent_id:
        return result
    result.add(parent_id)
    result.update(
        str(account.get("Id") or "").strip()
        for account in accounts
        if str(account.get("ParentId") or "").strip() == parent_id
    )
    result.discard("")
    return result


def resolve_contact(
    email: Any,
    contacts: list[dict[str, Any]],
    family_ids: set[str],
    *,
    sources: list[ContactSource] | None = None,
    submitted: dict[str, str] | None = None,
) -> ContactResolution:
    """Classify one submitted email using cautious family-first matching."""
    normalized, key, warnings = normalize_email(email)
    details = dict(submitted or {})
    source_values = list(sources or [])
    if not normalized or not key:
        return ContactResolution(
            ContactResolutionClassification.AMBIGUOUS,
            normalized,
            key,
            source_values,
            reason="The submitted email is blank or invalid and needs operator review.",
            confidence="none",
            warnings=warnings,
            submitted=details,
        )

    prepared = [
        (
            contact,
            normalize_email(contact.get("Email")),
            str(contact.get("AccountId") or "").strip() in family_ids,
        )
        for contact in contacts
    ]
    if is_generic_mailbox(normalized):
        warnings.append("The email appears to be a generic or shared mailbox.")
        generic_candidates = [
            contact
            for contact, (candidate_email, candidate_key, _), _ in prepared
            if candidate_email == normalized or candidate_key == key
        ]
        return ContactResolution(
            ContactResolutionClassification.AMBIGUOUS,
            normalized,
            key,
            source_values,
            candidates=_unique_contacts(generic_candidates),
            reason="Generic mailbox names require an operator choice.",
            confidence="operator_required",
            warnings=warnings,
            submitted=details,
        )

    family_exact = [
        contact
        for contact, (candidate_email, _, _), in_family in prepared
        if in_family and candidate_email == normalized
    ]
    external_exact = [
        contact
        for contact, (candidate_email, _, _), in_family in prepared
        if not in_family and candidate_email == normalized
    ]
    if len(family_exact) == 1 and not external_exact:
        return _existing_resolution(
            normalized,
            key,
            source_values,
            details,
            family_exact,
            family_exact[0],
            "A family Contact has the exact normalized email.",
            "exact",
            warnings,
        )
    if family_exact or external_exact:
        candidates = [*family_exact, *external_exact]
        return ContactResolution(
            ContactResolutionClassification.AMBIGUOUS,
            normalized,
            key,
            source_values,
            candidates=candidates,
            reason="Exact matches are tied or mixed between family and external Accounts.",
            confidence="operator_required",
            warnings=warnings,
            submitted=details,
        )

    family_key = [
        contact
        for contact, (_, candidate_key, _), in_family in prepared
        if in_family and candidate_key and candidate_key == key
    ]
    external_key = [
        contact
        for contact, (_, candidate_key, _), in_family in prepared
        if not in_family and candidate_key and candidate_key == key
    ]
    if len(family_key) == 1 and not external_key:
        return _existing_resolution(
            normalized,
            key,
            source_values,
            details,
            family_key,
            family_key[0],
            "A family Contact has the same dot-insensitive comparison key.",
            "exact_comparison_key",
            warnings,
        )
    if family_key or external_key:
        candidates = [*family_key, *external_key]
        return ContactResolution(
            ContactResolutionClassification.AMBIGUOUS,
            normalized,
            key,
            source_values,
            candidates=candidates,
            reason="Comparison-key matches are tied or include an external Account.",
            confidence="operator_required",
            warnings=warnings,
            submitted=details,
        )

    local_part, domain = key.rsplit("@", 1)
    strong: list[dict[str, Any]] = []
    typo: list[dict[str, Any]] = []
    differing_domain: list[dict[str, Any]] = []
    for contact, (candidate_email, _, _), in_family in prepared:
        if not in_family:
            continue
        patterns = name_local_part_patterns(
            contact.get("FirstName"), contact.get("LastName")
        )
        if not patterns:
            continue
        candidate_domain = (
            candidate_email.rsplit("@", 1)[1] if "@" in candidate_email else ""
        )
        if local_part in patterns:
            if candidate_domain == domain:
                strong.append(contact)
            else:
                differing_domain.append(contact)
        elif candidate_domain == domain and any(
            is_single_edit_or_transposition(local_part, pattern) for pattern in patterns
        ):
            typo.append(contact)

    name_candidates = _unique_contacts([*strong, *differing_domain])
    if name_candidates:
        return ContactResolution(
            ContactResolutionClassification.AMBIGUOUS,
            normalized,
            key,
            source_values,
            candidates=name_candidates,
            reason=(
                "Name-derived evidence or a differing email domain is not strong "
                "enough for automatic selection."
            ),
            confidence="operator_required",
            warnings=warnings,
            submitted=details,
        )
    typo = _unique_contacts(typo)
    if len(typo) == 1:
        return ContactResolution(
            ContactResolutionClassification.LIKELY_TYPO,
            normalized,
            key,
            source_values,
            candidates=typo,
            selected_contact=typo[0],
            reason="The local-part is one edit or one adjacent transposition from a name pattern.",
            confidence="likely_typo",
            warnings=warnings,
            submitted=details,
        )
    if len(typo) > 1:
        return ContactResolution(
            ContactResolutionClassification.AMBIGUOUS,
            normalized,
            key,
            source_values,
            candidates=typo,
            reason="More than one family Contact is a likely one-character typo match.",
            confidence="operator_required",
            warnings=warnings,
            submitted=details,
        )
    return ContactResolution(
        ContactResolutionClassification.CREATE_NEW,
        normalized,
        key,
        source_values,
        reason="No safe existing Contact match was found.",
        confidence="no_match",
        warnings=warnings,
        submitted=details,
    )


def merge_resolution(
    existing: ContactResolution,
    incoming: ContactResolution,
) -> ContactResolution:
    """Merge repeated appearances of the same comparison email."""
    existing.sources.extend(
        source for source in incoming.sources if source not in existing.sources
    )
    for key, value in incoming.submitted.items():
        if value and not existing.submitted.get(key):
            existing.submitted[key] = value
    existing.warnings.extend(
        warning for warning in incoming.warnings if warning not in existing.warnings
    )
    return existing


def contact_snapshot(contact: dict[str, Any] | None) -> dict[str, Any]:
    """Keep the small set of Contact fields needed for review and audit."""
    if contact is None:
        return {}
    return {
        name: contact.get(name)
        for name in (
            "Id",
            "AccountId",
            "FirstName",
            "LastName",
            "Title",
            "Email",
            "Phone",
        )
    }


def _existing_resolution(
    normalized: str,
    key: str,
    sources: list[ContactSource],
    submitted: dict[str, str],
    candidates: list[dict[str, Any]],
    selected: dict[str, Any],
    reason: str,
    confidence: str,
    warnings: list[str],
) -> ContactResolution:
    return ContactResolution(
        ContactResolutionClassification.USE_EXISTING,
        normalized,
        key,
        sources,
        candidates=candidates,
        selected_contact=selected,
        reason=reason,
        confidence=confidence,
        warnings=warnings,
        submitted=submitted,
    )


def _unique_contacts(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for contact in contacts:
        identity = str(contact.get("Id") or "").strip() or repr(contact)
        by_id.setdefault(identity, contact)
    return list(by_id.values())
