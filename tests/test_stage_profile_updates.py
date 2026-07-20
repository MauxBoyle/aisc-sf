import csv
import json
from datetime import UTC, datetime

import pytest

from aisc_salesforce.stage_profile_updates import (
    ACCOUNT_FIELDS,
    CONTACT_FIELDS,
    CSV_COLUMNS,
    ProfileUpdateStagingService,
    write_staged_profile_updates,
)


def submission(**changes):
    record = {
        "Id": "submission-1",
        "Name": "PU-100",
        "CreatedDate": "2026-07-15T14:30:00.000+0000",
        "Status__c": "New",
        "Account__c": "account-1",
        "Account__r": {"Name": "Acme Steel"},
        "Email__c": "submitter@example.com",
        "Name__c": "Sam Submitter",
        "Phone__c": "312-555-0100",
    }
    record.update(changes)
    return record


def account(**changes):
    record = {
        "Id": "account-1",
        "Name": "Acme Steel",
        "Certification_ID__c": "C-100",
        "BillingStreet": "1 Main St",
        "BillingCity": "Chicago",
        "BillingState": "IL",
        "BillingPostalCode": "60601",
        "BillingCountry": "USA",
        "ParentId": "parent-1",
        "Cert_Certification_Contact__c": "contact-cert",
        "Cert_Principal_Contact__c": "contact-principal",
        "Cert_Accounting_Contact__c": "contact-accounting",
        "Cert_Marketing_Contact__c": "contact-quality",
        "Cert_Safety_Contact__c": "contact-ny",
    }
    record.update(changes)
    return record


class FakeClient:
    def __init__(
        self,
        submissions,
        accounts=None,
        siblings=None,
        contacts=None,
        cases=None,
    ):
        self.submissions = submissions
        self.accounts = accounts or []
        self.siblings = siblings or []
        self.contacts = contacts or []
        self.cases = (
            cases
            if cases is not None
            else [
                {
                    "Id": "case-1",
                    "CaseNumber": "00010001",
                    "Subject": (
                        "Profile Update Received - "
                        + " / ".join(
                            dict.fromkeys(
                                record.get("Name", "")
                                for record in submissions
                                if record.get("Name")
                            )
                        )
                    ),
                    "Status": "Pending",
                    "CreatedDate": "2026-07-15T15:00:00.000+0000",
                    "AccountId": "account-1",
                }
            ]
        )
        self.queries = []

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        self.queries.append((object_name, fields, where, order_by))
        if object_name == "Company_Profile_Change__c":
            return list(self.submissions)
        if object_name == "Account":
            if where and where.startswith("Id IN"):
                return list(self.accounts)
            return list(self.siblings)
        if object_name == "Contact":
            return list(self.contacts)
        if object_name == "Case":
            return list(self.cases)
        raise AssertionError(object_name)


def stage(submissions, *, accounts=None, siblings=None, contacts=None, cases=None):
    client = FakeClient(submissions, accounts, siblings, contacts, cases)
    result = ProfileUpdateStagingService(client).stage()
    return result, client


def test_merges_same_account_and_email_with_later_nonblank_values():
    first = submission(
        Id="submission-1",
        Name="PU-100",
        Revised_Company_Name__c="Acme Company",
        Comments__c="First comment",
    )
    second = submission(
        Id="submission-2",
        Name="PU-101",
        CreatedDate="2026-07-16T14:30:00.000+0000",
        Email__c=" SUBMITTER@EXAMPLE.COM ",
        Revised_Company_Name__c="Acme Company LLC",
        Comments__c="",
    )

    result, client = stage([second, first], accounts=[account()])

    assert len(result.rows) == 1
    row = result.rows[0]
    assert json.loads(row["source_submission_ids"]) == [
        "submission-1",
        "submission-2",
    ]
    assert json.loads(row["source_submission_names"]) == ["PU-100", "PU-101"]
    assert row["earliest_submission_date"] == first["CreatedDate"]
    assert row["latest_submission_date"] == second["CreatedDate"]
    assert row["revised_company_name"] == "Acme Company LLC"
    assert row["comments"] == "First comment"
    assert client.queries[0][3] == "CreatedDate ASC, Id ASC"
    assert client.queries[1][1] == ACCOUNT_FIELDS
    contact_query = next(query for query in client.queries if query[0] == "Contact")
    assert contact_query[1] == CONTACT_FIELDS


