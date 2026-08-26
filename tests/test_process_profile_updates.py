import csv
import json
import re
from datetime import UTC, datetime

import pytest

import aisc_salesforce.process_profile_updates as profile_update_processing
from aisc_salesforce.contact_resolution import (
    ContactResolution,
    ContactResolutionClassification,
    ContactSource,
)
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
    load_staging_session,
    publish_staging_session,
    read_staged_profile_updates,
)
from aisc_salesforce.profile_updates import AutomationCounts
from aisc_salesforce.review_queue import (
    QueueStatus,
    ReviewQueueStore,
    build_review_queue,
    iter_changes,
    read_review_queue,
    write_review_queue,
)
from aisc_salesforce.review_ui import (
    ChoiceAnswer,
    ChoiceQuestion,
    FreeTextAnswer,
    FreeTextQuestion,
)
from aisc_salesforce.salesforce import SalesforceError
from aisc_salesforce.stage_profile_updates import CSV_COLUMNS, StagingResult

NOW = datetime(2026, 7, 17, 18, 0, tzinfo=UTC)


def test_review_helpers_have_docstrings():
    routing = profile_update_processing._ParentRouting
    assert routing.is_parent.__doc__
    assert routing.blocked.__doc__
    assert profile_update_processing._AuditWriter.append.__doc__
    assert profile_update_processing._ResponseWriter.append.__doc__

    class Client:
        def create_record(self):
            return None

    client = profile_update_processing._QueuePublishingClient(Client(), lambda: None)
    mutation = client.__getattr__("create_record")
    assert mutation.__doc__


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


def staged_resolution(
    *,
    email="sam@example.com",
    sources=None,
    submitted=None,
    classification=ContactResolutionClassification.CREATE_NEW,
    candidates=None,
    selected=None,
):
    normalized = email.strip().casefold()
    local, domain = normalized.rsplit("@", 1)
    resolution = ContactResolution(
        classification,
        normalized,
        f"{local.replace('.', '')}@{domain}",
        sources=list(sources or []),
        candidates=list(candidates or []),
        selected_contact=selected,
        reason="test resolution",
        confidence="test",
        submitted=dict(submitted or {}),
    )
    return json.dumps([resolution.as_dict()])


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
    def __init__(
        self,
        *,
        source=None,
        account=None,
        children=None,
        contacts=None,
        history=None,
    ):
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
        self.children = children or []
        for child in self.children:
            self.records[("Account", child["Id"])] = child
        for contact in self.contacts:
            self.records[("Contact", contact["Id"])] = contact
        self.history = history or []
        self.get_sequences = {}
        self.queries = []
        self.gets = []
        self.created = []
        self.updated = []
        self.fail_update = None
        self.fail_create = None

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
        if object_name == "Account":
            return [
                dict(item)
                for item in self.children
                if item.get("ParentId") == "account-1"
            ]
        raise AssertionError(object_name)

    def get_record(self, object_name, record_id, fields):
        self.gets.append((object_name, record_id, tuple(fields)))
        key = (object_name, record_id)
        sequence = self.get_sequences.get(key)
        if sequence:
            return dict(sequence.pop(0))
        return dict(self.records[key])

    def create_record(self, object_name, values):
        if self.fail_create is not None:
            error = self.fail_create
            self.fail_create = None
            raise error
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


class ResolvingProcessor(CapturingProcessor):
    def resolve_missing_submission_accounts(self):
        self.events.append("resolve_accounts")
        return 1


def test_queue_aware_workflow_publishes_preflight_before_setup_and_refreshes(
    tmp_path,
):
    events = []
    run_folder = tmp_path / "published"

    class SequencedStaging:
        def __init__(self):
            self.calls = 0

        def stage(self):
            self.calls += 1
            events.append(f"stage-{self.calls}")
            if self.calls == 1:
                return StagingResult(
                    [
                        staged_row(
                            account_id="",
                            case_id="",
                            case_number="",
                            case_match_status="missing",
                        )
                    ],
                    1,
                )
            return StagingResult([staged_row(account_name="refreshed")], 0)

    class QueueAwareProcessor:
        def prepare_review_queue(self, rows, artifact_dir):
            events.append("queue")
            assert not any(event in events for event in ("resolve", "cases"))
            write_review_queue(
                build_review_queue(rows, now=NOW),
                artifact_dir / "review_queue.json",
            )

        def transition_setup(self, *args, **kwargs):
            events.append(f"transition-{args[0]}-{args[2]}")

        def resolve_missing_submission_accounts(self):
            assert (run_folder / "review_queue.json").exists()
            events.append("resolve")
            return 1

        def refresh_review_queue(self, rows):
            events.append("refresh")
            assert rows[0]["account_name"] == "refreshed"

        def review(self, rows, artifact_dir):
            events.append("review")
            return artifact_dir

    class QueueAwareCaseService:
        errors = []

        def run(self):
            assert (run_folder / "review_queue.json").exists()
            events.append("cases")
            return AutomationCounts(created=1)

    def writer(rows, output_dir):
        events.append("write")
        run_folder.mkdir()
        with (run_folder / "profile_updates.csv").open(
            "w", newline="", encoding="utf-8"
        ) as output:
            csv_writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
            csv_writer.writeheader()
            csv_writer.writerows(rows)
        return run_folder

    workflow = ProfileUpdateProcessingWorkflow(
        QueueAwareCaseService(),
        SequencedStaging(),
        QueueAwareProcessor(),
        staging_writer=writer,
        output_fn=lambda message: None,
    )

    assert workflow.run(tmp_path) == run_folder
    assert events.index("queue") < events.index("resolve") < events.index("cases")
    assert events.index("cases") < events.index("stage-2") < events.index("review")


def test_session_publication_writes_csv_and_queue_under_one_generated_id(tmp_path):
    result = publish_staging_session([staged_row()], tmp_path, now=NOW)
    collision = publish_staging_session([staged_row()], tmp_path, now=NOW)

    assert result.session_id == "2026-07-17T18-00-00Z"
    assert collision.session_id == "2026-07-17T18-00-00Z-01"
    assert result.path == tmp_path / result.session_id
    assert result.csv_path.is_file()
    assert result.queue_path.is_file()
    loaded, rows, manifest = load_staging_session(tmp_path, result.session_id)
    assert loaded == result
    assert rows == [staged_row()]
    assert manifest.batches


def test_session_publication_retries_when_publication_collides(tmp_path, monkeypatch):
    real_rename = profile_update_processing.os.rename
    base_path = tmp_path / "2026-07-17T18-00-00Z"

    def collide_before_first_publication(source, target):
        if target == base_path and not target.exists():
            base_path.mkdir()
            (base_path / "published-by-other-process").write_text("claimed")
        real_rename(source, target)

    monkeypatch.setattr(
        profile_update_processing.os, "rename", collide_before_first_publication
    )

    result = publish_staging_session([staged_row()], tmp_path, now=NOW)

    assert result.session_id == "2026-07-17T18-00-00Z-01"
    assert (base_path / "published-by-other-process").read_text() == "claimed"
    assert result.csv_path.is_file()
    assert result.queue_path.is_file()
    assert not list(tmp_path.glob(".*.tmp"))


def test_session_publication_syncs_before_and_after_directory_rename(
    tmp_path, monkeypatch
):
    events = []
    real_rename = profile_update_processing.os.rename

    def recording_sync(path):
        events.append(("sync", path, path.exists()))

    def recording_rename(source, target):
        if source.is_dir():
            events.append(("rename", source, target))
        real_rename(source, target)

    monkeypatch.setattr(profile_update_processing, "sync_directory", recording_sync)
    monkeypatch.setattr(profile_update_processing.os, "rename", recording_rename)

    result = publish_staging_session([staged_row()], tmp_path, now=NOW)

    temporary_sync, rename, output_sync = events
    assert temporary_sync == ("sync", rename[1], True)
    assert rename == ("rename", rename[1], result.path)
    assert output_sync == ("sync", tmp_path, True)


def test_session_publication_exposes_nothing_when_queue_write_fails(
    tmp_path, monkeypatch
):
    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        "aisc_salesforce.process_profile_updates.write_review_queue", fail
    )

    with pytest.raises(OSError, match="disk full"):
        publish_staging_session([staged_row()], tmp_path, now=NOW)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "session_id",
    [
        "../outside",
        "/absolute",
        "nested/session",
        "..",
        "not-a-session",
        "2026-99-04T15-30-00Z",
    ],
)
def test_session_loader_rejects_unsafe_or_malformed_ids(tmp_path, session_id):
    with pytest.raises(ProcessingError, match="Invalid staging session ID"):
        load_staging_session(tmp_path, session_id)


def test_session_loader_rejects_csv_queue_submission_mismatch(tmp_path):
    result = publish_staging_session([staged_row()], tmp_path, now=NOW)
    rows = [staged_row(source_submission_ids='["different"]')]
    with result.csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(ProcessingError, match="different submission IDs"):
        load_staging_session(tmp_path, result.session_id)


