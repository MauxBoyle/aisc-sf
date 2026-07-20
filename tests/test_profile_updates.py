from datetime import date

import pytest

from aisc_salesforce.profile_updates import (
    AUDIT_FIELDS,
    SUBMISSION_FIELDS,
    ProfileUpdateService,
    build_submission_summary,
    has_meaningful_explanation,
    is_eligible_audit,
    match_contact,
)
from aisc_salesforce.salesforce import SalesforceError

TODAY = date(2026, 7, 16)


def audit(**changes):
    record = {
        "Id": "audit-1",
        "Name": "A-100",
        "Cert_Audit_Date__c": "2026-07-01",
        "Company_Profile_Change_Form__c": True,
        "Explanation_for_Profile_Change_Form__c": "The company moved.",
        "Cert_Account__c": "account-1",
        "Cert_Account__r": {"Name": "Acme Steel"},
        "Cert_Contact__c": "contact-cert",
    }
    record.update(changes)
    return record


def submission(**changes):
    record = {
        "Id": "submission-1",
        "Name": "PU-100",
        "CreatedDate": "2026-07-15T14:30:00.000+0000",
        "Status__c": "New",
        "Account__c": "account-1",
        "Account__r": {"Name": "Acme Steel"},
        "Email__c": "submitter@example.com",
        "Revised_Company_Name__c": "Acme Steel LLC",
        "Comments__c": "New legal name",
    }
    record.update(changes)
    return record


class FakeClient:
    def __init__(self, *, audits=None, submissions=None, cases=None, contacts=None):
        self.audits = audits or []
        self.submissions = submissions or []
        self.cases = cases or {}
        self.contacts = contacts or []
        self.feeds = {}
        self.created = []
        self.updated = []
        self.posted = []
        self.queries = []

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        self.queries.append((object_name, fields, where, order_by))
        if object_name == "Cert_Audit__c":
            return self.audits
        if object_name == "Company_Profile_Change__c":
            return self.submissions
        if object_name == "Case":
            account_id = where.split("AccountId = '", 1)[1].split("'", 1)[0]
            return list(self.cases.get(account_id, []))
        if object_name == "Contact":
            return list(self.contacts)
        raise AssertionError(object_name)

    def create_record(self, object_name, payload):
        record_id = f"case-created-{len(self.created) + 1}"
        self.created.append((object_name, payload))
        return record_id

    def update_record(self, object_name, record_id, payload):
        self.updated.append((object_name, record_id, payload))

    def get_record(self, object_name, record_id, fields):
        return {"Id": record_id, "CaseNumber": "00012345"}

    def get_feed_messages(self, record_id):
        return list(self.feeds.get(record_id, []))

    def post_feed_message(self, record_id, message):
        self.posted.append((record_id, message))
        self.feeds.setdefault(record_id, []).append(message)


def service(client):
    return ProfileUpdateService(client, "queue-id", "responder-id", today=TODAY)


@pytest.mark.parametrize("value", [None, "", "  ", "None", " none ", "N/A", " n/a "])
def test_explanation_must_be_meaningful(value):
    assert not has_meaningful_explanation(value)


def test_audit_window_is_inclusive_and_excludes_future_dates():
    assert is_eligible_audit(audit(Cert_Audit_Date__c="2026-06-16"), TODAY)
    assert is_eligible_audit(audit(Cert_Audit_Date__c="2026-07-16"), TODAY)
    assert not is_eligible_audit(audit(Cert_Audit_Date__c="2026-06-15"), TODAY)
    assert not is_eligible_audit(audit(Cert_Audit_Date__c="2026-07-17"), TODAY)
    assert not is_eligible_audit(
        audit(Explanation_for_Profile_Change_Form__c="N/A"), TODAY
    )


def test_audit_creates_expected_case_and_posts_exact_messages():
    client = FakeClient(audits=[audit()])

    counts = service(client).run()

    assert counts.created == 1
    assert client.created == [
        (
            "Case",
            {
                "Subject": (
                    "2026-07-01: Profile Update Expected for Acme Steel based on A-100"
                ),
                "OwnerId": "queue-id",
                "Primary_Responder__c": "responder-id",
                "ContactId": "contact-cert",
                "AccountId": "account-1",
                "Status": "Pending",
                "Origin": "Web",
                "Label_new__c": "Auditing",
                "Sub_Label__c": "Profile Change",
                "Description": "",
            },
        )
    ]
    assert client.posted == [
        ("case-created-1", "The company moved."),
        (
            "audit-1",
            "Profile Change need noted. A pending case (00012345) has been made "
            "on the Acme Steel account. -MB",
        ),
    ]


def test_case_duplicate_checks_are_scoped_to_the_audit_account():
    other_case = {
        "Id": "other-case",
        "Subject": "2026-07-01: Profile Update Expected elsewhere",
        "CreatedDate": "2026-07-02T00:00:00.000+0000",
    }
    client = FakeClient(audits=[audit()], cases={"other-account": [other_case]})

    counts = service(client).run()

    assert counts.created == 1
    case_query = next(query for query in client.queries if query[0] == "Case")
    assert "AccountId = 'account-1'" in case_query[2]


