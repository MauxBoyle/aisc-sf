import csv
import json
from datetime import UTC, datetime

import pytest

from aisc_salesforce.process_profile_updates import (
    ActionResult,
    ActionStatus,
    ChangeProposal,
    InteractiveProfileUpdateProcessor,
    ProcessingError,
    ProcessingInterrupted,
    ProfileUpdateProcessingWorkflow,
    ReviewDecision,
    build_case_batches,
    format_response_emails,
)
from aisc_salesforce.profile_updates import AutomationCounts
from aisc_salesforce.salesforce import SalesforceError
from aisc_salesforce.stage_profile_updates import CSV_COLUMNS, StagingResult

NOW = datetime(2026, 7, 17, 18, 0, tzinfo=UTC)


def staged_row(**changes):
    row = {column: "" for column in CSV_COLUMNS}
    row.update(
        {
            "source_submission_ids": json.dumps(["submission-1"]),
            "source_submission_names": json.dumps(["PU-100"]),
            "earliest_submission_date": "2026-07-15T14:30:00.000+0000",
            "latest_submission_date": "2026-07-15T14:30:00.000+0000",
            "account_id": "account-1",
            "account_name": "Acme Steel",
            "submitter_name": "Sam Submitter",
            "submitter_email": "sam@example.com",
            "case_id": "case-1",
            "case_number": "00010001",
            "case_status": "Pending",
            "case_match_status": "matched",
            "has_key_updates": "false",
            "has_warnings": "false",
        }
    )
    row.update(changes)
    return row


def source_record(**changes):
    record = {
        "Id": "submission-1",
        "Name": "PU-100",
        "CreatedDate": "2026-07-15T14:30:00.000+0000",
        "Status__c": "New",
        "Account__c": "account-1",
        "Name__c": "Sam Submitter",
        "Email__c": "sam@example.com",
        "Comments__c": "Fresh comment",
        "Other_Personnel_Notes__c": "Fresh personnel note",
    }
    record.update(changes)
    return record


def account_record(**changes):
    record = {
        "Id": "account-1",
        "Name": "Acme Steel",
        "Company_Owner__c": "Old Owner",
        "BillingStreet": "1 Main St",
        "BillingCity": "Chicago",
        "BillingState": "IL",
        "BillingPostalCode": "60601",
        "BillingCountry": "USA",
        "Cert_Certification_Contact__c": "old-contact",
        "Cert_Principal_Contact__c": "",
        "Cert_Accounting_Contact__c": "",
        "Cert_Marketing_Contact__c": "",
        "Cert_Safety_Contact__c": "",
    }
    record.update(changes)
    return record


class Feeder:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeClient:
    def __init__(self, *, source=None, account=None, contacts=None, history=None):
        self.records = {
            ("Company_Profile_Change__c", "submission-1"): source or source_record(),
            ("Account", "account-1"): account or account_record(),
            ("Case", "case-1"): {
                "Id": "case-1",
                "CaseNumber": "00010001",
                "Status": "Pending",
            },
        }
        self.contacts = contacts or []
        for contact in self.contacts:
            self.records[("Contact", contact["Id"])] = contact
        self.history = history or []
        self.get_sequences = {}
        self.queries = []
        self.gets = []
        self.created = []
        self.updated = []
        self.fail_update = None

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        self.queries.append((object_name, fields, where, order_by))
        if object_name == "Contact":
            return [dict(contact) for contact in self.contacts]
        if object_name == "AccountHistory":
            return [dict(item) for item in self.history]
        raise AssertionError(object_name)

    def get_record(self, object_name, record_id, fields):
        self.gets.append((object_name, record_id, tuple(fields)))
        key = (object_name, record_id)
        sequence = self.get_sequences.get(key)
        if sequence:
            return dict(sequence.pop(0))
        return dict(self.records[key])

    def create_record(self, object_name, values):
        record_id = f"created-{len(self.created) + 1}"
        self.created.append((object_name, dict(values)))
        self.records[(object_name, record_id)] = {"Id": record_id, **values}
        if object_name == "Contact":
            self.contacts.append(self.records[(object_name, record_id)])
        return record_id

    def update_record(self, object_name, record_id, values):
        if self.fail_update == (object_name, record_id):
            raise SalesforceError("write failed")
        self.updated.append((object_name, record_id, dict(values)))
        self.records.setdefault((object_name, record_id), {"Id": record_id}).update(
            values
        )