def test_completed_session_review_is_a_noop(tmp_path):
    session = publish_staging_session([staged_row()], tmp_path, now=NOW)
    store = ReviewQueueStore(session.queue_path, read_review_queue(session.queue_path))
    for change in tuple(iter_changes(store.manifest)):
        store.transition(change.id, QueueStatus.COMPLETED, outcome="applied")
    (session.path / "review_audit.jsonl").write_text(
        json.dumps(
            {
                "target_object": "Company_Profile_Change__c",
                "target_record_id": "submission-1",
                "field": "Status__c",
                "proposed_value": "Closed",
                "result": "applied",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    output = []

    class MustNotRun:
        def run(self, *args):
            raise AssertionError("completed Case preparation ran again")

        def stage(self, *args):
            raise AssertionError("completed session was refreshed")

    processor = InteractiveProfileUpdateProcessor(
        FakeClient(), input_fn=Feeder([]), output_fn=output.append, now=NOW
    )
    workflow = ProfileUpdateProcessingWorkflow(
        MustNotRun(), MustNotRun(), processor, output_fn=output.append
    )

    result = workflow.review(session.session_id, tmp_path)

    assert result.completed_batches == 1
    assert result.pending_batches == 0
    assert any("already complete" in message for message in output)


def test_review_skips_case_setup_completed_by_prepare(tmp_path):
    row = staged_row()
    session = publish_staging_session([row], tmp_path, now=NOW)
    calls = []

    class SessionCaseService:
        errors = []

        def run(self, submission_ids):
            calls.append(set(submission_ids))
            return AutomationCounts(reused=1)

    class SessionStagingService:
        def stage(self, submission_ids):
            assert set(submission_ids) == {"submission-1"}
            return StagingResult([row], 0)

    processor = InteractiveProfileUpdateProcessor(
        FakeClient(),
        input_fn=Feeder([], row_answers=["q"]),
        output_fn=lambda message: None,
        now=NOW,
    )
    workflow = ProfileUpdateProcessingWorkflow(
        SessionCaseService(), SessionStagingService(), processor
    )

    workflow.prepare(session.session_id, tmp_path)
    result = workflow.review(session.session_id, tmp_path)

    assert calls == [{"submission-1"}]
    assert result.stopped_early is True


def test_review_prepares_case_for_submission_after_account_repair(
    tmp_path, monkeypatch
):
    blank_account_row = staged_row(
        account_id="",
        account_name="",
        case_id="",
        case_number="",
        case_status="",
        case_match_status="missing",
    )
    repaired_row = staged_row(
        case_id="",
        case_number="",
        case_status="",
        case_match_status="missing",
    )
    session = publish_staging_session([blank_account_row], tmp_path, now=NOW)
    events = []
    case_calls = []

    class RepairingProcessor:
        now = NOW

        def load_review_queue(self, rows, artifact_dir, *, resume=False):
            return read_review_queue(artifact_dir / "review_queue.json")

        def transition_setup(self, object_name, field, status, **kwargs):
            if object_name == "Case" and status is QueueStatus.IN_PROGRESS:
                events.append("case_transition")

        def resolve_missing_submission_accounts(self, submission_ids):
            assert set(submission_ids) == {"submission-1"}
            events.append("repair_account")
            return 1

        def refresh_review_queue(self, rows):
            events.append("refresh_queue")

        def review(self, rows, artifact_dir):
            events.append("review")
            return "reviewed"

    class RefreshingStagingService:
        def stage(self, submission_ids):
            assert set(submission_ids) == {"submission-1"}
            event = (
                "verify_account" if "verify_account" not in events else "final_refresh"
            )
            events.append(event)
            return StagingResult([repaired_row], 0)

    class CapturingCaseService:
        errors = []

        def run(self, submission_ids):
            events.append("prepare_case")
            case_calls.append(set(submission_ids))
            return AutomationCounts(created=1)

    def unfinished_ids_after_account_verification(manifest, object_name, field):
        assert "verify_account" in events
        assert object_name == field == "Case"
        events.append("find_unfinished_cases")
        return set()

    monkeypatch.setattr(
        profile_update_processing,
        "_unfinished_setup_submission_ids",
        unfinished_ids_after_account_verification,
    )
    workflow = ProfileUpdateProcessingWorkflow(
        CapturingCaseService(),
        RefreshingStagingService(),
        RepairingProcessor(),
        output_fn=lambda message: None,
    )

    result = workflow.review(session.session_id, tmp_path)

    assert result == "reviewed"
    assert case_calls == [{"submission-1"}]
    assert events == [
        "repair_account",
        "verify_account",
        "find_unfinished_cases",
        "case_transition",
        "prepare_case",
        "final_refresh",
        "refresh_queue",
        "review",
    ]


def test_workflow_repairs_submission_accounts_before_creating_cases(tmp_path):
    events = []
    processor = ResolvingProcessor(events)

    def writer(rows, output_dir):
        events.append("write")
        folder = output_dir / "published"
        folder.mkdir()
        with (folder / "profile_updates.csv").open(
            "w", newline="", encoding="utf-8"
        ) as output:
            csv.DictWriter(output, fieldnames=CSV_COLUMNS).writeheader()
            csv.DictWriter(output, fieldnames=CSV_COLUMNS).writerow(rows[0])
        return folder

    workflow = ProfileUpdateProcessingWorkflow(
        CaseService(events),
        StagingService(events),
        processor,
        staging_writer=writer,
        output_fn=lambda message: None,
    )

    workflow.run(tmp_path)

    assert events == ["resolve_accounts", "cases", "stage", "write", "review"]


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
def test_published_csv_requires_new_staging_metadata_columns(tmp_path, missing_column):
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


def test_key_update_exactly_seven_days_old_is_not_in_the_priority_group():
    rows = [
        staged_row(
            earliest_submission_date="2026-07-01T12:00:00+00:00",
            case_id="case-old-ordinary",
        ),
        staged_row(
            earliest_submission_date="2026-07-16T12:00:00+00:00",
            earliest_key_update_date="2026-07-10T18:00:00+00:00",
            has_key_updates="true",
            case_id="case-exactly-seven-days",
        ),
    ]

    batches = build_case_batches(rows, now=NOW)

    assert [batch.case_id for batch in batches] == [
        "case-old-ordinary",
        "case-exactly-seven-days",
    ]


class AccountResolutionUI:
    def __init__(self, *, entered_certification_ids=()):
        self.entered_certification_ids = iter(entered_certification_ids)
        self.events = []
        self.questions = []

    def display(self, event):
        self.events.append(event)

    def ask(self, question):
        self.questions.append(question)
        if isinstance(question, FreeTextQuestion):
            return FreeTextAnswer(next(self.entered_certification_ids))
        if isinstance(question, ChoiceQuestion):
            return ChoiceAnswer(question.choices[0])
        raise AssertionError(type(question))


class AccountResolutionClient:
    def __init__(self, *, certification_id="C-100", accounts_by_certification_id=None):
        self.certification_id = certification_id
        self.accounts_by_certification_id = accounts_by_certification_id
        self.queries = []
        self.updated = []

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        self.queries.append((object_name, fields, where, order_by))
        if object_name == "Company_Profile_Change__c":
            return [
                {
                    "Id": "submission-1",
                    "Name": "PU-100",
                    "CreatedDate": "2026-07-15T14:30:00.000+0000",
                    "Account__c": None,
                    "Certification_ID__c": self.certification_id,
                }
            ]
        if object_name == "Account":
            if self.accounts_by_certification_id is not None:
                certification_ids = re.findall(r"'([^']*)'", where)
                return [
                    account
                    for certification_id in certification_ids
                    for account in self.accounts_by_certification_id.get(
                        certification_id, []
                    )
                ]
            return [
                {
                    "Id": "account-1",
                    "Name": "Acme Steel",
                    "Certification_ID__c": self.certification_id or "C-200",
                },
                {
                    "Id": "account-2",
                    "Name": "Beta Steel",
                    "Certification_ID__c": self.certification_id or "C-200",
                },
            ]
        raise AssertionError(object_name)

    def update_record(self, object_name, record_id, values):
        self.updated.append((object_name, record_id, values))


def test_missing_submission_account_unique_certification_id_is_assigned_automatically():
    client = AccountResolutionClient(
        accounts_by_certification_id={
            "C-100": [
                {"Id": "account-1", "Name": "Acme Steel", "Certification_ID__c": "C-100"}
            ]
        }
    )
    ui = AccountResolutionUI()
    processor = InteractiveProfileUpdateProcessor(client, ui, now=NOW)

    repaired = processor.resolve_missing_submission_accounts()

    assert repaired == 1
    assert client.updated == [
        (
            "Company_Profile_Change__c",
            "submission-1",
            {"Account__c": "account-1"},
        )
    ]
    account_query = next(query for query in client.queries if query[0] == "Account")
    assert account_query[2] == "Certification_ID__c = 'C-100'"
    assert account_query[3] == "Name ASC, Id ASC"
    assert ui.questions == []
    assignment = "".join(
        fragment.value if hasattr(fragment, "value") else fragment.text
        for fragment in ui.events[0].message
    )
    assert assignment == "Assigned Profile Update PU-100 to Acme Steel."


def test_multiple_submission_account_matches_offer_numbered_certification_id_choices():
    client = AccountResolutionClient()
    ui = AccountResolutionUI()
    processor = InteractiveProfileUpdateProcessor(client, ui, now=NOW)

    processor.resolve_missing_submission_accounts()

    question = next(question for question in ui.questions if isinstance(question, ChoiceQuestion))
    assert [choice.key for choice in question.choices] == ["1", "2", "different_certification_id"]
    assert [choice.label for choice in question.choices[:2]] == [
        "Acme Steel (Certification ID C-100)",
        "Beta Steel (Certification ID C-100)",
    ]


def test_submission_account_lookup_zero_pads_certification_id_sections():
    client = AccountResolutionClient(
        certification_id="2015-9-11-3893F",
        accounts_by_certification_id={
            "2015-09-11-003893F": [
                {
                    "Id": "account-1",
                    "Name": "Acme Steel",
                    "Certification_ID__c": "2015-09-11-003893F",
                }
            ]
        },
    )
    ui = AccountResolutionUI()
    processor = InteractiveProfileUpdateProcessor(client, ui, now=NOW)

    processor.resolve_missing_submission_accounts()

    account_query = next(query for query in client.queries if query[0] == "Account")
    assert account_query[2] == (
        "Certification_ID__c IN ('2015-9-11-3893F', '2015-09-11-003893F')"
    )
    assert client.updated[0][2] == {"Account__c": "account-1"}
    assert ui.questions == []


def test_certification_id_lookup_candidates_include_known_o_suffix_replacements():
    assert profile_update_processing._certification_id_lookup_candidates(
        "2015-9-11-3893O"
    ) == (
        "2015-9-11-3893O",
        "2015-09-11-003893O",
        "2015-09-11-003893F",
        "2015-09-11-003893E",
        "2015-09-11-003893P",
    )


def test_missing_submission_certification_id_requests_and_validates_certification_id():
    client = AccountResolutionClient(
        certification_id="",
        accounts_by_certification_id={
            "C-200": [
                {"Id": "account-2", "Name": "Beta Steel", "Certification_ID__c": "C-200"}
            ]
        },
    )
    ui = AccountResolutionUI(entered_certification_ids=("", "C-200"))
    processor = InteractiveProfileUpdateProcessor(client, ui, now=NOW)

    processor.resolve_missing_submission_accounts()

    account_query = next(query for query in client.queries if query[0] == "Account")
    assert account_query[2] == "Certification_ID__c = 'C-200'"
    assert len(ui.questions) == 2
    assert all(isinstance(question, FreeTextQuestion) for question in ui.questions)
    prompt = "".join(fragment.text for fragment in ui.questions[0].prompt if hasattr(fragment, "text"))
    assert "Certification ID" in prompt
    feedback = "".join(fragment.text for fragment in ui.events[0].message if hasattr(fragment, "text"))
    assert feedback == "Certification ID cannot be blank."


def test_unmatched_certification_id_reports_and_allows_retry():
    client = AccountResolutionClient(
        certification_id="C-missing",
        accounts_by_certification_id={
            "C-missing": [],
            "C-200": [
                {"Id": "account-2", "Name": "Beta Steel", "Certification_ID__c": "C-200"}
            ],
        },
    )
    ui = AccountResolutionUI(entered_certification_ids=("C-200",))
    processor = InteractiveProfileUpdateProcessor(client, ui, now=NOW)

    processor.resolve_missing_submission_accounts()

    assert client.updated[0][2] == {"Account__c": "account-2"}
    feedback = "".join(fragment.value if hasattr(fragment, "value") else fragment.text for fragment in ui.events[0].message)
    assert feedback == "No Account was found for Certification ID C-missing."
    prompt = "".join(fragment.text for fragment in ui.questions[0].prompt if hasattr(fragment, "text"))
    assert "to find the Salesforce Account" in prompt


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
                ("Note: this combined profile update has no submitted update content."),
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
    queue = json.loads(result.queue_path.read_text(encoding="utf-8"))
    stopped_change = next(
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] == "Name"
    )
    assert stopped_change["status"] == "stopped_early"


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
            account_record(),
            account_record(),
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
    assert result.queue_path == tmp_path / "review_queue.json"
    queue = json.loads(result.queue_path.read_text(encoding="utf-8"))
    account_change = next(
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] == "Name"
    )
    assert account_change["status"] == "completed"
    assert account_change["outcome"] == "applied"
    assert queue["default_next_item_id"] is None
    assert any(item[0] == "Company_Profile_Change__c" for item in client.gets)
    history_query = next(
        query for query in client.queries if query[0] == "AccountHistory"
    )
    assert "CreatedDate >=" in history_query[2]
    assert "CreatedDate <" in history_query[2]


def test_parent_routes_account_updates_to_active_direct_children_only(tmp_path):
    children = [
        account_record(
            Id="child-1",
            Name="Shared Old Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Certified",
        ),
        account_record(
            Id="child-2",
            Name=" Shared Old Name ",
            ParentId="account-1",
            Cert_Certification_Status__c="Initials",
        ),
        account_record(
            Id="child-dropped",
            Name="Different Dropped Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Dropped",
        ),
        account_record(
            Id="grandchild",
            Name="Different Grandchild Name",
            ParentId="child-1",
            Cert_Certification_Status__c="Certified",
        ),
    ]
    client = FakeClient(
        source=source_record(Revised_Company_Name__c="New Shared Name"),
        children=children,
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["a", "a", "yes"], row_answers=[""]),
        output_fn=lambda message: None,
        now=NOW,
    )
    affected = json.dumps(
        [
            {
                "id": "child-1",
                "name": "Shared Old Name",
                "certification_status": "Certified",
            },
            {
                "id": "child-2",
                "name": "Shared Old Name",
                "certification_status": "Initials",
            },
        ]
    )

    result = processor.review(
        [
            staged_row(
                is_parent_account="true",
                affected_accounts=affected,
                revised_company_name="New Shared Name",
            )
        ],
        tmp_path,
    )

    assert ("Account", "child-1", {"Name": "New Shared Name"}) in client.updated
    assert ("Account", "child-2", {"Name": "New Shared Name"}) in client.updated
    assert not any(
        object_name == "Account"
        and record_id in {"account-1", "child-dropped", "grandchild"}
        and "Name" in values
        for object_name, record_id, values in client.updated
    )
    queue = json.loads(result.queue_path.read_text(encoding="utf-8"))
    name_changes = [
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] == "Name"
    ]
    assert {change["salesforce"]["record_id"] for change in name_changes} == {
        "child-1",
        "child-2",
    }
    assert {change["outcome"] for change in name_changes} == {"applied"}


