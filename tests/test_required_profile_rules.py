"""Tests for the pure required-profile rule engine."""

from dataclasses import FrozenInstanceError

import pytest

from aisc_salesforce.account_roles import AccountRole
from aisc_salesforce.required_profile_rules import (
    NOT_ELIGIBLE_SKIP_REASON,
    AccountRoleAssignment,
    RequiredProfileDecision,
    determine_required_profile,
)
from aisc_salesforce.user_sync_config import ParticipantProfile


def assignment(
    role: AccountRole,
    account_id: str = "001-first",
    certification_status: str = "Certified",
) -> AccountRoleAssignment:
    """Create a concise assignment for a rule test."""
    return AccountRoleAssignment(role, account_id, certification_status)


@pytest.mark.parametrize(
    ("role", "expected_profile"),
    [
        (AccountRole.CERTIFICATION, ParticipantProfile.PARTICIPANT),
        (AccountRole.PRINCIPAL, ParticipantProfile.PRINCIPAL),
        (AccountRole.ACCOUNTING, ParticipantProfile.AP),
        (AccountRole.QUALITY_QC, ParticipantProfile.QC),
    ],
)
def test_single_qualifying_role_selects_its_profile(role, expected_profile):
    source = assignment(role)

    decision = determine_required_profile([source])

    assert decision == RequiredProfileDecision(expected_profile, None, (source,))


def test_new_york_role_requires_ras_profile():
    source = assignment(AccountRole.NEW_YORK)

    decision = determine_required_profile([source])

    assert decision == RequiredProfileDecision(ParticipantProfile.RAS, None, (source,))


@pytest.mark.parametrize("role", list(AccountRole))
def test_multi_account_family_assignment_overrides_role_profile_with_ras(role):
    source = assignment(role, "001-family-member")

    decision = determine_required_profile(
        [source], multi_account_family_account_ids={"001-family-member"}
    )

    assert decision == RequiredProfileDecision(ParticipantProfile.RAS, None, (source,))


def test_non_qualifying_multi_account_family_assignment_is_not_eligible():
    source = assignment(
        AccountRole.PRINCIPAL,
        "001-family-member",
        certification_status="Expired",
    )

    decision = determine_required_profile(
        [source], multi_account_family_account_ids={"001-family-member"}
    )

    assert decision == RequiredProfileDecision(None, NOT_ELIGIBLE_SKIP_REASON, ())


@pytest.mark.parametrize(
    ("sources", "expected_profile"),
    [
        ([assignment(AccountRole.PRINCIPAL)], ParticipantProfile.PRINCIPAL),
        ([assignment(AccountRole.NEW_YORK)], ParticipantProfile.RAS),
        (
            [
                assignment(AccountRole.ACCOUNTING, "001-first"),
                assignment(AccountRole.QUALITY_QC, "001-second"),
            ],
            ParticipantProfile.RAS,
        ),
    ],
)
def test_assignments_outside_multi_account_family_keep_existing_rules(
    sources, expected_profile
):
    decision = determine_required_profile(
        sources, multi_account_family_account_ids={"001-unrelated-family-member"}
    )

    assert decision == RequiredProfileDecision(expected_profile, None, tuple(sources))


@pytest.mark.parametrize(
    "sources",
    [
        [assignment(AccountRole.CERTIFICATION), assignment(AccountRole.PRINCIPAL)],
        [
            assignment(AccountRole.ACCOUNTING, "001-first"),
            assignment(AccountRole.QUALITY_QC, "001-second"),
        ],
        [assignment(AccountRole.CERTIFICATION), assignment(AccountRole.NEW_YORK)],
    ],
)
def test_multiple_distinct_qualifying_roles_require_ras_profile(sources):
    decision = determine_required_profile(sources)

    assert decision == RequiredProfileDecision(
        ParticipantProfile.RAS, None, tuple(sources)
    )


def test_repeated_role_on_different_accounts_stays_as_separate_causes():
    sources = [
        assignment(AccountRole.PRINCIPAL, "001-first"),
        assignment(AccountRole.PRINCIPAL, "001-second"),
    ]

    decision = determine_required_profile(sources)

    assert decision == RequiredProfileDecision(
        ParticipantProfile.PRINCIPAL, None, tuple(sources)
    )


def test_ignored_statuses_do_not_cause_eligibility_or_a_profile():
    sources = [
        assignment(AccountRole.CERTIFICATION, certification_status="Expired"),
        assignment(AccountRole.PRINCIPAL, certification_status="Pending"),
    ]

    decision = determine_required_profile(sources)

    assert decision == RequiredProfileDecision(None, NOT_ELIGIBLE_SKIP_REASON, ())


def test_initials_is_a_qualifying_certification_status():
    source = assignment(AccountRole.ACCOUNTING, certification_status="Initials")

    assert determine_required_profile([source]).profile is ParticipantProfile.AP


def test_no_assignments_is_not_eligible():
    assert determine_required_profile([]) == RequiredProfileDecision(
        None, NOT_ELIGIBLE_SKIP_REASON, ()
    )


def test_duplicate_role_account_causes_are_deduplicated_in_first_seen_order():
    first = assignment(AccountRole.CERTIFICATION, "001-first", "Certified")
    duplicate = assignment(AccountRole.CERTIFICATION, "001-first", "Initials")
    other = assignment(AccountRole.CERTIFICATION, "001-second")

    decision = determine_required_profile([first, duplicate, other])

    assert decision == RequiredProfileDecision(
        ParticipantProfile.PARTICIPANT, None, (first, other)
    )


def test_rule_input_and_decision_are_frozen():
    source = assignment(AccountRole.CERTIFICATION)
    decision = determine_required_profile([source])

    with pytest.raises(FrozenInstanceError):
        source.account_id = "001-other"
    with pytest.raises(FrozenInstanceError):
        decision.profile = ParticipantProfile.RAS