def test_keeps_different_emails_separate_and_blank_emails_independent():
    records = [
        submission(Id="one", Email__c="first@example.com"),
        submission(Id="two", Email__c="second@example.com"),
        submission(Id="three", Email__c=""),
        submission(Id="four", Email__c="  "),
    ]

    result, _ = stage(records, accounts=[account()])

    assert len(result.rows) == 4
    blank_rows = [row for row in result.rows if not row["submitter_email"]]
    assert len(blank_rows) == 2
    assert all(row["has_warnings"] == "true" for row in blank_rows)
    assert all("blank submitter email" in row["warnings"].lower() for row in blank_rows)


def test_different_accounts_stay_separate_and_merged_row_can_have_key_and_role_data():
    records = [
        submission(
            Id="key",
            Revised_Company_Owner__c="Taylor Owner",
        ),
        submission(
            Id="role",
            CreatedDate="2026-07-16T14:30:00.000+0000",
            Cert_Email__c="new-cert@example.com",
        ),
        submission(
            Id="other-account",
            Account__c="account-2",
            Account__r={"Name": "Other Steel"},
        ),
    ]

    result, _ = stage(
        records,
        accounts=[account(), account(Id="account-2", Name="Other Steel")],
    )

    assert len(result.rows) == 2
    merged = next(row for row in result.rows if row["account_id"] == "account-1")
    assert merged["revised_company_owner"] == "Taylor Owner"
    assert merged["certification_email"] == "new-cert@example.com"


def test_partial_address_uses_account_billing_address():
    record = submission(
        Revised_Facility_Street__c="99 New Ave",
        Revised_Facility_City__c="",
    )

    result, _ = stage([record], accounts=[account()])

    row = result.rows[0]
    assert row["revised_facility_street"] == "99 New Ave"
    assert row["revised_facility_city"] == "Chicago"
    assert row["revised_facility_state"] == "IL"
    assert row["revised_facility_zip"] == "60601"
    assert row["revised_facility_country"] == "USA"


def test_complete_address_does_not_use_account_billing_values():
    record = submission(
        Revised_Facility_Street__c="2 New St",
        Revised_Facility_City__c="Gary",
        Revised_Facility_State__c="IN",
        Revised_Facility_Zip__c="46402",
        Revised_Facility_Country__c="USA",
    )

    result, _ = stage([record], accounts=[account()])

    row = result.rows[0]
    assert [
        row["revised_facility_street"],
        row["revised_facility_city"],
        row["revised_facility_state"],
        row["revised_facility_zip"],
        row["revised_facility_country"],
    ] == ["2 New St", "Gary", "IN", "46402", "USA"]


def test_key_answers_are_labeled_and_blank_roles_stay_empty():
    record = submission(
        Type__c=" key DATA ",
        Did_the_Cert_contact_change__c="Yes",
        Will_software_change__c="No",
    )

    result, _ = stage([record], accounts=[account()])

    row = result.rows[0]
    assert "Certification contact changed: Yes" in row["key_answers"]
    assert "Software will change: No" in row["key_answers"]
    assert len(row["key_answers"].splitlines()) == 8
    for column in CSV_COLUMNS:
        if column.startswith("certification_") and column != "certification_id":
            assert row[column] == ""


def test_name_resolution_stops_at_first_candidate_tier():
    record = submission(
        Cert_First_Name__c="Jordan",
        Cert_Last_Name__c="Lee",
        Principal_First_Name__c=" jordan ",
        Principal_Last_Name__c="LEE",
        Principal_Email__c="jordan@example.com",
    )
    contacts = [
        {
            "Id": "account-jordan",
            "AccountId": "account-1",
            "FirstName": "Jordan",
            "LastName": "Lee",
            "Title": "Manager",
            "Email": "old@example.com",
            "Phone": "123",
        }
    ]

    result, _ = stage([record], accounts=[account()], contacts=contacts)

    row = result.rows[0]
    assert row["certification_resolution_action"] == "use_submitted_contact"
    assert row["certification_resolution_source"] == "submitted_role"
    assert row["certification_source_submission_id"] == "submission-1"
    assert row["certification_source_role"] == "principal"
    assert row["certification_salesforce_contact_id"] == ""