def test_parent_conflict_is_acknowledged_and_blocks_whole_case_without_writes(
    tmp_path,
):
    children = [
        account_record(
            Id="child-1",
            Name="First Current Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Certified",
        ),
        account_record(
            Id="child-2",
            Name="Second Current Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Initials",
        ),
        account_record(
            Id="child-dropped",
            Name="Dropped Current Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Dropped",
        ),
    ]
    client = FakeClient(
        source=source_record(Revised_Company_Name__c="Requested Name"),
        children=children,
    )
    output = []
    feeder = Feeder([""])
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=output.append,
        now=NOW,
    )
    affected = json.dumps(
        [
            {
                "id": child["Id"],
                "name": child["Name"],
                "certification_status": child["Cert_Certification_Status__c"],
            }
            for child in children[:2]
        ]
    )

    result = processor.review(
        [
            staged_row(
                is_parent_account="true",
                affected_accounts=affected,
                revised_company_name="Requested Name",
            )
        ],
        tmp_path,
    )

    rendered = "\n".join(output)
    assert "Requested value: Requested Name" in rendered
    assert "First Child" not in rendered
    assert "First Current Name" in rendered
    assert "Second Current Name" in rendered
    assert "Dropped Current Name" not in rendered
    assert any("manual follow-up" in prompt.casefold() for prompt in feeder.prompts)
    assert client.updated == []
    assert client.created == []
    assert (
        client.records[("Company_Profile_Change__c", "submission-1")]["Status__c"]
        == "New"
    )
    assert client.records[("Case", "case-1")]["Status"] == "Pending"
    audit = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(entry["result"] == "deferred manual follow-up" for entry in audit)
    queue = json.loads(result.queue_path.read_text(encoding="utf-8"))
    assert queue["batches"][0]["status"] == "blocked"
    assert all(
        change["status"] in {"blocked", "completed"}
        for row in queue["batches"][0]["rows"]
        for change in row["changes"]
    )


def test_parent_with_no_active_children_is_acknowledged_without_salesforce_writes(
    tmp_path,
):
    client = FakeClient(
        source=source_record(Revised_Company_Name__c="Requested Name"),
        children=[
            account_record(
                Id="child-dropped",
                Name="Dropped Child",
                ParentId="account-1",
                Cert_Certification_Status__c="Dropped",
            ),
            account_record(
                Id="child-suspended",
                Name="Suspended Child",
                ParentId="account-1",
                Cert_Certification_Status__c="Suspended",
            ),
        ],
    )
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder([""]),
        output_fn=output.append,
        now=NOW,
    )

    result = processor.review(
        [
            staged_row(
                is_parent_account="true",
                affected_accounts="[]",
                revised_company_name="Requested Name",
            )
        ],
        tmp_path,
    )

    rendered = "\n".join(output)
    assert "no direct child with status Certified or Initials" in rendered
    assert "Dropped Child" in rendered
    assert "Suspended Child" in rendered
    assert client.updated == []
    assert client.created == []
    assert result.pending_batches == 1


def test_parent_preflight_compares_only_submitted_fields_and_normalizes_display_values():
    children = [
        account_record(
            Id="child-1",
            Name="Different One",
            Company_Owner__c=" Shared Owner ",
            ParentId="account-1",
            Cert_Certification_Status__c="Certified",
            Cert_Certification_Contact__c=None,
        ),
        account_record(
            Id="child-2",
            Name="Different Two",
            Company_Owner__c="Shared Owner",
            ParentId="account-1",
            Cert_Certification_Status__c="Initials",
            Cert_Certification_Contact__c=" ",
        ),
        account_record(
            Id="child-dropped",
            Name="Different Dropped",
            Company_Owner__c="Other Owner",
            ParentId="account-1",
            Cert_Certification_Status__c="Dropped",
            Cert_Certification_Contact__c="other-contact",
        ),
    ]
    source = source_record(
        Revised_Company_Owner__c="Requested Owner",
        Cert_Email__c="cert@example.com",
    )
    row = staged_row(
        is_parent_account="true",
        affected_accounts="[]",
        revised_company_owner="Requested Owner",
        certification_email="cert@example.com",
        certification_resolution_action="create_contact",
    )
    processor = InteractiveProfileUpdateProcessor(
        FakeClient(source=source, children=children),
        input_fn=Feeder([]),
        output_fn=lambda message: None,
        now=NOW,
    )
    batch = build_case_batches([row], now=NOW)[0]

    routing = processor._preflight_parent_routing(batch, {"submission-1": source})

    assert routing.is_parent is True
    assert routing.blocked is False
    assert routing.conflicts == ()
    assert {child["Id"] for child in routing.target_accounts} == {
        "child-1",
        "child-2",
    }


def test_parent_role_lookup_conflict_blocks_before_contact_or_case_writes(tmp_path):
    children = [
        account_record(
            Id="child-1",
            ParentId="account-1",
            Cert_Certification_Status__c="Certified",
            Cert_Certification_Contact__c="contact-1",
        ),
        account_record(
            Id="child-2",
            ParentId="account-1",
            Cert_Certification_Status__c="Initials",
            Cert_Certification_Contact__c="contact-2",
        ),
    ]
    client = FakeClient(
        source=source_record(Cert_Email__c="cert@example.com"),
        children=children,
    )
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder([""]),
        output_fn=output.append,
        now=NOW,
    )

    processor.review(
        [
            staged_row(
                is_parent_account="true",
                affected_accounts=json.dumps(
                    [
                        {
                            "id": child["Id"],
                            "name": child["Name"],
                            "certification_status": child[
                                "Cert_Certification_Status__c"
                            ],
                        }
                        for child in children
                    ]
                ),
                certification_email="cert@example.com",
                certification_salesforce_contact_id="requested-contact",
                certification_resolution_action="use_existing",
            )
        ],
        tmp_path,
    )

    rendered = "\n".join(output)
    assert "Certification Account Role" in rendered
    assert "Requested value: requested-contact" in rendered
    assert "contact-1" in rendered
    assert "contact-2" in rendered
    assert client.updated == []
    assert client.created == []


def test_blocked_parent_batch_advances_to_the_next_case(tmp_path):
    children = [
        account_record(
            Id="child-1",
            Name="First Current Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Certified",
        ),
        account_record(
            Id="child-2",
            Name="Second Current Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Initials",
        ),
    ]
    client = FakeClient(
        source=source_record(Revised_Company_Name__c="Parent Requested Name"),
        children=children,
    )
    client.records.update(
        {
            ("Company_Profile_Change__c", "submission-2"): source_record(
                Id="submission-2",
                Name="PU-200",
                Account__c="account-2",
                CreatedDate="2026-07-16T14:30:00.000+0000",
                Revised_Company_Name__c="Ordinary New Name",
            ),
            ("Account", "account-2"): account_record(
                Id="account-2", Name="Ordinary Old Name"
            ),
            ("Case", "case-2"): {
                "Id": "case-2",
                "CaseNumber": "00010002",
                "Status": "Pending",
            },
        }
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["", "a", "yes"], row_answers=[""]),
        output_fn=lambda message: None,
        now=NOW,
    )
    rows = [
        staged_row(
            is_parent_account="true",
            affected_accounts=json.dumps(
                [
                    {
                        "id": child["Id"],
                        "name": child["Name"],
                        "certification_status": child["Cert_Certification_Status__c"],
                    }
                    for child in children
                ]
            ),
            revised_company_name="Parent Requested Name",
        ),
        staged_row(
            source_submission_ids='["submission-2"]',
            source_submission_names='["PU-200"]',
            earliest_submission_date="2026-07-16T14:30:00.000+0000",
            latest_submission_date="2026-07-16T14:30:00.000+0000",
            account_id="account-2",
            account_name="Ordinary Old Name",
            case_id="case-2",
            case_number="00010002",
            revised_company_name="Ordinary New Name",
        ),
    ]

    result = processor.review(rows, tmp_path)

    assert ("Account", "account-2", {"Name": "Ordinary New Name"}) in client.updated
    assert not any(
        object_name == "Account"
        and record_id in {"account-1", "child-1", "child-2"}
        and "Name" in values
        for object_name, record_id, values in client.updated
    )
    assert result.completed_batches == 2
    assert result.pending_batches == 1


