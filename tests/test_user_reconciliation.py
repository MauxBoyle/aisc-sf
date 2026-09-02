"""Tests for the read-only participant User reconciliation planner."""

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from aisc_salesforce.user_reconciliation import (
    ReconciliationPlanError,
    UserReconciliationService,
    build_user_reconciliation_plan,
    load_user_reconciliation_plan,
    render_user_reconciliation_plan,
)
from aisc_salesforce.user_sync_config import PROFILE_CONFIGURATION, ParticipantProfile

CONTACT = {
    "Id": "contact-1",
    "FirstName": "Ada",
    "LastName": "Lovelace",
    "Email": " ADA@example.com ",
    "MailingStreet": "1 Main St",
    "MailingCity": "Chicago",
    "MailingState": "IL",
    "MailingPostalCode": "60601",
    "MailingCountry": "United States",
}
CONFIG = {profile: profile.value for profile in ParticipantProfile}
PROFILES = [
    {"Id": profile.value, "Name": name}
    for profile, (_, name) in PROFILE_CONFIGURATION.items()
]


def account(**overrides):
    return {
        "Id": "account-1",
        "ParentId": "",
        "Cert_Certification_Status__c": "Certified",
        "Cert_Certification_Contact__c": "contact-1",
        **overrides,
    }


def test_no_active_user_produces_create_with_normalized_username():
    plan = build_user_reconciliation_plan(
        CONTACT, [], [account()], [account()], PROFILES, CONFIG, []
    )

    assert plan.proposed_operation == "create"
    desired = dict(plan.proposed_create or ())
    assert desired["Username"] == "ada@example.com"
    assert desired["Alias"] == "lovela26"
    assert desired["TimeZoneSidKey"] == "America/Chicago"
    assert "CommunityNickname" not in desired
    assert plan.required_profile == ParticipantProfile.PARTICIPANT
    assert not plan.blockers
    assert plan.as_dict()["proposed_create"]["ContactId"] == "contact-1"


def test_one_active_user_does_not_copy_contact_mailing_fields():
    user = {
        "Id": "user-1",
        "IsActive": True,
        **dict(
            build_user_reconciliation_plan(
                CONTACT, [], [account()], [account()], PROFILES, CONFIG, []
            ).proposed_create
            or ()
        ),
    }
    user["City"] = "Evanston"

    plan = build_user_reconciliation_plan(
        CONTACT, [user], [account()], [account()], PROFILES, CONFIG, []
    )

    assert plan.proposed_operation == "none"
    assert not plan.field_changes


def test_alias_and_community_nickname_collisions_block_without_suffixes():
    plan = build_user_reconciliation_plan(
        CONTACT,
        [],
        [account()],
        [account()],
        PROFILES,
        CONFIG,
        [],
        alias_matches=[{"Id": "alias-user", "Alias": "lovelaa26"}],
        community_nickname_matches=[
            {"Id": "nickname-user", "CommunityNickname": "lovelacea26"}
        ],
        community_nickname_required=True,
        clock=lambda: date(2026, 1, 1),
    )

    assert dict(plan.desired_user)["CommunityNickname"] == "lovelacea26"
    assert [item["Id"] for item in plan.alias_collisions] == ["alias-user"]
    assert [item["Id"] for item in plan.community_nickname_collisions] == [
        "nickname-user"
    ]
    assert {item.code for item in plan.blockers} >= {
        "alias_collision",
        "community_nickname_collision",
    }


def test_multiple_active_users_and_inactive_users_are_kept_separate():
    users = [
        {"Id": "one", "IsActive": True},
        {"Id": "two", "IsActive": True},
        {"Id": "old", "IsActive": False},
    ]
    plan = build_user_reconciliation_plan(
        CONTACT, users, [account()], [account()], PROFILES, CONFIG, []
    )

    assert [item["Id"] for item in plan.inactive_users] == ["old"]
    assert plan.proposed_operation is None
    assert [item.code for item in plan.blockers] == ["multiple_active_users"]


def test_collision_excludes_linked_users_and_blocks_other_active_or_inactive_users():
    linked = [{"Id": "linked", "IsActive": True}]
    matches = [
        *linked,
        {"Id": "collision", "IsActive": False, "Username": "ada@example.com"},
    ]
    plan = build_user_reconciliation_plan(
        CONTACT, linked, [account()], [account()], PROFILES, CONFIG, matches
    )

    assert [item["Id"] for item in plan.username_collisions] == ["collision"]
    assert any(item.code == "username_collision" for item in plan.blockers)