def test_same_submitted_contact_in_multiple_roles_is_not_ambiguous():
    record = submission(
        Cert_First_Name__c="Jordan",
        Cert_Last_Name__c="Lee",
        Principal_First_Name__c="Jordan",
        Principal_Last_Name__c="Lee",
        Principal_Title__c="President",
        Principal_Email__c="jordan@example.com",
        Principal_Phone__c="312-555-0142",
        AP_First_Name__c="Jordan",
        AP_Last_Name__c="Lee",
        AP_Email__c="jordan@example.com",
    )

    result, _ = stage([record], accounts=[account()])

    row = result.rows[0]
    assert row["certification_resolution_action"] == "use_submitted_contact"
    assert row["certification_title"] == "President"
    assert row["certification_phone"] == "312-555-0142"
    assert row["certification_warning"] == ""
    assert row["has_warnings"] == "false"


def test_same_submitted_name_with_different_emails_is_ambiguous():
    record = submission(
        Cert_First_Name__c="Jordan",
        Cert_Last_Name__c="Lee",
        Principal_First_Name__c="Jordan",
        Principal_Last_Name__c="Lee",
        Principal_Email__c="first.jordan@example.com",
        AP_First_Name__c="Jordan",
        AP_Last_Name__c="Lee",
        AP_Email__c="second.jordan@example.com",
    )

    result, _ = stage([record], accounts=[account()])

    row = result.rows[0]
    assert row["certification_resolution_action"] == ""
    assert "ambiguous" in row["certification_warning"].lower()
    assert row["has_warnings"] == "true"


def test_name_resolution_uses_account_then_sibling_contacts_and_warns_on_ambiguity():
    account_contact = {
        "Id": "account-match",
        "AccountId": "account-1",
        "FirstName": "Taylor",
        "LastName": "Kim",
        "Email": "taylor@example.com",
    }
    sibling_contact = {
        "Id": "sibling-match",
        "AccountId": "sibling-1",
        "FirstName": "Morgan",
        "LastName": "Reed",
        "Email": "morgan@example.com",
    }
    record = submission(
        Cert_First_Name__c="Taylor",
        Principal_First_Name__c="Morgan",
        Principal_Last_Name__c="Reed",
    )
    sibling = account(Id="sibling-1", Name="Acme West")

    result, _ = stage(
        [record],
        accounts=[account()],
        siblings=[sibling],
        contacts=[account_contact, sibling_contact],
    )

    row = result.rows[0]
    assert row["certification_salesforce_contact_id"] == "account-match"
    assert row["certification_resolution_source"] == "account_contact"
    assert row["principal_salesforce_contact_id"] == "sibling-match"
    assert row["principal_resolution_source"] == "sibling_contact"

    result, _ = stage(
        [submission(Cert_First_Name__c="Taylor")],
        accounts=[account()],
        contacts=[account_contact, {**account_contact, "Id": "duplicate"}],
    )
    assert "ambiguous" in result.rows[0]["certification_warning"].lower()


def test_existing_contact_fills_optional_title_and_phone_without_warning():
    matching = {
        "Id": "existing-contact",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Certification Manager",
        "Email": "alex@example.com",
        "Phone": "312-555-0105",
    }
    record = submission(
        Cert_First_Name__c="Alex",
        Cert_Last_Name__c="Smith",
        Cert_Email__c="alex@example.com",
    )

    result, _ = stage([record], accounts=[account()], contacts=[matching])

    row = result.rows[0]
    assert row["certification_salesforce_contact_id"] == "existing-contact"
    assert row["certification_title"] == "Certification Manager"
    assert row["certification_phone"] == "312-555-0105"
    assert row["certification_warning"] == ""
    assert row["has_warnings"] == "false"