def test_parent_acknowledgement_interruption_writes_nothing_and_retry_refetches(
    tmp_path,
):
    children = [
        account_record(
            Id="child-1",
            Name="First Current Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Certified",
        ),
        account_record(
            Id="child-2",
            Name="Second Current Name",
            ParentId="account-1",
            Cert_Certification_Status__c="Initials",
        ),
    ]
    client = FakeClient(
        source=source_record(Revised_Company_Name__c="Requested Name"),
        children=children,
    )
    row = staged_row(
        is_parent_account="true",
        affected_accounts="[]",
        revised_company_name="Requested Name",
    )
    interrupted = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder([KeyboardInterrupt()]),
        output_fn=lambda message: None,
        now=NOW,
    )

    with pytest.raises(ProcessingInterrupted, match="parent preflight"):
        interrupted.review([dict(row)], tmp_path / "interrupted")

    assert client.updated == []
    assert client.created == []
    interrupted_audit = (tmp_path / "interrupted" / "review_audit.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"result": "interrupted"' in interrupted_audit

    client.children[1]["Name"] = "First Current Name"
    retry = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["a", "a", "yes"], row_answers=[""]),
        output_fn=lambda message: None,
        now=NOW,
    )
    result = retry.review([dict(row)], tmp_path / "retry")

    assert ("Account", "child-1", {"Name": "Requested Name"}) in client.updated
    assert ("Account", "child-2", {"Name": "Requested Name"}) in client.updated
    assert result.pending_batches == 0


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
    queue = json.loads(result.queue_path.read_text(encoding="utf-8"))
    account_change = next(
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] == "Name"
    )
    assert account_change["status"] == "completed"
    assert account_change["outcome"] == "no-op"
    assert "Company Name:" not in result.response_path.read_text(encoding="utf-8")


def test_manual_change_is_refetched_and_must_match(tmp_path):
    client = FakeClient()
    client.get_sequences[("Account", "account-1")] = [
        account_record(),
        account_record(),
        account_record(),
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
    queue = json.loads((tmp_path / "review_queue.json").read_text(encoding="utf-8"))
    failed = next(
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] == "Name"
    )
    assert failed["status"] == "failed"


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
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Alexa",
            "Cert_Last_Name__c": "Jones",
            "Cert_Title__c": "Director",
            "Cert_Email__c": "old@example.com",
            "Cert_Phone__c": "312-555-0199",
        }
    )
    feeder = Feeder(
        [
            "1",
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
    )

    result = processor.review([row], tmp_path)

    contact_query = next(query for query in client.queries if query[0] == "Contact")
    assert contact_query[2] == "Email = 'old@example.com'"
    assert "AccountId" not in contact_query[2]
    assert any("Contact choice" in prompt for prompt in feeder.prompts)
    assert sum(prompt.startswith("Decision [") for prompt in feeder.prompts) == 5
    assert (
        "Contact",
        "contact-1",
        {
            "FirstName": "Alexa",
            "LastName": "Jones",
            "Title": "Director",
            "Phone": "312.555.0199",
        },
    ) in client.updated
    assert not any(
        item[0] == "Account" and "Cert_Certification_Contact__c" in item[2]
        for item in client.updated
    )
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        item["field"] == "FirstName"
        and item["action"] == "update Contact from approved fields"
        and item["result"] == "applied"
        for item in entries
    )
    assert any(
        item["field"] == "Cert_Certification_Contact__c"
        and item["result"] == "rejected"
        for item in entries
    )
    displayed = "\n".join(output)
    assert "Reconciled Contact: Alexa Jones <old@example.com>" in displayed
    assert ("First Name | Alex | Alexa | submission-1 / Certification") in displayed
    assert (
        "Phone | 312-555-0100 | 312.555.0199 | submission-1 / Certification"
    ) in displayed
    assert "Contact First Name: Alexa Jones <old@example.com>" in displayed
    assert "Contact Phone: Alexa Jones <old@example.com>" in displayed
    assert "Current Salesforce value: {" not in displayed
    assert "Proposed value: {" not in displayed
    assert (
        "Certification Account Role\n"
        "Current Salesforce value: Old Contact <old.contact@example.com>\n"
        "Proposed value: Alexa Jones <old@example.com>"
    ) in displayed
    assert "contact-1" not in "\n".join(feeder.prompts)
    assert "old-contact" not in "\n".join(feeder.prompts)


def test_incomplete_contact_is_not_automatically_created(tmp_path):
    client = FakeClient()
    client.records[("Company_Profile_Change__c", "submission-1")][
        "Cert_First_Name__c"
    ] = "Only"
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


def test_incomplete_new_contact_can_use_manual_creation_path(tmp_path):
    client = FakeClient()
    client.records[("Company_Profile_Change__c", "submission-1")][
        "Cert_First_Name__c"
    ] = "Only"

    def answer(prompt):
        if prompt.startswith("Continue with this staged row"):
            return ""
        if prompt.startswith("Decision ["):
            decisions = iter(("make manually", "apply automatically"))
            answer.decisions = getattr(answer, "decisions", decisions)
            return next(answer.decisions)
        if prompt.startswith("Create the Contact in Salesforce"):
            manual = {
                "Id": "manual-contact",
                "AccountId": "account-1",
                "FirstName": "Only",
                "LastName": "Entered Manually",
                "Title": "",
                "Email": "",
                "Phone": "",
            }
            client.records[("Contact", "manual-contact")] = manual
            client.contacts.append(manual)
            return "manual-contact"
        if prompt.startswith("Make the Contact First Name change"):
            return ""
        if prompt.startswith("Was the response email"):
            return "yes"
        raise AssertionError(prompt)

    result = InteractiveProfileUpdateProcessor(
        client, input_fn=answer, output_fn=lambda message: None, now=NOW
    ).review(
        [
            staged_row(
                certification_first_name="Only",
                certification_resolution_action="create_contact",
            )
        ],
        tmp_path,
    )

    assert client.created == []
    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "manual-contact"},
    ) in client.updated
    verified = next(
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
        if '"field": "FirstName"' in line
        and '"result": "verified manually"' in line
    )
    assert verified["final_value"] == "Only"


def test_valid_contact_creation_precedes_field_and_role_decisions(tmp_path):
    client = FakeClient()
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "New",
            "Cert_Last_Name__c": "Person",
            "Cert_Email__c": "new.person@example.com",
        }
    )
    feeder = Feeder(
        [
            "apply automatically",
            "apply automatically",
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
    assert sum(prompt.startswith("Decision [") for prompt in feeder.prompts) == 4
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
    displayed = "\n".join(output)
    assert "Reconciled Contact: New Person <new.person@example.com>" in displayed
    assert "First Name | (blank) | New | submission-1 / Certification" in displayed
    assert (
        "Email | (blank) | new.person@example.com | submission-1 / Certification"
    ) in displayed
    assert "Contact First Name: New Person <new.person@example.com>" in displayed
    assert "Contact Last Name: New Person <new.person@example.com>" in displayed
    assert "Contact Email: New Person <new.person@example.com>" in displayed
    assert "Proposed value: {" not in displayed


def test_new_contract_reuses_submitter_role_contact_and_assigns_case(tmp_path):
    client = FakeClient()
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Sam",
            "Cert_Last_Name__c": "Submitter",
            "Cert_Email__c": "sam@example.com",
            "Cert_Phone__c": "312-555-0101",
        }
    )
    feeder = Feeder(
        ["apply automatically"] * 5 + ["yes"]
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=feeder,
        output_fn=lambda message: None,
        now=NOW,
    )
    sources = [
        ContactSource("submitter", submission_id="submission-1"),
        ContactSource("role", role="certification", submission_id="submission-1"),
    ]
    submitted = {
        "first_name": "Sam",
        "last_name": "Submitter",
        "email": "sam@example.com",
        "phone": "312-555-0101",
    }
    row = staged_row(
        certification_first_name="Sam",
        certification_last_name="Submitter",
        certification_email="sam@example.com",
        certification_phone="312-555-0101",
        contact_resolutions=staged_resolution(
            sources=sources,
            submitted=submitted,
        ),
    )

    result = processor.review([row], tmp_path)

    assert client.created == [
        (
            "Contact",
            {
                "AccountId": "account-1",
                "FirstName": "Sam",
                "LastName": "Submitter",
                "Email": "sam@example.com",
                "Phone": "312.555.0101",
            },
        )
    ]
    assert ("Case", "case-1", {"ContactId": "created-1"}) in client.updated
    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "created-1"},
    ) in client.updated
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    contact_create = next(
        entry
        for entry in entries
        if entry["action"] == "create Contact from approved fields"
    )
    case_assignment = next(
        entry
        for entry in entries
        if entry["action"] == "assign created submitter Contact to Case"
    )
    role_assignment = next(
        entry for entry in entries if entry["field"] == "Cert_Certification_Contact__c"
    )
    assert contact_create["comparison_key"] == "sam@example.com"
    assert case_assignment["target_record_id"] == "case-1"
    assert role_assignment["selected_contact"]["Id"] == "created-1"


def test_fresh_contact_collection_normalizes_values_before_comparison():
    client = FakeClient(
        source=source_record(
            Name__c="  jane mcdonald ",
            Email__c=" JANE@Example.COM ",
            Phone__c="+1 (312) 555-0100 ext. 123",
        )
    )
    processor = InteractiveProfileUpdateProcessor(client, now=NOW)
    row = staged_row(
        contact_resolutions=staged_resolution(
            email="old@example.com",
            sources=[ContactSource("submitter", submission_id="submission-1")],
            submitted={
                "first_name": "Old",
                "last_name": "Value",
                "email": "old@example.com",
                "phone": "old",
            },
        )
    )
    batch = build_case_batches([row], now=NOW)[0]

    items, _ = processor._collect_contact_work(
        batch,
        {"submission-1": client.records[("Company_Profile_Change__c", "submission-1")]},
    )

    assert len(items) == 1
    item = items[0]
    assert {
        field_name: set(values) for field_name, values in item.proposals.items()
    } == {
        "first_name": {"Jane"},
        "last_name": {"McDonald"},
        "email": {"jane@example.com"},
        "phone": {"312.555.0100 x123"},
    }
    assert any(query[2] == "Email = 'jane@example.com'" for query in client.queries)