def test_audit_reuses_received_case_and_reopens_only_when_closed():
    received = {
        "Id": "received-1",
        "CaseNumber": "0042",
        "Subject": "2026-06-01: Profile Update Received for Acme - PU-1",
        "Status": "Closed",
        "IsClosed": True,
        "CreatedDate": "2026-06-01T00:00:00.000+0000",
    }
    client = FakeClient(audits=[audit()], cases={"account-1": [received]})

    counts = service(client).run()

    assert counts.reused == 1
    assert client.updated == [("Case", "received-1", {"Status": "Pending"})]
    assert client.posted[0] == ("received-1", "The company moved.")


def test_audit_recognizes_aisc_received_case():
    received = {
        "Id": "received-1",
        "CaseNumber": "0042",
        "Subject": "AISC Profile Update for Acme Steel - PU-100 26-07-15",
        "Status": "Working",
        "IsClosed": False,
        "CreatedDate": "2026-07-15T00:00:00.000+0000",
    }
    client = FakeClient(audits=[audit()], cases={"account-1": [received]})

    counts = service(client).run()

    assert counts.reused == 1
    assert client.created == []
    assert client.posted[0] == ("received-1", "The company moved.")


def test_audit_preserves_open_received_status_and_skips_newer_other_case():
    open_received = {
        "Id": "received-open",
        "CaseNumber": "0043",
        "Subject": "Profile Update Received for Acme - PU-1",
        "Status": "Working",
        "IsClosed": False,
        "CreatedDate": "2026-06-01T00:00:00.000+0000",
    }
    client = FakeClient(audits=[audit()], cases={"account-1": [open_received]})

    service(client).run()

    assert not client.updated

    other_profile_case = {
        "Id": "other-profile",
        "Subject": "Manual Profile Update follow-up",
        "Status": "Working",
        "IsClosed": False,
        "CreatedDate": "2026-07-01T00:00:00.000+0000",
    }
    client = FakeClient(audits=[audit()], cases={"account-1": [other_profile_case]})

    counts = service(client).run()

    assert counts.skipped == 1
    assert not client.created


def test_partial_audit_retry_posts_only_missing_message():
    subject = "2026-07-01: Profile Update Expected for Acme Steel based on A-100"
    existing = {
        "Id": "case-existing",
        "CaseNumber": "0042",
        "Subject": subject,
        "Status": "Pending",
        "IsClosed": False,
        "CreatedDate": "2026-07-01T00:00:00.000+0000",
    }
    client = FakeClient(audits=[audit()], cases={"account-1": [existing]})
    client.feeds["case-existing"] = ["The company moved."]

    service(client).run()

    assert client.posted == [
        (
            "audit-1",
            "Profile Change need noted. A pending case (0042) has been made on "
            "the Acme Steel account. -MB",
        )
    ]


def test_new_submission_converts_newest_expected_case():
    expected = {
        "Id": "expected-newest",
        "Subject": "Profile Update Expected for Acme",
        "Status": "Pending",
        "IsClosed": False,
        "CreatedDate": "2026-07-10T00:00:00.000+0000",
    }
    older = {
        **expected,
        "Id": "expected-older",
        "CreatedDate": "2026-07-01T00:00:00.000+0000",
    }
    contacts = [
        {"Id": "contact-1", "AccountId": "account-1", "Email": "SUBMITTER@example.com"}
    ]
    client = FakeClient(
        submissions=[submission()],
        cases={"account-1": [older, expected]},
        contacts=contacts,
    )

    counts = service(client).run()

    assert counts.reused == 1
    assert client.updated == [
        (
            "Case",
            "expected-newest",
            {
                "Subject": "AISC Profile Update for Acme Steel - PU-100 26-07-15",
                "OwnerId": "queue-id",
                "Primary_Responder__c": "responder-id",
                "ContactId": "contact-1",
                "AccountId": "account-1",
                "Status": "Pending",
                "Origin": "Participant Portal",
                "Label_new__c": "Participant Portal",
                "Sub_Label__c": "Profile Change",
                "Description": "",
            },
        )
    ]


def test_new_submission_creates_received_case_with_exact_payload():
    client = FakeClient(submissions=[submission()], contacts=[])

    counts = service(client).run()

    assert counts.created == 1
    assert client.created == [
        (
            "Case",
            {
                "Subject": "AISC Profile Update for Acme Steel - PU-100 26-07-15",
                "OwnerId": "queue-id",
                "Primary_Responder__c": "responder-id",
                "ContactId": None,
                "AccountId": "account-1",
                "Status": "Pending",
                "Origin": "Participant Portal",
                "Label_new__c": "Participant Portal",
                "Sub_Label__c": "Profile Change",
                "Description": "",
            },
        )
    ]
    assert client.posted == [("case-created-1", build_submission_summary(submission()))]