def test_email_only_can_match_another_submitted_role():
    record = submission(
        Cert_Email__c="shared@example.com",
        Principal_Email__c=" SHARED@example.com ",
    )

    result, _ = stage([record], accounts=[account()])

    row = result.rows[0]
    assert row["certification_resolution_action"] == "use_submitted_contact"
    assert row["certification_resolution_source"] == "submitted_role"
    assert row["certification_source_role"] == "principal"
    assert row["certification_warning"] == ""


def test_email_and_partial_field_resolution_use_existing_role_contact():
    matching = {
        "Id": "existing-email",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Email": "known@example.com",
    }
    record = submission(
        Cert_Email__c="KNOWN@example.com",
        AP_Email__c="new@example.com",
        QC_Title__c="Quality Director",
        NY_Phone__c="312-555-0188",
    )

    result, _ = stage([record], accounts=[account()], contacts=[matching])

    row = result.rows[0]
    assert row["certification_resolution_action"] == "update_contact"
    assert row["certification_salesforce_contact_id"] == "existing-email"
    assert row["accounting_resolution_action"] == "change_email"
    assert row["accounting_salesforce_contact_id"] == "contact-accounting"
    assert row["quality_resolution_action"] == "update_contact"
    assert row["quality_salesforce_contact_id"] == "contact-quality"
    assert row["new_york_resolution_action"] == "update_contact"
    assert row["new_york_salesforce_contact_id"] == "contact-ny"


def test_role_lookup_contact_fills_missing_optional_details():
    quality_contact = {
        "Id": "contact-quality",
        "AccountId": "account-1",
        "FirstName": "Quinn",
        "LastName": "Davis",
        "Title": "Old title",
        "Email": "quinn@example.com",
        "Phone": "312-555-0199",
    }
    record = submission(QC_Title__c="Quality Director")

    result, _ = stage(
        [record],
        accounts=[account()],
        contacts=[quality_contact],
    )

    row = result.rows[0]
    assert row["quality_title"] == "Quality Director"
    assert row["quality_phone"] == "312-555-0199"
    assert row["quality_warning"] == ""


def test_unmatched_named_contact_records_create_intent_and_warning():
    record = submission(
        Cert_First_Name__c="New",
        Cert_Last_Name__c="Person",
        Cert_Email__c="new.person@example.com",
    )

    result, _ = stage([record], accounts=[account()])

    row = result.rows[0]
    assert row["certification_resolution_action"] == "create_contact"
    assert "new contact will need to be created" in row["certification_warning"].lower()
    assert row["has_warnings"] == "true"


def test_unmatched_first_name_only_records_new_contact_warning():
    result, _ = stage(
        [submission(Cert_First_Name__c="Unlisted")],
        accounts=[account()],
    )

    row = result.rows[0]
    assert row["certification_resolution_action"] == "create_contact"
    assert "new contact will need to be created" in row["certification_warning"].lower()


def test_missing_account_and_last_name_only_are_staged_with_warnings():
    record = submission(
        Account__c="missing-account",
        Cert_Last_Name__c="Nguyen",
    )

    result, _ = stage([record])

    row = result.rows[0]
    assert row["account_id"] == "missing-account"
    assert row["account_name"] == "Acme Steel"
    assert row["has_warnings"] == "true"
    assert row["certification_resolution_action"] == "create_contact"
    assert "could not be retrieved" in row["warnings"]
    assert "last-name-only" in row["certification_warning"].lower()


def test_unresolved_name_and_missing_role_lookup_have_explicit_warnings():
    record = submission(
        Cert_First_Name__c="Bob",
        Cert_Last_Name__c="Jones",
        AP_Title__c="Controller",
    )
    contact = {
        "Id": "robert",
        "AccountId": "account-1",
        "FirstName": "Robert",
        "LastName": "Jones",
        "Email": "robert@example.com",
    }

    result, _ = stage(
        [record],
        accounts=[account(Cert_Accounting_Contact__c="")],
        contacts=[contact],
    )

    row = result.rows[0]
    assert row["certification_resolution_action"] == "create_contact"
    assert "new contact will need to be created" in row["certification_warning"]
    assert row["accounting_resolution_action"] == "update_contact"
    assert "no existing Accounting role contact" in row["accounting_warning"]