def test_fresh_role_without_email_is_normalized_before_review(tmp_path):
    current = {
        "Id": "contact-quality",
        "AccountId": "account-1",
        "FirstName": "Taylor",
        "LastName": "Lee",
        "Title": "Old Title",
        "Email": "taylor@example.com",
        "Phone": "312.555.0199",
    }
    client = FakeClient(
        source=source_record(
            QC_Title__c=" chief qa officer ",
            Quality_Phone__c="+1 (312) 555-0104 #0007",
        ),
        account=account_record(Cert_Marketing_Contact__c="contact-quality"),
        contacts=[current],
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["apply automatically", "apply automatically", "yes"]),
        output_fn=lambda message: None,
        now=NOW,
    )

    processor.review([staged_row(contact_resolutions="[]")], tmp_path)

    assert (
        "Contact",
        "contact-quality",
        {
            "Title": "Chief QA Officer",
            "Phone": "312.555.0104 x0007",
        },
    ) in client.updated


def test_contact_phase_combines_roles_into_one_write_before_account_and_role_links(
    tmp_path,
):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Old Title",
        "Email": "alex@example.com",
        "Phone": "312.555.0100",
    }
    client = FakeClient(contacts=[contact])
    source = client.records[("Company_Profile_Change__c", "submission-1")]
    source.update(
        {
            "Cert_First_Name__c": "Alex",
            "Cert_Last_Name__c": "Smith",
            "Cert_Email__c": "alex@example.com",
            "Cert_Phone__c": "312-555-0199",
            "Principal_First_Name__c": "Alex",
            "Principal_Last_Name__c": "Smith",
            "Principal_Title__c": "Director",
            "Principal_Email__c": "alex@example.com",
            "Revised_Company_Name__c": "Acme Steel LLC",
        }
    )
    sources = [
        ContactSource("role", role="certification", submission_id="submission-1"),
        ContactSource("role", role="principal", submission_id="submission-1"),
    ]
    feeder = Feeder(
        [
            "apply automatically",
            "apply automatically",
            "apply automatically",
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
    row = staged_row(
        revised_company_name="stale staging value",
        certification_first_name="fallback",
        certification_email="alex@example.com",
        principal_first_name="fallback",
        principal_email="alex@example.com",
        contact_resolutions=staged_resolution(
            email="alex@example.com",
            sources=sources,
            submitted={"title": "stale fallback"},
        ),
    )

    result = processor.review([row], tmp_path)

    contact_writes = [
        item
        for item in client.updated
        if item[0] == "Contact" and item[1] == "contact-1"
    ]
    assert contact_writes == [
        (
            "Contact",
            "contact-1",
            {"Title": "Director", "Phone": "312.555.0199"},
        )
    ]
    contact_index = client.updated.index(contact_writes[0])
    account_index = next(
        index
        for index, item in enumerate(client.updated)
        if item[:2] == ("Account", "account-1") and "Name" in item[2]
    )
    role_index = next(
        index
        for index, item in enumerate(client.updated)
        if item[:2] == ("Account", "account-1")
        and "Cert_Certification_Contact__c" in item[2]
    )
    assert contact_index < account_index < role_index
    displayed = "\n".join(output)
    assert displayed.index("Contact Updates") < displayed.index("Account Updates")
    assert displayed.index("Account Updates") < displayed.index("Role Links")

    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    contact_fields = {
        entry["field"]: entry
        for entry in entries
        if entry["action"] == "update Contact from approved fields"
    }
    assert {
        field: entry["proposed_value"] for field, entry in contact_fields.items()
    } == {"Phone": "312.555.0199", "Title": "Director"}
    assert all(
        entry["source_submission_ids"] == ["submission-1"]
        for entry in contact_fields.values()
    )


def test_conflicting_contact_values_are_resolved_before_any_contact_write(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Current Title",
        "Email": "alex@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    first = client.records[("Company_Profile_Change__c", "submission-1")]
    first.update(
        {
            "Cert_First_Name__c": "Alex",
            "Cert_Last_Name__c": "Smith",
            "Cert_Title__c": "Manager",
            "Cert_Email__c": "alex@example.com",
        }
    )
    second = source_record(
        Id="submission-2",
        Name="PU-101",
        Cert_First_Name__c="Alex",
        Cert_Last_Name__c="Smith",
        Cert_Title__c="Director",
        Cert_Email__c="alex@example.com",
    )
    client.records[("Company_Profile_Change__c", "submission-2")] = second
    sources = [
        ContactSource("role", role="certification", submission_id="submission-1"),
        ContactSource("role", role="certification", submission_id="submission-2"),
    ]
    writes_seen_at_conflict = []

    def answer(prompt):
        if prompt.startswith("Continue with this staged row"):
            return ""
        if prompt.startswith("Choose reconciled Title"):
            writes_seen_at_conflict.append(
                [item for item in client.updated if item[0] == "Contact"]
            )
            return "current"
        if prompt.startswith("Was the response email"):
            return "yes"
        raise AssertionError(prompt)

    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=answer,
        output_fn=output.append,
        now=NOW,
    )
    row = staged_row(
        source_submission_ids=json.dumps(["submission-1", "submission-2"]),
        source_submission_names=json.dumps(["PU-100", "PU-101"]),
        contact_resolutions=staged_resolution(
            email="alex@example.com",
            sources=sources,
            submitted={"title": "stale fallback"},
        ),
    )

    result = processor.review([row], tmp_path)

    assert writes_seen_at_conflict == [[]]
    assert not any(item[0] == "Contact" for item in client.updated)
    displayed = "\n".join(output)
    assert "Manager" in displayed
    assert "Director" in displayed
    assert "submission-1 / Certification" in displayed
    assert "submission-2 / Certification" in displayed
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    conflict = next(
        entry
        for entry in entries
        if entry["action"] == "resolve Contact field conflict"
    )
    assert conflict["field"] == "Title"
    assert conflict["proposed_value"] == "Current Title"
    assert conflict["result"] == "verified manually"


def test_interruption_during_contact_conflict_is_audited_before_any_write(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Current Title",
        "Email": "alex@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    first = client.records[("Company_Profile_Change__c", "submission-1")]
    first.update(
        {
            "Cert_Title__c": "Manager",
            "Cert_Email__c": "alex@example.com",
        }
    )
    client.records[("Company_Profile_Change__c", "submission-2")] = source_record(
        Id="submission-2",
        Name="PU-101",
        Cert_Title__c="Director",
        Cert_Email__c="alex@example.com",
    )

    def interrupt(prompt):
        if prompt.startswith("Continue with this staged row"):
            return ""
        if prompt.startswith("Choose reconciled Title"):
            raise KeyboardInterrupt
        raise AssertionError(prompt)

    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=interrupt,
        output_fn=lambda message: None,
        now=NOW,
    )
    row = staged_row(
        source_submission_ids=json.dumps(["submission-1", "submission-2"]),
        source_submission_names=json.dumps(["PU-100", "PU-101"]),
        contact_resolutions="[]",
    )

    with pytest.raises(ProcessingInterrupted):
        processor.review([row], tmp_path)

    assert not any(item[0] == "Contact" for item in client.updated)
    entries = [
        json.loads(line)
        for line in (tmp_path / "review_audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        entry["action"] == "resolve Contact field conflict"
        and entry["result"] == "interrupted"
        for entry in entries
    )
    assert ("Case", "case-1", {"Status": "Pending"}) in client.updated


def test_submitted_email_selects_its_contact_instead_of_current_role_contact(
    tmp_path,
):
    current_role_contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Ray",
        "LastName": "Ryan",
        "Title": "",
        "Email": "ray@example.com",
        "Phone": "",
    }
    submitted_contact = {
        "Id": "contact-2",
        "AccountId": "account-1",
        "FirstName": "Tim",
        "LastName": "Ryan",
        "Title": "Old Title",
        "Email": "tim@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[current_role_contact, submitted_contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Tim",
            "Cert_Last_Name__c": "Ryan",
            "Cert_Title__c": "President",
            "Cert_Email__c": "tim@example.com",
        }
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(
            [
                "apply automatically",
                "apply automatically",
                "yes",
            ]
        ),
        output_fn=lambda message: None,
        now=NOW,
    )
    row = staged_row(
        certification_resolution_action="change_email",
        certification_salesforce_contact_id="contact-1",
        contact_resolutions=staged_resolution(
            email="tim@example.com",
            sources=[
                ContactSource(
                    "role",
                    role="certification",
                    submission_id="submission-1",
                )
            ],
            submitted={"email": "stale@example.com"},
        ),
    )

    processor.review([row], tmp_path)

    assert client.created == []
    assert [item for item in client.updated if item[0] == "Contact"] == [
        ("Contact", "contact-2", {"Title": "President"})
    ]
    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "contact-2"},
    ) in client.updated


def test_contact_updates_use_tim_and_stacy_emails_before_assigning_roles(tmp_path):
    ray = {
        "Id": "contact-ray",
        "AccountId": "account-1",
        "FirstName": "Ray",
        "LastName": "Ryan",
        "Title": "President",
        "Email": "ray@wsfabrication.com",
        "Phone": "312.555.0100",
    }
    tim = {
        "Id": "contact-tim",
        "AccountId": "account-1",
        "FirstName": "Tim",
        "LastName": "Ryan",
        "Title": "Old Title",
        "Email": "tim@wsfabrication.com",
        "Phone": "312.555.0101",
    }
    stacy = {
        "Id": "contact-stacy",
        "AccountId": "account-1",
        "FirstName": "Stacy",
        "LastName": "Morgan",
        "Title": "",
        "Email": "stacy@wsfabrication.com",
        "Phone": "312.555.0102",
    }
    client = FakeClient(
        source=source_record(
            Name__c="Stacy Morgan",
            Email__c="stacy@wsfabrication.com",
            Phone__c="312-555-0198",
            Principal_First_Name__c="Tim",
            Principal_Last_Name__c="Ryan",
            Principal_Title__c="Owner",
            Principal_Email__c="tim@wsfabrication.com",
            Principal_Phone__c="312-555-0199",
        ),
        account=account_record(Cert_Principal_Contact__c="contact-ray"),
        contacts=[ray, tim, stacy],
    )
    submitter_resolution = json.loads(
        staged_resolution(
            email="stacy@wsfabrication.com",
            sources=[ContactSource("submitter", submission_id="submission-1")],
            classification=ContactResolutionClassification.USE_EXISTING,
            selected=stacy,
        )
    )[0]
    # This deliberately reproduces the stale/wrong role-based selection. Fresh
    # email resolution must still choose Tim rather than Ray.
    principal_resolution = json.loads(
        staged_resolution(
            email="tim@wsfabrication.com",
            sources=[
                ContactSource(
                    "role",
                    role="principal",
                    submission_id="submission-1",
                )
            ],
            classification=ContactResolutionClassification.USE_EXISTING,
            selected=ray,
        )
    )[0]
    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(
            [
                "apply automatically",
                "apply automatically",
                "apply automatically",
                "apply automatically",
                "yes",
            ]
        ),
        output_fn=output.append,
        now=NOW,
    )

    processor.review(
        [
            staged_row(
                submitter_name="Stacy Morgan",
                submitter_email="stacy@wsfabrication.com",
                principal_resolution_action="update_contact",
                principal_salesforce_contact_id="contact-ray",
                contact_resolutions=json.dumps(
                    [submitter_resolution, principal_resolution]
                ),
            )
        ],
        tmp_path,
    )

    assert [item for item in client.updated if item[0] == "Contact"] == [
        ("Contact", "contact-stacy", {"Phone": "312.555.0198"}),
        (
            "Contact",
            "contact-tim",
            {"Title": "Owner", "Phone": "312.555.0199"},
        ),
    ]
    assert not any(
        object_name == "Contact" and record_id == "contact-ray"
        for object_name, record_id, _ in client.updated
    )
    assert (
        "Account",
        "account-1",
        {"Cert_Principal_Contact__c": "contact-tim"},
    ) in client.updated
    displayed = "\n".join(output)
    assert "Reconciled Contact: Stacy Morgan <stacy@wsfabrication.com>" in displayed
    assert "Reconciled Contact: Tim Ryan <tim@wsfabrication.com>" in displayed
    assert "Current Salesforce value: {" not in displayed
    assert "Proposed value: {" not in displayed