class CaseService:
    def __init__(self, events, *, failed=0):
        self.events = events
        self.failed = failed
        self.errors = ["case failed"] if failed else []

    def run(self):
        self.events.append("cases")
        return AutomationCounts(created=1, failed=self.failed)


class StagingService:
    def __init__(self, events):
        self.events = events

    def stage(self):
        self.events.append("stage")
        return StagingResult([staged_row(account_name="memory value")], 0)


class CapturingProcessor:
    def __init__(self, events):
        self.events = events
        self.rows = None

    def review(self, rows, artifact_dir):
        self.events.append("review")
        self.rows = rows
        return artifact_dir


def test_workflow_creates_cases_then_stages_and_reads_the_published_csv(tmp_path):
    events = []
    processor = CapturingProcessor(events)

    def writer(rows, output_dir):
        events.append("write")
        folder = output_dir / "published"
        folder.mkdir()
        disk_row = {**rows[0], "account_name": "value read from disk"}
        with (folder / "profile_updates.csv").open(
            "w", newline="", encoding="utf-8"
        ) as output:
            csv.DictWriter(output, fieldnames=CSV_COLUMNS).writeheader()
            csv.DictWriter(output, fieldnames=CSV_COLUMNS).writerow(disk_row)
        return folder

    workflow = ProfileUpdateProcessingWorkflow(
        CaseService(events),
        StagingService(events),
        processor,
        staging_writer=writer,
    )

    workflow.run(tmp_path)

    assert events == ["cases", "stage", "write", "review"]
    assert processor.rows[0]["account_name"] == "value read from disk"


@pytest.mark.parametrize("failure", ["cases", "stage", "write"])
def test_workflow_aborts_before_review_when_required_setup_fails(tmp_path, failure):
    events = []
    processor = CapturingProcessor(events)
    case_service = CaseService(events, failed=1 if failure == "cases" else 0)

    class MaybeFailingStaging(StagingService):
        def stage(self):
            if failure == "stage":
                raise SalesforceError("stage failed")
            return super().stage()

    def writer(rows, output_dir):
        events.append("write")
        if failure == "write":
            raise OSError("disk full")
        raise AssertionError("writer should only be reached by the write failure case")

    workflow = ProfileUpdateProcessingWorkflow(
        case_service,
        MaybeFailingStaging(events),
        processor,
        staging_writer=writer,
    )

    with pytest.raises((ProcessingError, SalesforceError, OSError)):
        workflow.run(tmp_path)

    assert "review" not in events


def test_batches_group_account_and_case_and_prioritize_old_key_updates():
    rows = [
        staged_row(
            source_submission_ids='["newer"]',
            earliest_submission_date="2026-07-16T12:00:00+00:00",
            case_id="case-newer",
        ),
        staged_row(
            source_submission_ids='["old-key"]',
            earliest_submission_date="2026-07-01T12:00:00+00:00",
            earliest_key_update_date="2026-07-01T12:00:00+00:00",
            has_key_updates="true",
            case_id="case-key",
        ),
        staged_row(
            source_submission_ids='["same-case"]',
            earliest_submission_date="2026-07-14T12:00:00+00:00",
            case_id="case-newer",
        ),
        staged_row(
            source_submission_ids='["oldest-ordinary"]',
            earliest_submission_date="2026-06-30T12:00:00+00:00",
            case_id="case-old",
        ),
    ]

    batches = build_case_batches(rows, now=NOW)

    assert [batch.case_id for batch in batches] == [
        "case-key",
        "case-old",
        "case-newer",
    ]
    assert len(batches[-1].rows) == 2


def test_blocking_case_match_is_never_guessed():
    with pytest.raises(ProcessingError, match="blocking Case match"):
        build_case_batches(
            [staged_row(case_id="", case_match_status="ambiguous")],
            now=NOW,
        )