def test_submission_reuses_received_case_and_appends_name_once():
    received = {
        "Id": "received-1",
        "Subject": "AISC Profile Update for Acme Steel - PU-099 26-07-01",
        "Status": "Working",
        "IsClosed": False,
        "CreatedDate": "2026-07-01T00:00:00.000+0000",
    }
    client = FakeClient(submissions=[submission()], cases={"account-1": [received]})

    counts = service(client).run()

    assert counts.reused == 1
    assert not any(query[0] == "Contact" for query in client.queries)
    assert client.updated == [
        (
            "Case",
            "received-1",
            {
                "Subject": (
                    "AISC Profile Update for Acme Steel - "
                    "PU-099 26-07-01 / PU-100 26-07-15"
                )
            },
        )
    ]
    assert "PU-100" in client.posted[0][1]

    client.updated.clear()
    client.posted.clear()
    client.feeds["received-1"] = [build_submission_summary(submission())]
    received["Subject"] += " / PU-100 26-07-15"
    retry_counts = service(client).run()
    assert retry_counts.skipped == 1
    assert not client.updated
    assert not client.posted


def test_existing_aisc_update_skips_before_reading_chatter_or_writing():
    received = {
        "Id": "received-1",
        "Subject": (
            "AISC Profile Update for Acme Steel - PU-099 26-07-01 / pu-100 26-07-15"
        ),
        "Status": "Working",
        "IsClosed": False,
        "CreatedDate": "2026-07-15T15:00:00.000+0000",
    }
    client = FakeClient(submissions=[submission()], cases={"account-1": [received]})

    counts = service(client).run()

    assert counts.skipped == 1
    assert client.updated == []
    assert client.posted == []
    assert client.feeds == {}


def test_partial_profile_update_number_does_not_count_as_duplicate():
    received = {
        "Id": "received-1",
        "Subject": "AISC Profile Update for Acme Steel - PU-100 26-07-01",
        "Status": "Working",
        "IsClosed": False,
        "CreatedDate": "2026-07-01T00:00:00.000+0000",
    }
    client = FakeClient(
        submissions=[submission(Name="PU-10")],
        cases={"account-1": [received]},
    )

    counts = service(client).run()

    assert counts.reused == 1
    assert client.updated[0][2]["Subject"].endswith(" / PU-10 26-07-15")


def test_legacy_retry_still_posts_missing_summary_without_renaming_subject():
    received = {
        "Id": "received-1",
        "Subject": "2026-07-01: Profile Update Received for Acme Steel - PU-100",
        "Status": "Working",
        "IsClosed": False,
        "CreatedDate": "2026-07-01T00:00:00.000+0000",
    }
    client = FakeClient(submissions=[submission()], cases={"account-1": [received]})

    counts = service(client).run()

    assert counts.reused == 1
    assert client.updated == []
    assert client.posted == [("received-1", build_submission_summary(submission()))]


def test_contact_matching_prefers_account_and_rejects_ambiguity():
    contacts = [
        {"Id": "other", "AccountId": "account-2", "Email": "person@example.com"},
        {"Id": "preferred", "AccountId": "account-1", "Email": "PERSON@example.com"},
    ]
    assert match_contact(contacts, "person@EXAMPLE.com", "account-1") == "preferred"
    assert (
        match_contact(
            contacts + [{**contacts[1], "Id": "duplicate"}],
            "person@example.com",
            "account-1",
        )
        is None
    )


def test_summary_is_deterministic_readable_and_omits_blank_fields():
    text = build_submission_summary(
        submission(Phone__c="", Effective_Date__c=None, Type__c="Address change")
    )
    assert text.startswith("Profile Update PU-100\n\nSubmission details:")
    assert "Revised Company Name: Acme Steel LLC" in text
    assert "Comments: New legal name" in text
    assert "Type: Address change" in text
    assert "Phone" not in text
    assert "Effective Date" not in text


def test_submission_subject_over_255_characters_fails_without_truncation():
    client = FakeClient(submissions=[submission(Name="X" * 230)])

    counts = service(client).run()

    assert counts.failed == 1
    assert not client.created


def test_one_record_failure_does_not_prevent_later_records_and_is_retryable():
    class FailingOnceClient(FakeClient):
        def post_feed_message(self, record_id, message):
            if record_id == "audit-1":
                raise SalesforceError("temporary feed error")
            super().post_feed_message(record_id, message)

    client = FailingOnceClient(
        audits=[audit(), audit(Id="audit-2", Cert_Account__c="account-2")]
    )

    counts = service(client).run()

    assert counts.created == 1
    assert counts.failed == 1
    assert len(client.created) == 2


def test_service_queries_only_recent_audits_and_new_submissions():
    client = FakeClient()
    service(client).run()
    audit_query, submission_query = client.queries[:2]
    assert audit_query[1] == AUDIT_FIELDS
    assert "Cert_Audit_Date__c >= 2026-06-16" in audit_query[2]
    assert "Cert_Audit_Date__c <= 2026-07-16" in audit_query[2]
    assert submission_query[1] == SUBMISSION_FIELDS
    assert submission_query[2] == "Status__c = 'New'"
