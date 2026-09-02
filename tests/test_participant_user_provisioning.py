"""Tests for the post-email external User provisioning guardrails."""

from datetime import date

import pytest

from aisc_salesforce.participant_user_provisioning import (
    ParticipantUserProvisioningError,
    ParticipantUserProvisioningService,
)
from aisc_salesforce.salesforce import SalesforceError
from aisc_salesforce.user_reconciliation import UserReconciliationPlan

ENVIRONMENT = {
    "EXTERNAL_USER_LICENSE_NAME": "Customer Community Plus",
    "EXTERNAL_USER_ACCOUNT_ELIGIBILITY_FIELD": "Portal_Eligible__c",
    "EXTERNAL_USER_ACCOUNT_ELIGIBILITY_VALUE": "Yes",
    "PARTICIPANT_PROFILE_ID": "00e5w000000k7KfAAI",
    "PARTICIPANT_PRINCIPAL_PROFILE_ID": "00e5w000000kDqiAAE",
    "PARTICIPANT_AP_PROFILE_ID": "00e5w000000kDqdAAE",
    "PARTICIPANT_QC_PROFILE_ID": "00e5w000000kDqnAAE",
    "PARTICIPANT_RAS_PROFILE_ID": "00e5w000000kDqsAAE",
}


def planned_create():
    payload = {
        "ContactId": "contact-1",
        "FirstName": "Ada",
        "LastName": "Lovelace",
        "Email": "ada@example.com",
        "ProfileId": "profile-1",
        "Username": "ada@example.com",
        "Alias": "lovela26",
        "TimeZoneSidKey": "America/Chicago",
        "LocaleSidKey": "en_US",
        "LanguageLocaleKey": "en_US",
        "EmailEncodingKey": "UTF-8",
    }
    return UserReconciliationPlan(
        "contact-1",
        tuple(payload.items()),
        "profile-1",
        (),
        (),
        (),
        (),
        "create",
        tuple(payload.items()),
        (),
        (),
    )


class Client:
    def __init__(self, *, rows=None, license_error=False):
        self.rows = rows or {}
        self.license_error = license_error
        self.created = []

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        if object_name == "UserLicense" and self.license_error:
            raise SalesforceError("not permitted")
        if object_name == "User" and where and not where.startswith("Id ="):
            return []
        return self.rows.get(object_name, [])

    def create_record(self, object_name, values):
        self.created.append((object_name, values))
        return "user-1"


def valid_rows():
    return {
        "Contact": [{"Id": "contact-1", "AccountId": "account-1"}],
        "Account": [
            {"Id": "account-1", "OwnerId": "owner-1", "Portal_Eligible__c": "Yes"}
        ],
        "User": [{"Id": "owner-1", "IsActive": True, "UserRoleId": "role-1"}],
        "Profile": [
            {"Id": "profile-1", "UserLicense": {"Name": "Customer Community Plus"}}
        ],
        "UserLicense": [
            {
                "Id": "license-1",
                "Name": "Customer Community Plus",
                "TotalLicenses": 10,
                "UsedLicenses": 2,
            }
        ],
    }


def service(client):
    result = ParticipantUserProvisioningService(client, clock=lambda: date(2026, 1, 1))
    result._planner.plan = lambda *_: planned_create()  # type: ignore[method-assign]
    return result


def test_creates_valid_external_user_payload():
    client = Client(rows=valid_rows())
    outcomes = service(client).provision({"contact-1"}, ENVIRONMENT)

    assert outcomes[0].action == "created"
    assert client.created == [
        ("User", {**dict(planned_create().proposed_create or ()), "IsActive": True})
    ]


@pytest.mark.parametrize(
    ("object_name", "replacement", "code"),
    [
        ("Contact", [{"Id": "contact-1", "AccountId": ""}], "contact_account_missing"),
        (
            "Account",
            [{"Id": "account-1", "OwnerId": "owner-1", "Portal_Eligible__c": "No"}],
            "account_not_eligible",
        ),
        (
            "User",
            [{"Id": "owner-1", "IsActive": False, "UserRoleId": ""}],
            "account_owner_invalid",
        ),
        (
            "Profile",
            [{"Id": "profile-1", "UserLicense": {"Name": "Wrong"}}],
            "profile_license_mismatch",
        ),
    ],
)
def test_preflight_blockers_are_actionable(object_name, replacement, code):
    rows = valid_rows()
    rows[object_name] = replacement
    with pytest.raises(ParticipantUserProvisioningError, match=".") as error:
        service(Client(rows=rows)).provision({"contact-1"}, ENVIRONMENT)
    assert error.value.outcome.code == code


def test_exhausted_license_blocks_creation():
    rows = valid_rows()
    rows["UserLicense"][0].update(TotalLicenses=2, UsedLicenses=2)
    with pytest.raises(ParticipantUserProvisioningError) as error:
        service(Client(rows=rows)).provision({"contact-1"}, ENVIRONMENT)
    assert error.value.outcome.code == "license_capacity_exhausted"


def test_duplicate_username_blocks_creation():
    class DuplicateClient(Client):
        def query_records(self, object_name, fields, *, where=None, order_by=None):
            if object_name == "User" and where and "Username" in where:
                return [{"Id": "other-user"}]
            return super().query_records(
                object_name, fields, where=where, order_by=order_by
            )

    with pytest.raises(ParticipantUserProvisioningError) as error:
        service(DuplicateClient(rows=valid_rows())).provision(
            {"contact-1"}, ENVIRONMENT
        )
    assert error.value.outcome.code == "username_collision"


def test_unqueryable_license_capacity_is_a_warning_not_a_blocker():
    client = Client(rows=valid_rows(), license_error=True)
    outcome = service(client).provision({"contact-1"}, ENVIRONMENT)[0]
    assert outcome.action == "created"
    assert "could not be queried" in outcome.warning


def test_race_recheck_reuses_active_linked_user():
    rows = valid_rows()

    # The third User query is the recheck: owner, username, then ContactId.
    class RaceClient(Client):
        def __init__(self):
            super().__init__(rows=rows)
            self.user_calls = 0

        def query_records(self, object_name, fields, *, where=None, order_by=None):
            if object_name == "User":
                self.user_calls += 1
                if self.user_calls == 3:
                    return [
                        {
                            "Id": "racing-user",
                            "IsActive": True,
                            "ContactId": "contact-1",
                        }
                    ]
            return super().query_records(
                object_name, fields, where=where, order_by=order_by
            )

    client = RaceClient()
    outcome = service(client).provision({"contact-1"}, ENVIRONMENT)[0]
    assert outcome.action == "reused"
    assert client.created == []
