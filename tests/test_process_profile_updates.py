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
    read_staged_profile_updates,
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
    def __init__(self, answers, *, row_answers=None):
        self.answers = iter(answers)
        self.row_answers = iter(row_answers or [])
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if prompt.startswith("Continue with this staged row"):
            answer = next(self.row_answers, "")
            if isinstance(answer, BaseException):
                raise answer
            return answer
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
            ("Contact", "old-contact"): {
                "Id": "old-contact",
                "AccountId": "account-1",
                "FirstName": "Old",
                "LastName": "Contact",
                "Title": "",
                "Email": "old.contact@example.com",
                "Phone": "312-555-0000",
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
            if where and where.startswith("Email = '") and where.endswith("'"):
                email = where.removeprefix("Email = '").removesuffix("'")
                return [
                    dict(contact)
                    for contact in self.contacts
                    if contact.get("Email") == email
                ]
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
    output = []
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
        output_fn=output.append,
    )

    workflow.run(tmp_path)

    assert events == ["cases", "stage", "write", "review"]
    assert processor.rows[0]["account_name"] == "value read from disk"
    progress = [
        "Preparing Profile Update Cases",
        "Case preparation complete",
        "Staging Profile Updates",
        "Staging complete",
        "Publishing staging CSV",
        "Staging CSV published",
        "Validating published staging CSV",
        "Staging CSV validated",
        "Starting interactive review",
    ]
    positions = [
        next(index for index, message in enumerate(output) if text in message)
        for text in progress
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize(
    "missing_column",
    ["has_contact_derived_values", "has_no_update_content"],
)
def test_published_csv_requires_new_staging_metadata_columns(
    tmp_path, missing_column
):
    columns = [column for column in CSV_COLUMNS if column != missing_column]
    csv_path = tmp_path / "profile_updates.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()

    with pytest.raises(ProcessingError, match=missing_column):
        read_staged_profile_updates(csv_path)


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
        output_fn=lambda message: None,
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


@pytest.mark.parametrize("answer", ["", "c", "Continue"])
def test_each_staged_row_has_a_continue_checkpoint_and_heading(tmp_path, answer):
    client = FakeClient()
    feeder = Feeder([], row_answers=[answer, answer.swapcase()])
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=output.append,
        now=NOW,
    )
    rows = [
        staged_row(source_submission_names='["PU-100"]'),
        staged_row(source_submission_names='["PU-101"]'),
    ]

    result = processor.review(rows, tmp_path)

    checkpoint_prompts = [
        prompt
        for prompt in feeder.prompts
        if prompt.startswith("Continue with this staged row")
    ]
    assert len(checkpoint_prompts) == 2
    displayed = "\n".join(output)
    assert "Account: Acme Steel" in displayed
    assert "Submitter: Sam Submitter <sam@example.com>" in displayed
    assert "Profile Updates: PU-100" in displayed
    assert "Profile Updates: PU-101" in displayed
    assert result.stopped_early is False


@pytest.mark.parametrize(
    ("flags", "expected_notes"),
    [
        (
            {"has_contact_derived_values": "true"},
            [
                (
                    "Note: contact details were supplemented from available "
                    "contact information."
                )
            ],
        ),
        (
            {"has_no_update_content": "true"},
            ["Note: this combined profile update has no submitted update content."],
        ),
        (
            {
                "has_contact_derived_values": "true",
                "has_no_update_content": "true",
            },
            [
                (
                    "Note: contact details were supplemented from available "
                    "contact information."
                ),
                (
                    "Note: this combined profile update has no submitted "
                    "update content."
                ),
            ],
        ),
        ({}, []),
    ],
)
def test_staged_row_heading_shows_metadata_notes(tmp_path, flags, expected_notes):
    client = FakeClient()
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder([]),
        output_fn=output.append,
        now=NOW,
    )

    processor.review([staged_row(**flags)], tmp_path)

    displayed = "\n".join(output)
    known_notes = [
        "Note: contact details were supplemented from available contact information.",
        "Note: this combined profile update has no submitted update content.",
    ]
    for note in known_notes:
        assert (note in displayed) is (note in expected_notes)


