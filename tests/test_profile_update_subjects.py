from datetime import date

import pytest

from aisc_salesforce.profile_update_subjects import (
    AiscProfileUpdateSubject,
    ProfileUpdateReference,
    append_profile_update,
    build_aisc_profile_update_subject,
    is_received_profile_update_subject,
    parse_aisc_profile_update_subject,
    parse_legacy_profile_update_subject,
    subject_has_profile_update,
)


def test_builds_and_parses_aisc_subject_with_ordered_update_date_pairs():
    subject = build_aisc_profile_update_subject(
        "Acme Steel",
        [
            ProfileUpdateReference("PU-099", date(2026, 7, 1)),
            ProfileUpdateReference("PU-100", date(2026, 7, 15)),
        ],
    )

    assert (
        subject
        == "AISC Profile Update for Acme Steel - PU-099 26-07-01 / PU-100 26-07-15"
    )
    assert parse_aisc_profile_update_subject(subject) == AiscProfileUpdateSubject(
        "Acme Steel",
        (
            ProfileUpdateReference("PU-099", date(2026, 7, 1)),
            ProfileUpdateReference("PU-100", date(2026, 7, 15)),
        ),
    )


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("pu-100", True),
        (" PU-100 ", True),
        ("PU-10", False),
        ("PU-1000", False),
    ],
)
def test_matching_is_trimmed_case_insensitive_and_never_partial(candidate, expected):
    subject = "AISC Profile Update for Acme Steel - PU-100 26-07-15"

    assert subject_has_profile_update(subject, candidate) is expected


@pytest.mark.parametrize(
    "subject",
    [
        "2026-07-15: Profile Update Received for Acme Steel - PU-100",
        "2026-07-15 - Profile Update Received for Acme Steel - PU-100",
        "26-07-15 - Profile Update Received for Acme Steel - PU-100",
        "Profile Update Received for Acme Steel - PU-100",
        "AISC Profile Update for Acme Steel - PU-100 26-07-15",
    ],
)
def test_received_recognition_and_exact_matching_support_aisc_and_legacy(subject):
    assert is_received_profile_update_subject(subject)
    assert subject_has_profile_update(subject, "pu-100")
    assert not subject_has_profile_update(subject, "PU-10")


@pytest.mark.parametrize(
    ("subject", "received_date"),
    [
        (
            "2026-07-15: Profile Update Received for Acme Steel - PU-100 / PU-101",
            date(2026, 7, 15),
        ),
        (
            "2026-07-15 - Profile Update Received for Acme Steel - PU-100",
            date(2026, 7, 15),
        ),
        (
            "26-07-15 - Profile Update Received for Acme Steel - PU-100",
            date(2026, 7, 15),
        ),
    ],
)
def test_legacy_parser_uses_embedded_date_for_every_update(subject, received_date):
    parsed = parse_legacy_profile_update_subject(subject)

    assert parsed == AiscProfileUpdateSubject(
        "Acme Steel",
        tuple(
            ProfileUpdateReference(name, received_date)
            for name in ("PU-100", "PU-101")[: len(parsed.updates)]
        ),
    )


@pytest.mark.parametrize(
    "subject",
    [
        "Profile Update Received for Acme Steel - PU-100",
        "not-a-date: Profile Update Received for Acme Steel - PU-100",
        "2026-02-30: Profile Update Received for Acme Steel - PU-100",
    ],
)
def test_migration_parser_rejects_legacy_subject_without_trustworthy_date(subject):
    assert parse_legacy_profile_update_subject(subject) is None


def test_appending_adds_date_to_aisc_but_preserves_legacy_format():
    aisc = "AISC Profile Update for Acme Steel - PU-099 26-07-01"
    legacy = "2026-07-01: Profile Update Received for Acme Steel - PU-099"

    assert append_profile_update(aisc, "PU-100", date(2026, 7, 15)) == (
        "AISC Profile Update for Acme Steel - PU-099 26-07-01 / PU-100 26-07-15"
    )
    assert append_profile_update(legacy, "PU-100", date(2026, 7, 15)) == (
        "2026-07-01: Profile Update Received for Acme Steel - PU-099 / PU-100"
    )


def test_subject_builder_rejects_salesforce_subjects_over_255_characters():
    with pytest.raises(ValueError, match="255"):
        build_aisc_profile_update_subject(
            "A" * 240,
            [ProfileUpdateReference("PU-100", date(2026, 7, 15))],
        )