def test_account_change_uses_fresh_context_audits_and_closes_completed_batch(tmp_path):
    history = [
        {
            "Id": "history-1",
            "AccountId": "account-1",
            "Field": "Name",
            "OldValue": "Older Acme",
            "NewValue": "Acme Steel",
            "CreatedDate": "2026-07-15T15:15:00.000+0000",
        }
    ]
    client = FakeClient(history=history)
    feeder = Feeder(["apply automatically", "yes"])
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=output.append,
        now=NOW,
    )
    row = staged_row(
        revised_company_name="Acme Steel LLC",
        comments="Staged comment",
        personnel_notes="Staged personnel note",
        warnings="Review this carefully",
        has_warnings="true",
        has_key_updates="true",
        earliest_key_update_date="2026-07-15T14:30:00.000+0000",
    )

    result = processor.review([row], tmp_path)

    displayed = "\n".join(output)
    assert "Fresh comment" in displayed
    assert "Fresh personnel note" in displayed
    assert "Review this carefully" in displayed
    assert "Older Acme" in displayed
    assert ("Account", "account-1", {"Name": "Acme Steel LLC"}) in client.updated
    assert (
        "Company_Profile_Change__c",
        "submission-1",
        {"Status__c": "Closed"},
    ) in client.updated
    assert ("Case", "case-1", {"Status": "Closed"}) in client.updated
    audit = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    applied = next(item for item in audit if item["field"] == "Name")
    assert applied["decision"] == "apply automatically"
    assert applied["result"] == "applied"
    response = result.response_path.read_text(encoding="utf-8")
    assert "Thank you for updating your information with AISC." in response
    assert "Company Name: Acme Steel LLC" in response
    assert "Replaces Acme Steel" in response
    assert any(item[0] == "Company_Profile_Change__c" for item in client.gets)
    history_query = next(
        query for query in client.queries if query[0] == "AccountHistory"
    )
    assert "CreatedDate >=" in history_query[2]
    assert "CreatedDate <" in history_query[2]


def test_current_value_is_an_audited_noop_without_prompt_or_email_item(tmp_path):
    client = FakeClient(account=account_record(Name="Acme Steel LLC"))
    feeder = Feeder([])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    result = processor.review(
        [staged_row(revised_company_name="Acme Steel LLC")],
        tmp_path,
    )

    assert feeder.prompts == []
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        item["result"] == "no-op" and item["field"] == "Name" for item in entries
    )
    assert "Company Name:" not in result.response_path.read_text(encoding="utf-8")


def test_manual_change_is_refetched_and_must_match(tmp_path):
    client = FakeClient()
    client.get_sequences[("Account", "account-1")] = [
        account_record(),
        account_record(Name="Acme Steel LLC"),
    ]
    feeder = Feeder(["make manually", "", "yes"])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    result = processor.review(
        [staged_row(revised_company_name="Acme Steel LLC")],
        tmp_path,
    )

    assert ("Account", "account-1", {"Name": "Acme Steel LLC"}) not in client.updated
    assert "verified manually" in result.audit_path.read_text(encoding="utf-8")


def test_manual_change_mismatch_stops_without_closing_sources(tmp_path):
    client = FakeClient()
    feeder = Feeder(["make manually", ""])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    with pytest.raises(ProcessingError, match="does not match"):
        processor.review(
            [staged_row(revised_company_name="Acme Steel LLC")],
            tmp_path,
        )

    assert not any(item[0] == "Company_Profile_Change__c" for item in client.updated)
    assert ("Case", "case-1", {"Status": "Pending"}) in client.updated


def test_reviewer_selects_contact_then_reviews_fields_and_role_separately(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Manager",
        "Email": "old@example.com",
        "Phone": "312-555-0100",
    }
    client = FakeClient(contacts=[contact])
    feeder = Feeder(
        [
            "select existing",
            "1",
            "apply automatically",
            "will not be made",
            "yes",
        ]
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )
    row = staged_row(
        certification_first_name="Alex",
        certification_last_name="Smith",
        certification_email="new@example.com",
        certification_salesforce_contact_id="contact-1",
        certification_resolution_action="update_contact",
    )

    result = processor.review([row], tmp_path)

    assert ("Contact", "contact-1", {"Email": "new@example.com"}) in client.updated
    assert not any(
        item[0] == "Account" and "Cert_Certification_Contact__c" in item[2]
        for item in client.updated
    )
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        item["field"] == "Email" and item["result"] == "applied" for item in entries
    )
    assert any(
        item["field"] == "Cert_Certification_Contact__c"
        and item["result"] == "rejected"
        for item in entries
    )


def test_incomplete_contact_is_not_automatically_created(tmp_path):
    client = FakeClient()
    feeder = Feeder(["create contact", "will not be made"])
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=output.append,
        now=NOW,
    )

    processor.review(
        [
            staged_row(
                certification_first_name="Only",
                certification_resolution_action="create_contact",
            )
        ],
        tmp_path,
    )

    assert client.created == []
    assert "required Last Name" in "\n".join(output)