def test_writer_publishes_csv_atomically_and_repeated_runs_are_independent(tmp_path):
    rows = [{column: "" for column in CSV_COLUMNS}]
    now = datetime(2026, 7, 17, 12, 30, tzinfo=UTC)

    first = write_staged_profile_updates(rows, tmp_path, now=now)
    second = write_staged_profile_updates(rows, tmp_path, now=now)

    assert first != second
    assert first.name == "2026-07-17T12-30-00Z"
    assert second.name == "2026-07-17T12-30-00Z-01"
    assert not list(tmp_path.glob(".*.tmp"))
    assert (first / "profile_updates.csv").read_bytes() == (
        second / "profile_updates.csv"
    ).read_bytes()
    with (first / "profile_updates.csv").open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == CSV_COLUMNS
        assert list(reader) == rows


def test_writer_removes_temporary_output_after_failure(monkeypatch, tmp_path):
    from aisc_salesforce import stage_profile_updates

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(stage_profile_updates, "_write_profile_updates_csv", fail)

    with pytest.raises(OSError, match="disk full"):
        write_staged_profile_updates([], tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_staging_records_case_and_key_update_metadata():
    record = submission(
        Type__c="Key Data",
        Revised_Company_Name__c="Acme Steel LLC",
    )

    result, client = stage([record], accounts=[account()])

    row = result.rows[0]
    assert row["case_id"] == "case-1"
    assert row["case_number"] == "00010001"
    assert row["case_match_status"] == "matched"
    assert row["has_key_updates"] == "true"
    assert row["earliest_key_update_date"] == record["CreatedDate"]
    case_query = next(query for query in client.queries if query[0] == "Case")
    assert "AccountId IN ('account-1')" in case_query[2]


def test_staging_matches_aisc_pairs_without_treating_dates_as_update_names():
    records = [
        submission(Name="PU-099"),
        submission(
            Id="submission-2",
            Name="PU-100",
            CreatedDate="2026-07-16T14:30:00.000+0000",
        ),
    ]
    aisc_case = {
        "Id": "case-1",
        "CaseNumber": "0001",
        "Subject": (
            "AISC Profile Update for Acme Steel - PU-099 26-07-01 / PU-100 26-07-15"
        ),
        "Status": "Pending",
        "CreatedDate": "2026-07-16T15:00:00.000+0000",
        "AccountId": "account-1",
    }

    result, _ = stage(records, accounts=[account()], cases=[aisc_case])

    assert result.rows[0]["case_match_status"] == "matched"
    assert result.rows[0]["case_id"] == "case-1"


def test_staging_profile_update_matching_is_exact_not_partial():
    aisc_case = {
        "Id": "case-1",
        "CaseNumber": "0001",
        "Subject": "AISC Profile Update for Acme Steel - PU-100 26-07-15",
        "Status": "Pending",
        "CreatedDate": "2026-07-15T15:00:00.000+0000",
        "AccountId": "account-1",
    }

    result, _ = stage(
        [submission(Name="PU-10")],
        accounts=[account()],
        cases=[aisc_case],
    )

    assert result.rows[0]["case_match_status"] == "missing"


def test_missing_or_ambiguous_case_match_is_a_blocking_warning():
    record = submission()

    missing, _ = stage([record], accounts=[account()], cases=[])
    assert missing.rows[0]["case_match_status"] == "missing"
    assert missing.rows[0]["case_id"] == ""
    assert "blocking" in missing.rows[0]["warnings"].lower()

    matching_case = {
        "Id": "case-1",
        "CaseNumber": "0001",
        "Subject": "Profile Update Received - PU-100",
        "Status": "Pending",
        "CreatedDate": "2026-07-15T15:00:00.000+0000",
        "AccountId": "account-1",
    }
    ambiguous, _ = stage(
        [record],
        accounts=[account()],
        cases=[matching_case, {**matching_case, "Id": "case-2"}],
    )
    assert ambiguous.rows[0]["case_match_status"] == "ambiguous"
    assert ambiguous.rows[0]["case_id"] == ""
    assert "blocking" in ambiguous.rows[0]["warnings"].lower()