def test_shared_new_contact_is_created_once_and_reused_for_both_roles(tmp_path):
    client = FakeClient()
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "New",
            "Cert_Last_Name__c": "Person",
            "Cert_Email__c": "new@example.com",
            "Principal_First_Name__c": "New",
            "Principal_Last_Name__c": "Person",
            "Principal_Email__c": "new@example.com",
        }
    )
    sources = [
        ContactSource("role", role="certification", submission_id="submission-1"),
        ContactSource("role", role="principal", submission_id="submission-1"),
    ]
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(
            [
                "apply automatically",
                "apply automatically",
                "apply automatically",
                "apply automatically",
                "apply automatically",
                "yes",
            ]
        ),
        output_fn=lambda message: None,
        now=NOW,
    )

    processor.review(
        [
            staged_row(
                contact_resolutions=staged_resolution(
                    email="new@example.com",
                    sources=sources,
                    submitted={"email": "stale@example.com"},
                )
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
                "Email": "new@example.com",
            },
        )
    ]
    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "created-1"},
    ) in client.updated
    assert (
        "Account",
        "account-1",
        {"Cert_Principal_Contact__c": "created-1"},
    ) in client.updated


def test_distinct_emails_in_one_role_stay_separate_until_role_assignment(
    tmp_path,
):
    client = FakeClient()
    first = client.records[("Company_Profile_Change__c", "submission-1")]
    first.update(
        {
            "Cert_First_Name__c": "New",
            "Cert_Last_Name__c": "Person",
            "Cert_Email__c": "first@example.com",
        }
    )
    second = source_record(
        Id="submission-2",
        Name="PU-101",
        Cert_First_Name__c="New",
        Cert_Last_Name__c="Person",
        Cert_Email__c="second@example.com",
    )
    client.records[("Company_Profile_Change__c", "submission-2")] = second
    sources = [
        ContactSource("role", role="certification", submission_id="submission-1"),
        ContactSource("role", role="certification", submission_id="submission-2"),
    ]
    feeder = Feeder(
        [
            "apply automatically",
            "apply automatically",
            "apply automatically",
            "apply automatically",
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

    result = processor.review(
        [
            staged_row(
                source_submission_ids=json.dumps(["submission-1", "submission-2"]),
                source_submission_names=json.dumps(["PU-100", "PU-101"]),
                certification_resolution_action="create_contact",
                contact_resolutions=staged_resolution(
                    email="second@example.com",
                    sources=sources,
                    submitted={"email": "flattened@example.com"},
                ),
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
                "Email": "first@example.com",
            },
        ),
        (
            "Contact",
            {
                "AccountId": "account-1",
                "FirstName": "New",
                "LastName": "Person",
                "Email": "second@example.com",
            },
        ),
    ]
    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "created-2"},
    ) in client.updated
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    creates = [
        entry
        for entry in entries
        if entry["action"] == "create Contact from approved fields"
        and entry["field"] == "Email"
    ]
    assert [entry["proposed_value"] for entry in creates] == [
        "first@example.com",
        "second@example.com",
    ]
    assert [entry["source_submission_ids"] for entry in creates] == [
        ["submission-1"],
        ["submission-2"],
    ]
    assert not any(
        entry["action"] == "resolve Contact field conflict"
        and entry["field"] == "Email"
        for entry in entries
    )


def test_every_fresh_submitter_submission_contributes_to_reconciliation(tmp_path):
    client = FakeClient(
        source=source_record(
            Name__c="Sam First",
            Email__c="sam@example.com",
        )
    )
    second = source_record(
        Id="submission-2",
        Name="PU-101",
        Name__c="Sam Second",
        Email__c="sam@example.com",
    )
    client.records[("Company_Profile_Change__c", "submission-2")] = second
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(
            ["2", "apply automatically", "apply automatically", "apply automatically"]
        ),
        output_fn=lambda message: None,
        now=NOW,
    )
    row = staged_row(
        source_submission_ids=json.dumps(["submission-1", "submission-2"]),
        source_submission_names=json.dumps(["PU-100", "PU-101"]),
        contact_resolutions=staged_resolution(
            sources=[ContactSource("submitter", submission_id="submission-2")],
            submitted={
                "first_name": "flattened",
                "last_name": "fallback",
                "email": "sam@example.com",
            },
        ),
    )

    result = processor.review([row], tmp_path)

    assert client.created == [
        (
            "Contact",
            {
                "AccountId": "account-1",
                "FirstName": "Sam",
                "LastName": "Second",
                "Email": "sam@example.com",
            },
        )
    ]
    conflicts = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
        if '"action": "resolve Contact field conflict"' in line
    ]
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "LastName"
    assert conflicts[0]["source_submission_ids"] == [
        "submission-1",
        "submission-2",
    ]


def test_rejected_reconciled_contact_has_no_contact_write(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Old Title",
        "Email": "alex@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Alex",
            "Cert_Last_Name__c": "Smith",
            "Cert_Title__c": "Director",
            "Cert_Email__c": "alex@example.com",
        }
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["will not be made", "yes"]),
        output_fn=lambda message: None,
        now=NOW,
    )

    result = processor.review(
        [
            staged_row(
                contact_resolutions=staged_resolution(
                    email="alex@example.com",
                    sources=[
                        ContactSource("role", "certification", "submission-1")
                    ],
                    submitted={"title": "Director"},
                    classification=ContactResolutionClassification.USE_EXISTING,
                    selected=contact,
                )
            )
        ],
        tmp_path,
    )

    assert not any(item[0] == "Contact" for item in client.updated)
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    rejected = next(
        entry for entry in entries if entry["action"] == "no Contact field write"
    )
    assert rejected["decision"] == "will not be made"
    assert rejected["proposed_value"] == "Director"


def test_contact_fields_have_independent_decisions_and_one_grouped_write(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Manager",
        "Email": "alex@example.com",
        "Phone": "312.555.0100",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Alexa",
            "Cert_Last_Name__c": "Smith",
            "Cert_Title__c": "Director",
            "Cert_Email__c": "alex@example.com",
            "Cert_Phone__c": "312-555-0199",
        }
    )

    def answer(prompt):
        if prompt.startswith("Continue with this staged row"):
            return ""
        if prompt.startswith("Decision ["):
            decisions = iter(("apply automatically", "will not be made", "make manually"))
            answer.decisions = getattr(answer, "decisions", decisions)
            return next(answer.decisions)
        if prompt.startswith("Make the Contact Phone change"):
            client.records[("Contact", "contact-1")]["Phone"] = "312.555.0199"
            return ""
        if prompt.startswith("Was the response email"):
            return "yes"
        raise AssertionError(prompt)

    output = []
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=answer,
        output_fn=output.append,
        now=NOW,
    )

    result = processor.review(
        [
            staged_row(
                contact_resolutions=staged_resolution(
                    email="alex@example.com",
                    sources=[
                        ContactSource("role", "certification", "submission-1")
                    ],
                    submitted={
                        "first_name": "Alexa",
                        "title": "Director",
                        "phone": "312.555.0199",
                    },
                    classification=ContactResolutionClassification.USE_EXISTING,
                    selected=contact,
                )
            )
        ],
        tmp_path,
    )

    assert [item for item in client.updated if item[0] == "Contact"] == [
        ("Contact", "contact-1", {"FirstName": "Alexa"})
    ]
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["target_object"] == "Contact"
        and json.loads(line)["field"] in {"FirstName", "Title", "Phone"}
    ]
    by_field = {entry["field"]: entry for entry in entries}
    assert by_field["FirstName"]["decision"] == "apply automatically"
    assert by_field["FirstName"]["final_value"] == "Alexa"
    assert by_field["Title"]["result"] == "rejected"
    assert by_field["Title"]["final_value"] == "Manager"
    assert by_field["Phone"]["decision"] == "make manually"
    assert by_field["Phone"]["final_value"] == "312.555.0199"
    displayed = "\n".join(output)
    assert "\n========================================================================\nManual Contact Follow-up" in displayed
    queue = json.loads(result.queue_path.read_text(encoding="utf-8"))
    outcomes = {
        change["field"]: change["outcome"]
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] in {"FirstName", "Title", "Phone"}
    }
    assert outcomes == {
        "FirstName": "applied",
        "Title": "rejected",
        "Phone": "verified manually",
    }


def test_new_contact_omits_rejected_fields_from_grouped_create(tmp_path):
    client = FakeClient()
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "New",
            "Cert_Last_Name__c": "Person",
            "Cert_Title__c": "Director",
            "Cert_Email__c": "new.person@example.com",
        }
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(
            [
                "apply automatically",
                "apply automatically",
                "will not be made",
                "apply automatically",
                "apply automatically",
                "yes",
            ]
        ),
        output_fn=lambda message: None,
        now=NOW,
    )

    result = processor.review(
        [
            staged_row(
                certification_first_name="New",
                certification_last_name="Person",
                certification_title="Director",
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
                "Email": "new.person@example.com",
            },
        )
    ]
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert {
        entry["field"]: entry["result"]
        for entry in entries
        if entry["target_object"] == "Contact"
        and entry["field"] in {"FirstName", "LastName", "Title", "Email"}
    } == {
        "FirstName": "applied",
        "LastName": "applied",
        "Title": "rejected",
        "Email": "applied",
    }