@pytest.mark.parametrize("answer", ["q", "Quit"])
def test_quit_is_audited_preserves_completed_batches_and_returns_success(
    tmp_path, answer
):
    client = FakeClient()
    client.records.update(
        {
            ("Company_Profile_Change__c", "submission-2"): source_record(
                Id="submission-2",
                Name="PU-200",
                Account__c="account-2",
                CreatedDate="2026-07-16T14:30:00.000+0000",
            ),
            ("Account", "account-2"): account_record(
                Id="account-2",
                Name="Beta Steel",
                Cert_Certification_Contact__c="",
            ),
            ("Case", "case-2"): {
                "Id": "case-2",
                "CaseNumber": "00010002",
                "Status": "Pending",
            },
        }
    )
    feeder = Feeder([], row_answers=["", answer])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )
    rows = [
        staged_row(),
        staged_row(
            source_submission_ids='["submission-2"]',
            source_submission_names='["PU-200"]',
            earliest_submission_date="2026-07-16T14:30:00.000+0000",
            latest_submission_date="2026-07-16T14:30:00.000+0000",
            account_id="account-2",
            account_name="Beta Steel",
            case_id="case-2",
            case_number="00010002",
            revised_company_name="Beta Steel LLC",
        ),
    ]

    result = processor.review(rows, tmp_path)

    assert result.stopped_early is True
    assert result.completed_batches == 1
    assert result.pending_batches == 1
    assert (
        "Company_Profile_Change__c",
        "submission-1",
        {"Status__c": "Closed"},
    ) in client.updated
    assert ("Case", "case-1", {"Status": "Closed"}) in client.updated
    assert not any(
        object_name == "Company_Profile_Change__c" and record_id == "submission-2"
        for object_name, record_id, _ in client.updated
    )
    assert ("Case", "case-2", {"Status": "Pending"}) in client.updated
    assert (
        "Account",
        "account-2",
        {"Name": "Beta Steel LLC"},
    ) not in client.updated
    audit = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    stopped = next(item for item in audit if item["result"] == "stopped early")
    assert stopped["case_id"] == "case-2"
    assert stopped["action"] == "reviewer requested safe stop"
    assert result.response_path.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("shortcut", "expected_decision", "expected_status"),
    [
        ("a", "apply automatically", "applied"),
        ("M", "make manually", "verified manually"),
        ("n", "will not be made", "rejected"),
    ],
)
def test_decision_shortcuts_are_case_insensitive_but_audit_full_phrases(
    tmp_path,
    shortcut,
    expected_decision,
    expected_status,
):
    client = FakeClient()
    answers = [shortcut]
    if shortcut.casefold() == "m":
        client.get_sequences[("Account", "account-1")] = [
            account_record(),
            account_record(Name="Acme Steel LLC"),
        ]
        answers.extend(["", "yes"])
    elif shortcut.casefold() == "a":
        answers.append("yes")
    feeder = Feeder(answers)
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

    audit = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    decision = next(item for item in audit if item["field"] == "Name")
    assert decision["decision"] == expected_decision
    assert decision["result"] == expected_status


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

    assert not any(prompt.startswith("Decision [") for prompt in feeder.prompts)
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