def test_multi_account_family_requires_ras_and_invalid_contact_data_blocks():
    parent = account(Id="parent", ParentId="")
    child = account(Id="account-1", ParentId="parent")
    bad_contact = {**CONTACT, "Email": "not email"}
    plan = build_user_reconciliation_plan(
        bad_contact, [], [child], [parent, child], PROFILES, CONFIG, []
    )

    assert plan.required_profile == ParticipantProfile.RAS
    assert {item.code for item in plan.blockers} >= {"invalid_email"}
    assert "blockers:" in render_user_reconciliation_plan(plan)


def test_plan_is_frozen_and_json_is_stable():
    plan = build_user_reconciliation_plan(
        CONTACT, [], [account()], [account()], PROFILES, CONFIG, []
    )
    with pytest.raises(FrozenInstanceError):
        plan.contact_id = "other"
    assert plan.to_json() == plan.to_json()


def test_service_uses_only_filtered_queries():
    class Client:
        def __init__(self):
            self.calls = []

        def query_records(self, object_name, fields, *, where=None, order_by=None):
            self.calls.append((object_name, tuple(fields), where))
            if object_name == "Contact":
                return [CONTACT]
            if object_name == "Account":
                return (
                    [account()]
                    if "Cert_Certification_Contact__c" in (where or "")
                    else []
                )
            if object_name == "Profile":
                return PROFILES
            return []

        def create_record(self, *args):
            raise AssertionError("write attempted")

        def update_record(self, *args):
            raise AssertionError("write attempted")

    client = Client()
    plan = UserReconciliationService(client).plan(
        "contact-1",
        {
            "PARTICIPANT_PROFILE_ID": ParticipantProfile.PARTICIPANT.value,
            "PARTICIPANT_PRINCIPAL_PROFILE_ID": ParticipantProfile.PRINCIPAL.value,
            "PARTICIPANT_AP_PROFILE_ID": ParticipantProfile.AP.value,
            "PARTICIPANT_QC_PROFILE_ID": ParticipantProfile.QC.value,
            "PARTICIPANT_RAS_PROFILE_ID": ParticipantProfile.RAS.value,
        },
    )

    assert plan.proposed_operation == "create"
    assert all(where for _, _, where in client.calls)
    assert client.calls[0][2] == "Id = 'contact-1'"


def test_apply_updates_only_allowed_changed_fields_and_reports_login_event():
    current_user = {
        "Id": "user-1", "IsActive": True, "ProfileId": "old-profile",
        "FirstName": "Ada", "LastName": "Byron", "Email": "ada@old.example",
        "Username": "ada@old.example", "Alias": "leave-me-alone",
    }
    reviewed = build_user_reconciliation_plan(
        CONTACT, [current_user], [account()], [account()], PROFILES, CONFIG, []
    )

    class Client:
        def update_record(self, object_name, record_id, values):
            self.updated = (object_name, record_id, values)

    client = Client()
    service = UserReconciliationService(client)
    service.plan = lambda contact_id, environment: reviewed  # type: ignore[method-assign]

    result = service.apply(reviewed, "contact-1", {})

    assert client.updated == (
        "User", "user-1",
        {
            "ProfileId": ParticipantProfile.PARTICIPANT.value,
            "LastName": "Lovelace",
            "Email": "ada@example.com",
            "Username": "ada@example.com",
        },
    )
    assert {item["field"]: item["status"] for item in result.fields} == {
        "ProfileId": "applied", "FirstName": "skipped", "LastName": "applied",
        "Email": "applied", "Username": "applied",
    }
    assert result.events[0]["type"] == "login_identity_changed"


def test_apply_rejects_stale_plan_before_writing():
    user = {"Id": "user-1", "IsActive": True, "Email": "old@example.com"}
    reviewed = build_user_reconciliation_plan(
        CONTACT, [user], [account()], [account()], PROFILES, CONFIG, []
    )
    stale = build_user_reconciliation_plan(
        CONTACT, [{**user, "Email": "newer@example.com"}], [account()], [account()], PROFILES, CONFIG, []
    )

    class Client:
        def update_record(self, *args):
            raise AssertionError("stale plan attempted a write")

    service = UserReconciliationService(Client())
    service.plan = lambda contact_id, environment: stale  # type: ignore[method-assign]
    with pytest.raises(ReconciliationPlanError, match="stale"):
        service.apply(reviewed, "contact-1", {})


def test_load_plan_requires_expected_active_user_values(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text('{"contact_id": "contact-1"}', encoding="utf-8")

    with pytest.raises(ReconciliationPlanError, match="missing required"):
        load_user_reconciliation_plan(path)