def test_differing_manual_contact_value_defaults_to_accepting_salesforce(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Manager",
        "Email": "alex@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")][
        "Cert_Title__c"
    ] = "Director"

    def answer(prompt):
        if prompt.startswith("Continue with this staged row"):
            return ""
        if prompt.startswith("Decision ["):
            return "make manually"
        if prompt.startswith("Make the Contact Title change"):
            client.records[("Contact", "contact-1")]["Title"] = "Senior Director"
            return ""
        if prompt.startswith("Accept the current Salesforce Title"):
            return ""
        if prompt.startswith("Was the response email"):
            return "yes"
        raise AssertionError(prompt)

    output = []
    result = InteractiveProfileUpdateProcessor(
        client, input_fn=answer, output_fn=output.append, now=NOW
    ).review([staged_row(contact_resolutions="[]")], tmp_path)

    verified = next(
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
        if '"field": "Title"' in line and '"decision": "make manually"' in line
    )
    assert verified["proposed_value"] == "Director"
    assert verified["final_value"] == "Senior Director"
    displayed = "\n".join(output)
    assert "Current Salesforce value: Senior Director" in displayed
    assert "Proposed value: Director" in displayed
    assert "Senior Director" in result.response_path.read_text(encoding="utf-8")


def test_declined_manual_contact_override_fails_and_keeps_batch_retryable(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Manager",
        "Email": "alex@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")][
        "Cert_Title__c"
    ] = "Director"

    def answer(prompt):
        if prompt.startswith("Continue with this staged row"):
            return ""
        if prompt.startswith("Decision ["):
            return "make manually"
        if prompt.startswith("Make the Contact Title change"):
            client.records[("Contact", "contact-1")]["Title"] = "Senior Director"
            return ""
        if prompt.startswith("Accept the current Salesforce Title"):
            return "no"
        raise AssertionError(prompt)

    with pytest.raises(ProcessingError, match="not accepted"):
        InteractiveProfileUpdateProcessor(
            client, input_fn=answer, output_fn=lambda message: None, now=NOW
        ).review(
            [
                staged_row(
                    contact_resolutions=staged_resolution(
                        sources=[
                            ContactSource(
                                "role", "certification", "submission-1"
                            )
                        ],
                        submitted={"title": "Director"},
                        classification=ContactResolutionClassification.USE_EXISTING,
                        selected=contact,
                    )
                )
            ],
            tmp_path,
        )

    assert not any(item[0] == "Company_Profile_Change__c" for item in client.updated)
    queue = json.loads((tmp_path / "review_queue.json").read_text(encoding="utf-8"))
    title = next(
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] == "Title"
    )
    assert title["status"] == "failed"


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), EOFError()])
def test_manual_contact_confirmation_interruption_is_audited(
    tmp_path, interruption
):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Manager",
        "Email": "alex@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")][
        "Cert_Title__c"
    ] = "Director"
    feeder = Feeder(["make manually", interruption])

    with pytest.raises(ProcessingInterrupted):
        InteractiveProfileUpdateProcessor(
            client, input_fn=feeder, output_fn=lambda message: None, now=NOW
        ).review([staged_row(contact_resolutions="[]")], tmp_path)

    entries = [
        json.loads(line)
        for line in (tmp_path / "review_audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    interrupted = next(
        entry
        for entry in entries
        if entry["field"] == "Title" and entry["result"] == "interrupted"
    )
    assert interrupted["decision"] == "make manually"
    assert not any(item[0] == "Company_Profile_Change__c" for item in client.updated)


def test_manual_contact_salesforce_read_failure_is_audited(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Manager",
        "Email": "alex@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")][
        "Cert_Title__c"
    ] = "Director"
    original_get = client.get_record

    def fail_manual_read(object_name, record_id, fields):
        if object_name == "Contact" and list(fields) == ["Id", "Title"]:
            raise SalesforceError("manual Contact read failed")
        return original_get(object_name, record_id, fields)

    client.get_record = fail_manual_read

    with pytest.raises(ProcessingError, match="manual Contact read failed"):
        InteractiveProfileUpdateProcessor(
            client,
            input_fn=Feeder(["make manually", ""]),
            output_fn=lambda message: None,
            now=NOW,
        ).review([staged_row(contact_resolutions="[]")], tmp_path)

    entries = [
        json.loads(line)
        for line in (tmp_path / "review_audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    failed = next(
        entry
        for entry in entries
        if entry["field"] == "Title" and entry["result"] == "failed"
    )
    assert failed["decision"] == "make manually"
    assert failed["error"] == "manual Contact read failed"


def test_all_rejected_new_contact_is_not_created_or_assigned(tmp_path):
    client = FakeClient()
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "New",
            "Cert_Last_Name__c": "Person",
            "Cert_Email__c": "new.person@example.com",
        }
    )
    contact_resolutions = staged_resolution(
        email="new.person@example.com",
        sources=[ContactSource("role", "certification", "submission-1")],
        submitted={
            "first_name": "New",
            "last_name": "Person",
            "email": "new.person@example.com",
        },
    )

    result = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["will not be made"] * 3 + ["yes"]),
        output_fn=lambda message: None,
        now=NOW,
    ).review(
        [
            staged_row(
                certification_resolution_action="create_contact",
                contact_resolutions=contact_resolutions,
            )
        ],
        tmp_path,
    )

    assert client.created == []
    assert not any(
        object_name == "Account" and "Cert_Certification_Contact__c" in values
        for object_name, _, values in client.updated
    )
    queue = json.loads(result.queue_path.read_text(encoding="utf-8"))
    contact_changes = [
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["salesforce"]["object_name"] == "Contact"
    ]
    assert {change["outcome"] for change in contact_changes} == {"rejected"}


def test_manual_contact_decisions_verify_fields_individually(tmp_path):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Old Title",
        "Email": "alex@example.com",
        "Phone": "312.555.0100",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Alex",
            "Cert_Last_Name__c": "Smith",
            "Cert_Title__c": "Director",
            "Cert_Email__c": "alex@example.com",
            "Cert_Phone__c": "312-555-0199",
        }
    )

    def answer(prompt):
        if prompt.startswith("Continue with this staged row"):
            return ""
        if prompt.startswith("Decision ["):
            return "make manually"
        if prompt.startswith("Make the Contact Title change"):
            client.records[("Contact", "contact-1")]["Title"] = "Director"
            return ""
        if prompt.startswith("Make the Contact Phone change"):
            client.records[("Contact", "contact-1")]["Phone"] = "312.555.0199"
            return ""
        if prompt.startswith("Was the response email"):
            return "yes"
        raise AssertionError(prompt)

    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=answer,
        output_fn=lambda message: None,
        now=NOW,
    )

    result = processor.review([staged_row(contact_resolutions="[]")], tmp_path)

    assert not any(item[0] == "Contact" for item in client.updated)
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    verified = [
        entry
        for entry in entries
        if entry["action"] == "verify Contact field manually"
    ]
    assert {entry["field"] for entry in verified} == {"Title", "Phone"}
    assert all(entry["result"] == "verified manually" for entry in verified)
    assert {entry["field"]: entry["proposed_value"] for entry in verified} == {
        "Phone": "312.555.0199",
        "Title": "Director",
    }


def test_contact_failure_is_audited_and_same_input_retries_then_becomes_noop(
    tmp_path,
):
    contact = {
        "Id": "contact-1",
        "AccountId": "account-1",
        "FirstName": "Alex",
        "LastName": "Smith",
        "Title": "Old Title",
        "Email": "alex@example.com",
        "Phone": "",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-1"),
        contacts=[contact],
    )
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Alex",
            "Cert_Last_Name__c": "Smith",
            "Cert_Title__c": "Director",
            "Cert_Email__c": "alex@example.com",
        }
    )
    row = staged_row(contact_resolutions="[]")
    client.fail_update = ("Contact", "contact-1")
    failed_processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["apply automatically"]),
        output_fn=lambda message: None,
        now=NOW,
    )

    with pytest.raises(ProcessingError, match="write failed"):
        failed_processor.review([row], tmp_path / "failed")

    failed_entries = [
        json.loads(line)
        for line in (tmp_path / "failed" / "review_audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        entry["action"] == "update Contact from approved fields"
        and entry["result"] == "failed"
        for entry in failed_entries
    )
    assert not any(item[0] == "Company_Profile_Change__c" for item in client.updated)

    client.fail_update = None
    retry_processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["apply automatically", "yes"]),
        output_fn=lambda message: None,
        now=NOW,
    )
    retry_processor.review([row], tmp_path / "retry")
    successful_contact_writes = [
        item for item in client.updated if item[0] == "Contact"
    ]
    assert successful_contact_writes == [
        ("Contact", "contact-1", {"Title": "Director"})
    ]

    noop_processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["yes"]),
        output_fn=lambda message: None,
        now=NOW,
    )
    noop_result = noop_processor.review([row], tmp_path / "noop")
    assert [
        item for item in client.updated if item[0] == "Contact"
    ] == successful_contact_writes
    noop_entries = [
        json.loads(line)
        for line in noop_result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        entry["action"] == "Contact field already current"
        for entry in noop_entries
    )


def test_compatibility_review_normalizes_fresh_role_values(tmp_path):
    client = FakeClient(
        source=source_record(
            Cert_First_Name__c=" aLEX ",
            Cert_Last_Name__c=" mcdonald ",
            Cert_Title__c=" chief qa officer ",
            Cert_Email__c=" NEW.CONTACT@Example.COM ",
            Cert_Phone__c="+1 (312) 555-0105 ext 42",
        )
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["apply automatically"] * 6 + ["yes"]),
        output_fn=lambda message: None,
        now=NOW,
    )

    processor.review(
        [
            staged_row(
                certification_first_name="stale",
                certification_last_name="stale",
                certification_email="stale@example.com",
            )
        ],
        tmp_path,
    )

    assert client.created == [
        (
            "Contact",
            {
                "AccountId": "account-1",
                "FirstName": "Alex",
                "LastName": "McDonald",
                "Title": "Chief QA Officer",
                "Email": "new.contact@example.com",
                "Phone": "312.555.0105 x42",
            },
        )
    ]
    assert any(
        query[2] == "Email = 'new.contact@example.com'" for query in client.queries
    )