def test_exact_email_match_is_global_and_each_mismatch_precedes_role_decision(
    tmp_path,
):
    contact = {
        "Id": "contact-1",
        "AccountId": "different-account",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Manager",
        "Email": "old@example.com",
        "Phone": "312-555-0100",
    }
    client = FakeClient(contacts=[contact])
    feeder = Feeder(
        [
            "apply automatically",
            "apply automatically",
            "apply automatically",
            "apply automatically",
            "will not be made",
            "yes",
        ]
    )
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=output.append,
        now=NOW,
    )
    row = staged_row(
        certification_first_name="Alexa",
        certification_last_name="Jones",
        certification_title="Director",
        certification_email="old@example.com",
        certification_phone="312-555-0199",
        certification_salesforce_contact_id="contact-1",
        certification_resolution_action="update_contact",
    )

    result = processor.review([row], tmp_path)

    contact_query = next(query for query in client.queries if query[0] == "Contact")
    assert contact_query[2] == "Email = 'old@example.com'"
    assert "AccountId" not in contact_query[2]
    assert "candidates" not in "\n".join(output).casefold()
    assert not any("Contact choice" in prompt for prompt in feeder.prompts)
    assert sum(prompt.startswith("Decision [") for prompt in feeder.prompts) == 5
    assert (
        "Contact",
        "contact-1",
        {"FirstName": "Alexa"},
    ) in client.updated
    assert ("Contact", "contact-1", {"LastName": "Jones"}) in client.updated
    assert ("Contact", "contact-1", {"Title": "Director"}) in client.updated
    assert ("Contact", "contact-1", {"Phone": "312-555-0199"}) in client.updated
    assert not any(
        item[0] == "Account" and "Cert_Certification_Contact__c" in item[2]
        for item in client.updated
    )
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        item["field"] == "FirstName" and item["result"] == "applied" for item in entries
    )
    assert any(
        item["field"] == "Cert_Certification_Contact__c"
        and item["result"] == "rejected"
        for item in entries
    )
    displayed = "\n".join(output)
    assert (
        "Current Certification Contact:\n"
        "Name: Alex Smith\n"
        "Title: Manager\n"
        "Email: old@example.com\n"
        "Phone: 312-555-0100"
    ) in displayed
    assert (
        "Certification Account Role\n"
        "Current Salesforce value: Old Contact <old.contact@example.com>\n"
        "Proposed value: Alexa Jones <old@example.com>"
    ) in displayed
    assert "contact-1" not in "\n".join(feeder.prompts)
    assert "old-contact" not in "\n".join(feeder.prompts)


def test_incomplete_contact_is_not_automatically_created(tmp_path):
    client = FakeClient()
    feeder = Feeder(["will not be made", "yes"])
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
            "apply automatically",
            "apply automatically",
            "yes",
        ]
    )
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
                certification_first_name="New",
                certification_last_name="Person",
                certification_email="new.person@example.com",
                certification_resolution_action="create_contact",
            )
        ],
        tmp_path,
    )

    contact_query = next(query for query in client.queries if query[0] == "Contact")
    assert contact_query[2] == "Email = 'new.person@example.com'"
    assert sum(prompt.startswith("Decision [") for prompt in feeder.prompts) == 2
    assert client.created == [
        (
            "Contact",
            {
                "AccountId": "account-1",
                "FirstName": "New",
                "LastName": "Person",
                "Email": "new.person@example.com",
            },
        )
    ]
    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "created-1"},
    ) in client.updated
    assert (
        "Submitted Certification Contact:\n"
        "Name: New Person\n"
        "Title: (blank)\n"
        "Email: new.person@example.com\n"
        "Phone: (blank)"
    ) in "\n".join(output)


