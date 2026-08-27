"""Pure rules for selecting a participant Profile from Account-role assignments.

This module deliberately does not read from or write to Salesforce.  Callers
provide already-known assignments and can later use the returned profile when
orchestrating Salesforce work. A qualifying assignment on a multi-account
Family Account has priority and selects the RAS profile.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from .account_roles import QUALIFYING_CERTIFICATION_STATUSES, AccountRole
from .user_sync_config import ParticipantProfile

NOT_ELIGIBLE_SKIP_REASON = "Not eligible: no qualifying Account-role assignments."
"""Reason returned when no assignment has a qualifying certification status."""


@dataclass(frozen=True)
class AccountRoleAssignment:
    """One Account role, its Salesforce Account ID, and certification status."""

    role: AccountRole
    account_id: str
    certification_status: str


@dataclass(frozen=True)
class RequiredProfileDecision:
    """The selected profile, eligibility explanation, and ordered rule causes."""

    profile: ParticipantProfile | None
    skip_reason: str | None
    causal_assignments: tuple[AccountRoleAssignment, ...]


_SINGLE_ROLE_PROFILES = {
    AccountRole.CERTIFICATION: ParticipantProfile.PARTICIPANT,
    AccountRole.PRINCIPAL: ParticipantProfile.PRINCIPAL,
    AccountRole.ACCOUNTING: ParticipantProfile.AP,
    AccountRole.QUALITY_QC: ParticipantProfile.QC,
}


def determine_required_profile(
    assignments: Iterable[AccountRoleAssignment],
    multi_account_family_account_ids: Collection[str] | None = None,
) -> RequiredProfileDecision:
    """Select the required participant Profile from qualifying assignments.

    Duplicate role/Account pairs retain only their first occurrence. A
    qualifying assignment on an Account in ``multi_account_family_account_ids``
    requires the RAS profile before all other profile rules. Otherwise, a New
    York assignment or more than one distinct role requires RAS.

    ``multi_account_family_account_ids`` is optional so callers that do not
    have family-hierarchy information retain the existing behavior. Its IDs
    must represent Accounts that have a parent, child, or sibling.
    """
    causes = _qualifying_unique_assignments(assignments)
    if not causes:
        return RequiredProfileDecision(
            profile=None,
            skip_reason=NOT_ELIGIBLE_SKIP_REASON,
            causal_assignments=(),
        )

    if multi_account_family_account_ids is not None and any(
        assignment.account_id in multi_account_family_account_ids
        for assignment in causes
    ):
        profile = ParticipantProfile.RAS
    else:
        profile = _profile_from_roles(causes)
    return RequiredProfileDecision(
        profile=profile,
        skip_reason=None,
        causal_assignments=causes,
    )


def _profile_from_roles(
    causes: tuple[AccountRoleAssignment, ...],
) -> ParticipantProfile:
    """Return the usual Profile from qualifying assignments without family data."""
    roles = {assignment.role for assignment in causes}
    if AccountRole.NEW_YORK in roles or len(roles) > 1:
        profile = ParticipantProfile.RAS
    else:
        profile = _SINGLE_ROLE_PROFILES[next(iter(roles))]
    return profile


def _qualifying_unique_assignments(
    assignments: Iterable[AccountRoleAssignment],
) -> tuple[AccountRoleAssignment, ...]:
    """Return qualifying role/Account causes in first-seen order."""
    causes: list[AccountRoleAssignment] = []
    seen: set[tuple[AccountRole, str]] = set()
    for assignment in assignments:
        if assignment.certification_status not in QUALIFYING_CERTIFICATION_STATUSES:
            continue
        key = (assignment.role, assignment.account_id)
        if key not in seen:
            seen.add(key)
            causes.append(assignment)
    return tuple(causes)