def test_duplicate_create_can_recover_with_an_alternate_email_contact(tmp_path):
    alternate = {
        "Id": "alternate-contact",
        "AccountId": "different-account",
        "FirstName": "Existing",
        "LastName": "Person",
        "Title": "",
        "Email": "other@example.com",
        "Phone": "",
    }
    client = FakeClient(contacts=[alternate])
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "New",
            "Cert_Last_Name__c": "Person",
            "Cert_Email__c": "new.person@example.com",
        }
    )
    client.fail_create = SalesforceError(
        "Salesforce failed to create Contact: Use one of these records?",
        error_code="DUPLICATES_DETECTED",
        salesforce_message="Use one of these records?",
    )
    feeder = Feeder(
        [
            "apply automatically",
            "apply automatically",
            "apply automatically",
            "3",
            "other@example.com",
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
    row = staged_row(
        certification_first_name="New",
        certification_last_name="Person",
        certification_email="new.person@example.com",
        contact_resolutions=staged_resolution(
            email="new.person@example.com",
            sources=[
                ContactSource(
                    "role",
                    role="certification",
                    submission_id="submission-1",
                )
            ],
            submitted={
                "first_name": "New",
                "last_name": "Person",
                "email": "new.person@example.com",
            },
        ),
    )

    result = processor.review([row], tmp_path)

    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "alternate-contact"},
    ) in client.updated
    entries = [
        json.loads(line)
        for line in result.audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(entry["field"] == "Contact" for entry in entries)
    recovered = next(
        entry
        for entry in entries
        if entry["action"] == "use alternate-email Contact after duplicate failure"
        and entry["field"] == "Email"
    )
    assert recovered["selected_contact"]["Id"] == "alternate-contact"
    assert recovered["proposed_value"] == "new.person@example.com"
    assert recovered["final_value"] == "other@example.com"


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
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Alex",
            "Cert_Last_Name__c": "Smith",
            "Cert_Email__c": "shared@example.com",
        }
    )
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
    failure = next(item for item in entries if item["result"] == "failed")
    assert failure["field"] == "resolution"
    assert failure["proposed_value"] == "shared@example.com"
    assert failure["result"] == "failed"
    assert not any(prompt.startswith("Decision [") for prompt in feeder.prompts)
    assert any("Contact choice" in prompt for prompt in feeder.prompts)
    assert "Candidate 1" in "\n".join(output)
    assert "Candidate 2" in "\n".join(output)
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
            "Phone": "312.555.0200",
        },
    ]
    client = FakeClient(
        account=account_record(
            Cert_Certification_Contact__c="contact-1",
            Cert_Principal_Contact__c="contact-2",
        ),
        contacts=contacts,
    )
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_First_Name__c": "Alex",
            "Cert_Last_Name__c": "Smith",
            "Cert_Title__c": "Safety Director",
            "Cert_Email__c": "alex@example.com",
            "Cert_Phone__c": "312-555-0199",
            "Principal_First_Name__c": "Pat",
            "Principal_Last_Name__c": "Jones",
            "Principal_Title__c": "President",
            "Principal_Email__c": "pat@example.com",
            "Principal_Phone__c": "312-555-0200",
        }
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
        "alex@example.com, 312.555.0199"
    ) in response
    assert (
        "Replaces Alex Smith, Safety Director, alex@example.com, 312-555-0100"
    ) in response
    assert (
        "Principal Contact: Pat Jones, President, "
        "pat@example.com, 312.555.0200 - no change"
    ) in response
    assert response.count("Replaces ") == 1
    assert "Certification Contact Phone:" not in response
    assert "Cert_Certification_Contact__c" not in response


def test_role_response_uses_contact_details_from_start_of_batch(tmp_path):
    mike = {
        "Id": "contact-mike",
        "AccountId": "account-1",
        "FirstName": "Mike",
        "LastName": "Miller",
        "Title": "Certification Manager",
        "Email": "mike@example.com",
        "Phone": "555-555-5555",
    }
    mary = {
        "Id": "contact-mary",
        "AccountId": "account-1",
        "FirstName": "Mary",
        "LastName": "Martin",
        "Title": "Quality Director",
        "Email": "mary@example.com",
        "Phone": "312.555.0100",
    }
    client = FakeClient(
        account=account_record(Cert_Certification_Contact__c="contact-mike"),
        contacts=[mike, mary],
    )
    client.records[("Company_Profile_Change__c", "submission-1")].update(
        {
            "Cert_Email__c": "mike@example.com",
            "Cert_Phone__c": "222.222.2222",
        }
    )
    client.records[("Company_Profile_Change__c", "submission-2")] = source_record(
        Id="submission-2",
        Name="PU-101",
        CreatedDate="2026-07-15T15:30:00.000+0000",
        Cert_First_Name__c="Mary",
        Cert_Last_Name__c="Martin",
        Cert_Title__c="Quality Director",
        Cert_Email__c="mary@example.com",
        Cert_Phone__c="312.555.0100",
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["apply automatically", "apply automatically", "yes"]),
        output_fn=lambda message: None,
        now=NOW,
    )

    result = processor.review(
        [
            staged_row(
                source_submission_ids=json.dumps(["submission-1", "submission-2"]),
                source_submission_names=json.dumps(["PU-100", "PU-101"]),
                latest_submission_date="2026-07-15T15:30:00.000+0000",
                certification_first_name="Mary",
                certification_last_name="Martin",
                certification_title="Quality Director",
                certification_email="mary@example.com",
                certification_phone="312.555.0100",
            )
        ],
        tmp_path,
    )

    assert ("Contact", "contact-mike", {"Phone": "222.222.2222"}) in client.updated
    assert (
        "Account",
        "account-1",
        {"Cert_Certification_Contact__c": "contact-mary"},
    ) in client.updated
    response = result.response_path.read_text(encoding="utf-8")
    assert (
        "Certification Contact: Mary Martin, Quality Director, "
        "mary@example.com, 312.555.0100"
    ) in response
    assert (
        "Replaces Mike Miller, Certification Manager, "
        "mike@example.com, 555-555-5555"
    ) in response
    assert (
        "Replaces Mike Miller, Certification Manager, "
        "mike@example.com, 222.222.2222"
    ) not in response
    persisted_artifacts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (result.queue_path, result.audit_path)
    )
    assert "role_contact_snapshot" not in persisted_artifacts
    assert all("snapshot" not in column.casefold() for column in CSV_COLUMNS)
    salesforce_values = [
        values
        for _, _, values in client.updated
    ] + [values for _, values in client.created]
    assert all(
        "snapshot" not in field_name.casefold()
        for values in salesforce_values
        for field_name in values
    )


def test_role_contact_snapshot_reads_happen_before_first_batch_write(tmp_path):
    operations = []

    class OrderingClient(FakeClient):
        def get_record(self, object_name, record_id, fields):
            operations.append(("read", object_name, record_id))
            return super().get_record(object_name, record_id, fields)

        def update_record(self, object_name, record_id, values):
            operations.append(("write", object_name, record_id))
            return super().update_record(object_name, record_id, values)

        def create_record(self, object_name, values):
            operations.append(("write", object_name, "(new)"))
            return super().create_record(object_name, values)

    role_contact_ids = (
        "cert-contact",
        "principal-contact",
        "accounting-contact",
        "quality-contact",
        "new-york-contact",
    )
    client = OrderingClient(
        account=account_record(
            Cert_Certification_Contact__c=role_contact_ids[0],
            Cert_Principal_Contact__c=role_contact_ids[1],
            Cert_Accounting_Contact__c=role_contact_ids[2],
            Cert_Marketing_Contact__c=role_contact_ids[3],
            Cert_Safety_Contact__c=role_contact_ids[4],
        ),
        contacts=[
            {
                "Id": contact_id,
                "AccountId": "account-1",
                "FirstName": "Role",
                "LastName": "Contact",
                "Title": "Manager",
                "Email": f"{contact_id}@example.com",
                "Phone": "312.555.0100",
            }
            for contact_id in role_contact_ids
        ],
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["yes"]),
        output_fn=lambda message: None,
        now=NOW,
    )

    processor.review([staged_row()], tmp_path)

    first_write = next(
        index for index, operation in enumerate(operations) if operation[0] == "write"
    )
    snapshot_reads = [
        operations.index(("read", "Contact", contact_id))
        for contact_id in role_contact_ids
    ]
    assert max(snapshot_reads) < first_write


def test_parent_role_response_uses_target_accounts_original_contact(tmp_path):
    child = account_record(
        Id="child-1",
        Name="Acme Chicago",
        ParentId="account-1",
        Cert_Certification_Status__c="Certified",
        Cert_Certification_Contact__c="child-mike",
    )
    child_mike = {
        "Id": "child-mike",
        "AccountId": "child-1",
        "FirstName": "Mike",
        "LastName": "Child",
        "Title": "Certification Manager",
        "Email": "mike.child@example.com",
        "Phone": "555-555-5555",
    }
    mary = {
        "Id": "contact-mary",
        "AccountId": "account-1",
        "FirstName": "Mary",
        "LastName": "Martin",
        "Title": "Quality Director",
        "Email": "mary@example.com",
        "Phone": "312.555.0100",
    }
    client = FakeClient(
        source=source_record(
            Cert_First_Name__c="Mary",
            Cert_Last_Name__c="Martin",
            Cert_Title__c="Quality Director",
            Cert_Email__c="mary@example.com",
            Cert_Phone__c="312.555.0100",
        ),
        children=[child],
        contacts=[child_mike, mary],
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        input_fn=Feeder(["apply automatically", "yes"]),
        output_fn=lambda message: None,
        now=NOW,
    )

    result = processor.review(
        [
            staged_row(
                is_parent_account="true",
                certification_first_name="Mary",
                certification_last_name="Martin",
                certification_title="Quality Director",
                certification_email="mary@example.com",
                certification_phone="312.555.0100",
            )
        ],
        tmp_path,
    )

    assert (
        "Account",
        "child-1",
        {"Cert_Certification_Contact__c": "contact-mary"},
    ) in client.updated
    response = result.response_path.read_text(encoding="utf-8")
    assert (
        "Replaces Mike Child, Certification Manager, "
        "mike.child@example.com, 555-555-5555"
    ) in response
    assert "Replaces Old Contact" not in response


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
    assert (
        "Thank you for updating your information with AISC. The changes are "
        "summarized below. An updated Participant Portal login will be sent by a "
        "separate email, if needed. Unless otherwise noted, previous contacts will "
        "remain in the Acme Steel contact list."
        in emails["first@example.com"]
    )
    assert "Company Name: Acme Steel" in emails["first@example.com"]
    assert "Billing City: Chicago" in emails["second@example.com"]
    assert all("\x1b[" not in body for body in emails.values())


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
    queue = json.loads((tmp_path / "review_queue.json").read_text(encoding="utf-8"))
    failed = next(
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] == "Name"
    )
    assert failed["status"] == "failed"


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
    queue = json.loads((tmp_path / "review_queue.json").read_text(encoding="utf-8"))
    interrupted = next(
        change
        for batch in queue["batches"]
        for queued_row in batch["rows"]
        for change in queued_row["changes"]
        if change["field"] == "Name"
    )
    assert interrupted["status"] == "failed"


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