def test_duplicate_exact_email_matches_are_audited_and_keep_case_retryable(tmp_path):
    contacts = [
        {
            "Id": "contact-1",
            "AccountId": "account-1",
            "FirstName": "Alex",
            "LastName": "Smith",
            "Title": "",
            "Email": "shared@example.com",
            "Phone": "",
        },
        {
            "Id": "contact-2",
            "AccountId": "different-account",
            "FirstName": "Alex",
            "LastName": "Jones",
            "Title": "",
            "Email": "shared@example.com",
            "Phone": "",
        },
    ]
    client = FakeClient(contacts=contacts)
    feeder = Feeder([])
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=output.append,
        now=NOW,
    )

    with pytest.raises(ProcessingError, match="Multiple Salesforce Contacts"):
        processor.review(
            [
                staged_row(
                    certification_first_name="Alex",
                    certification_last_name="Smith",
                    certification_email="shared@example.com",
                )
            ],
            tmp_path,
        )

    entries = [
        json.loads(line)
        for line in (tmp_path / "review_audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    failure = next(
        item for item in entries if item["action"] == "match Contact by exact email"
    )
    assert failure["field"] == "Email"
    assert failure["proposed_value"] == "shared@example.com"
    assert failure["result"] == "failed"
    assert "contact-1" in failure["original_value"]
    assert "contact-2" in failure["original_value"]
    assert not any(prompt.startswith("Decision [") for prompt in feeder.prompts)
    assert not any("Contact choice" in prompt for prompt in feeder.prompts)
    assert "candidates" not in "\n".join(output).casefold()
    assert not any(item[0] == "Company_Profile_Change__c" for item in client.updated)
    assert ("Case", "case-1", {"Status": "Pending"}) in client.updated


def test_case_context_and_history_are_loaded_once_for_reused_submission(tmp_path):
    history = [
        {
            "Id": "history-1",
            "AccountId": "account-1",
            "Field": "Name",
            "OldValue": "Old Acme",
            "NewValue": "Acme Steel",
            "CreatedDate": "2026-07-15T15:15:00.000+0000",
        }
    ]
    client = FakeClient(history=history)
    feeder = Feeder([])
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=output.append,
        now=NOW,
    )
    rows = [
        staged_row(
            key_answers="Certification contact changed: Yes",
            effective_date="2026-08-01",
            warnings="Review this carefully",
        ),
        staged_row(
            key_answers="Certification contact changed: Yes",
            effective_date="2026-08-01",
            warnings="Review this carefully",
        ),
    ]

    processor.review(rows, tmp_path)

    displayed = "\n".join(output)
    assert displayed.count("Fresh comment") == 1
    assert displayed.count("Fresh personnel note") == 1
    assert displayed.count("Certification contact changed: Yes") == 1
    assert displayed.count("Effective date: 2026-08-01") == 1
    assert displayed.count("Review this carefully") == 1
    assert displayed.count("Account History:") == 1
    assert (
        sum(
            object_name == "Company_Profile_Change__c"
            for object_name, _, _ in client.gets
        )
        == 1
    )
    assert sum(query[0] == "AccountHistory" for query in client.queries) == 1


def test_role_response_is_consolidated_and_marks_unchanged_roles(tmp_path):
    contacts = [
        {
            "Id": "contact-1",
            "AccountId": "account-1",
            "FirstName": "Alex",
            "LastName": "Smith",
            "Title": "Safety Director",
            "Email": "alex@example.com",
            "Phone": "312-555-0100",
        },
        {
            "Id": "contact-2",
            "AccountId": "account-1",
            "FirstName": "Pat",
            "LastName": "Jones",
            "Title": "President",
            "Email": "pat@example.com",
            "Phone": "312-555-0200",
        },
    ]
    client = FakeClient(
        account=account_record(
            Cert_Certification_Contact__c="contact-1",
            Cert_Principal_Contact__c="contact-2",
        ),
        contacts=contacts,
    )
    feeder = Feeder(["apply automatically", "yes"])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )

    result = processor.review(
        [
            staged_row(
                certification_first_name="Alex",
                certification_last_name="Smith",
                certification_title="Safety Director",
                certification_email="alex@example.com",
                certification_phone="312-555-0199",
                principal_first_name="Pat",
                principal_last_name="Jones",
                principal_title="President",
                principal_email="pat@example.com",
                principal_phone="312-555-0200",
            )
        ],
        tmp_path,
    )

    response = result.response_path.read_text(encoding="utf-8")
    assert response.count("Certification Contact:") == 1
    assert (
        "Certification Contact: Alex Smith, Safety Director, "
        "alex@example.com, 312-555-0199"
    ) in response
    assert (
        "Replaces Alex Smith, Safety Director, alex@example.com, 312-555-0100"
    ) in response
    assert (
        "Principal Contact: Pat Jones, President, "
        "pat@example.com, 312-555-0200 - no change"
    ) in response
    assert response.count("Replaces ") == 1
    assert "Certification Contact Phone:" not in response
    assert "Cert_Certification_Contact__c" not in response


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