def test_valid_contact_creation_precedes_field_and_role_decisions(tmp_path):
    client = FakeClient()
    feeder = Feeder(
        [
            "create contact",
            "apply automatically",
            "apply automatically",
            "apply automatically",
            "yes",
        ]
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    processor.review(
        [
            staged_row(
                certification_first_name="New",
                certification_last_name="Person",
                certification_email="new.person@example.com",
                certification_resolution_action="create_contact",
            )
        ],
        tmp_path,
    )

    assert client.created == [
        (
            "Contact",
            {
                "AccountId": "account-1",
                "FirstName": "New",
                "LastName": "Person",
            },
        )
    ]
    assert (
        "Contact",
        "created-1",
        {"Email": "new.person@example.com"},
    ) in client.updated
    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "created-1"},
    ) in client.updated


def test_email_formatter_creates_one_paragraph_per_submitter():
    first = ChangeProposal(
        source_submission_ids=("one",),
        case_id="case-1",
        account_id="account-1",
        account_name="Acme Steel",
        submitter_email="first@example.com",
        target_object="Account",
        target_record_id="account-1",
        field_name="Name",
        label="Company Name",
        original_value="Acme",
        proposed_value="Acme Steel",
    )
    second = ChangeProposal(
        **{
            **first.__dict__,
            "source_submission_ids": ("two",),
            "submitter_email": "second@example.com",
            "field_name": "BillingCity",
            "label": "Billing City",
            "original_value": "Gary",
            "proposed_value": "Chicago",
        }
    )
    results = [
        ActionResult(first, ReviewDecision.APPLY_AUTOMATICALLY, ActionStatus.APPLIED),
        ActionResult(
            second, ReviewDecision.MAKE_MANUALLY, ActionStatus.VERIFIED_MANUAL
        ),
    ]

    emails = format_response_emails(results)

    assert list(emails) == ["first@example.com", "second@example.com"]
    assert emails["first@example.com"].count("Thank you for updating") == 1
    assert "Company Name: Acme Steel" in emails["first@example.com"]
    assert "Billing City: Chicago" in emails["second@example.com"]


def test_unsent_response_closes_sources_but_keeps_case_pending(tmp_path):
    client = FakeClient()
    feeder = Feeder(["apply automatically", "no"])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    processor.review(
        [staged_row(revised_company_name="Acme Steel LLC")],
        tmp_path,
    )

    assert (
        "Company_Profile_Change__c",
        "submission-1",
        {"Status__c": "Closed"},
    ) in client.updated
    assert ("Case", "case-1", {"Status": "Pending"}) in client.updated


def test_salesforce_failure_is_audited_and_leaves_batch_retryable(tmp_path):
    client = FakeClient()
    client.fail_update = ("Account", "account-1")
    feeder = Feeder(["apply automatically"])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    with pytest.raises(ProcessingError, match="write failed"):
        processor.review(
            [staged_row(revised_company_name="Acme Steel LLC")],
            tmp_path,
        )

    audit_text = (tmp_path / "review_audit.jsonl").read_text(encoding="utf-8")
    assert '"result": "failed"' in audit_text
    assert not any(item[0] == "Company_Profile_Change__c" for item in client.updated)
    assert ("Case", "case-1", {"Status": "Pending"}) in client.updated


def test_interruption_flushes_audit_and_leaves_batch_retryable(tmp_path):
    client = FakeClient()
    feeder = Feeder([KeyboardInterrupt()])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    with pytest.raises(ProcessingInterrupted):
        processor.review(
            [staged_row(revised_company_name="Acme Steel LLC")],
            tmp_path,
        )

    audit_text = (tmp_path / "review_audit.jsonl").read_text(encoding="utf-8")
    assert '"result": "interrupted"' in audit_text
    assert not any(item[0] == "Company_Profile_Change__c" for item in client.updated)
    assert ("Case", "case-1", {"Status": "Pending"}) in client.updated


def test_interruption_during_email_confirmation_is_also_retryable(tmp_path):
    client = FakeClient()
    feeder = Feeder(["apply automatically", KeyboardInterrupt()])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    with pytest.raises(ProcessingInterrupted):
        processor.review(
            [staged_row(revised_company_name="Acme Steel LLC")],
            tmp_path,
        )

    audit_text = (tmp_path / "review_audit.jsonl").read_text(encoding="utf-8")
    assert '"result": "interrupted"' in audit_text
    assert not any(item[0] == "Company_Profile_Change__c" for item in client.updated)
    assert ("Case", "case-1", {"Status": "Pending"}) in client.updated
