"""Interactive, audited processing for staged Profile Update submissions."""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

from .contact_normalization import normalize_contact_value
from .contact_resolution import (
    ContactResolution,
    ContactResolutionClassification,
    ContactSource,
    contact_snapshot,
    family_account_ids,
    normalize_email,
    resolve_contact,
)
from .profile_updates import AutomationCounts, escape_soql_string
from .queried_fields import (
    ACCOUNT_HISTORY_FIELDS,
    ACCOUNT_REVIEW_FIELDS,
    CONTACT_REVIEW_FIELDS,
    SUBMISSION_FIELDS,
)
from .review_queue import (
    QueueBlocker,
    QueuePhase,
    QueueStatus,
    ReviewQueueManifest,
    ReviewQueueStore,
    build_review_queue,
    iter_changes,
    stable_queue_id,
)
from .review_ui import (
    AccountHistory,
    AcknowledgementAnswer,
    AcknowledgementQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    ConflictCandidate,
    ContactCard,
    ContactComparison,
    ContactComparisonRow,
    ContactFieldConflict,
    ContextLine,
    FreeTextAnswer,
    FreeTextQuestion,
    Heading,
    MappingComparison,
    MappingComparisonRow,
    Notice,
    ParentAccountChildValue,
    ParentAccountConflict,
    ParentAccountFieldConflict,
    ParentAccountNoActiveChildren,
    ResponseEmail,
    ReviewChoice,
    ReviewEvent,
    ReviewQueueSnapshot,
    ReviewUI,
    ScalarComparison,
    StagedRowSummary,
    StyledText,
    UnsupportedReviewInteractionError,
    ValidationFeedback,
    ValueFragment,
    styled,
)
from .salesforce import SalesforceClient, SalesforceError
from .salesforce_enums import CaseStatus, ProfileChangeStatus
from .stage_profile_updates import (
    ACTIVE_CERTIFICATION_STATUSES,
    CSV_COLUMNS,
    ROLE_DEFINITIONS,
    ProfileUpdateStagingService,
    RoleDefinition,
    StagingResult,
    write_staged_profile_updates,
)

CHICAGO = ZoneInfo("America/Chicago")
STAGE_SEPARATOR = "=" * 72
ITEM_SEPARATOR = "-" * 72

ACCOUNT_PROPOSALS = [
    ("revised_company_name", "Revised_Company_Name__c", "Name", "Company Name"),
    (
        "revised_company_owner",
        "Revised_Company_Owner__c",
        "Company_Owner__c",
        "Company Owner",
    ),
    (
        "revised_facility_street",
        "Revised_Facility_Street__c",
        "BillingStreet",
        "Billing Street",
    ),
    (
        "revised_facility_city",
        "Revised_Facility_City__c",
        "BillingCity",
        "Billing City",
    ),
    (
        "revised_facility_state",
        "Revised_Facility_State__c",
        "BillingState",
        "Billing State",
    ),
    (
        "revised_facility_zip",
        "Revised_Facility_Zip__c",
        "BillingPostalCode",
        "Billing ZIP",
    ),
    (
        "revised_facility_country",
        "Revised_Facility_Country__c",
        "BillingCountry",
        "Billing Country",
    ),
]

CONTACT_SUFFIX_FIELDS = [
    ("first_name", "FirstName", "First Name"),
    ("last_name", "LastName", "Last Name"),
    ("title", "Title", "Title"),
    ("email", "Email", "Email"),
    ("phone", "Phone", "Phone"),
]

CONTACT_FIELD_LABELS = {
    "AccountId": "Account ID",
    **{
        contact_field: field_label
        for _, contact_field, field_label in CONTACT_SUFFIX_FIELDS
    },
}

ACCOUNT_EMAIL_OPENING = (
    "Thank you for updating your information with AISC. The changes are "
    "summarized below. An updated Participant Portal login will be sent by a "
    "separate email, if needed. Unless otherwise noted, previous contacts will "
    "remain in the {account_name} contact list."
)


class ProcessingError(RuntimeError):
    """The interactive workflow could not finish safely."""


class ProcessingInterrupted(ProcessingError):
    """The reviewer interrupted processing before the batch was finalized."""


class _ParentPreflightInterrupted(ProcessingInterrupted):
    """The reviewer interrupted before a blocked Parent batch made any write."""


class ProcessingStoppedEarly(Exception):
    """The reviewer deliberately stopped before the current row was reviewed."""


class ReviewDecision(StrEnum):
    """The only decisions allowed for a real Salesforce change."""

    APPLY_AUTOMATICALLY = "apply automatically"
    MAKE_MANUALLY = "make manually"
    WILL_NOT_BE_MADE = "will not be made"


class ActionStatus(StrEnum):
    """Durable outcomes written to the JSON Lines audit."""

    APPLIED = "applied"
    VERIFIED_MANUAL = "verified manually"
    REJECTED = "rejected"
    NOOP = "no-op"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    STOPPED_EARLY = "stopped early"
    DEFERRED_MANUAL = "deferred manual follow-up"


@dataclass(frozen=True)
class ChangeProposal:
    """One proposed field or record change shown to the reviewer."""

    source_submission_ids: tuple[str, ...]
    case_id: str
    account_id: str
    account_name: str
    submitter_email: str
    target_object: str
    target_record_id: str
    field_name: str
    label: str
    original_value: Any
    proposed_value: Any
    case_number: str = ""
    context: str = ""
    warnings: str = ""
    classification: str = ""
    comparison_key: str = ""
    candidates: tuple[dict[str, Any], ...] = ()
    selected_contact: dict[str, Any] | None = None
    reason: str = ""
    confidence: str = ""


@dataclass(frozen=True)
class ActionResult:
    """The reviewer decision and the resulting Salesforce outcome."""

    proposal: ChangeProposal
    decision: ReviewDecision | None
    status: ActionStatus
    action: str = ""
    error: str = ""
    error_code: str = ""
    salesforce_message: str = ""


@dataclass
class CaseBatch:
    """All staged rows that belong to one Account and one Case."""

    account_id: str
    case_id: str
    case_number: str
    rows: list[dict[str, str]]
    earliest_submission: datetime
    earliest_key_update: datetime | None = None

    @property
    def source_submission_ids(self) -> tuple[str, ...]:
        """Return source IDs in stable first-seen order."""
        return tuple(
            dict.fromkeys(
                source_id
                for row in self.rows
                for source_id in _json_string_list(row["source_submission_ids"])
            )
        )


@dataclass(frozen=True)
class _ParentRouting:
    """Fresh one-level Account routing selected before a Case batch writes."""

    submitted_account: dict[str, Any]
    direct_children: tuple[dict[str, Any], ...]
    target_accounts: tuple[dict[str, Any], ...]
    conflicts: tuple[ParentAccountFieldConflict, ...] = ()

    @property
    def is_parent(self) -> bool:
        return bool(self.direct_children)

    @property
    def blocked(self) -> bool:
        return self.is_parent and (not self.target_accounts or bool(self.conflicts))


@dataclass(frozen=True)
class ProcessingResult:
    """Artifacts and counts returned after interactive review."""

    staging_path: Path
    audit_path: Path
    response_path: Path
    queue_path: Path
    completed_batches: int
    pending_batches: int
    stopped_early: bool = False


@dataclass(frozen=True)
class _RoleResponse:
    """One completed submitted role, consolidated for response-email text."""

    account_name: str
    submitter_email: str
    label: str
    contact_details: str
    previous_details: str
    changed: bool


@dataclass
class _ResolvedContact:
    """The runtime outcome for one batch-wide email resolution."""

    resolution: ContactResolution
    contact_id: str = ""
    ignored: bool = False
    contact_results: list[ActionResult] | None = None


@dataclass
class _ContactWorkItem:
    """Fresh proposals that resolve to one Salesforce Contact."""

    key: str
    resolution: ContactResolution
    row: dict[str, str]
    proposals: dict[str, dict[str, list[ContactSource]]]
    source_keys: set[tuple[str, str, str]]
    contact_id: str = ""
    ignored: bool = False
    original_contact: dict[str, Any] | None = None
    current_contact: dict[str, Any] | None = None
    reconciled: dict[str, str] | None = None
    write_values: dict[str, str] | None = None
    decision: ReviewDecision | None = None
    result: ActionResult | None = None

    @property
    def sources(self) -> list[ContactSource]:
        """Return every source once, preserving collection order."""
        unique: dict[tuple[str, str, str], ContactSource] = {}
        for values in self.proposals.values():
            for proposal_sources in values.values():
                for source in proposal_sources:
                    unique.setdefault(
                        (source.kind, source.role, source.submission_id), source
                    )
        return list(unique.values())

    @property
    def source_submission_ids(self) -> tuple[str, ...]:
        """Return submission IDs represented by this Contact."""
        return tuple(
            dict.fromkeys(
                source.submission_id for source in self.sources if source.submission_id
            )
        )


class _QueuePublishingClient:
    """Forward Salesforce calls and bracket every mutation with a snapshot."""

    _MUTATIONS = {"create_record", "update_record", "post_feed_message"}

    def __init__(self, client: Any, publish: Callable[[], None]):
        self._client = client
        self._publish = publish

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if name not in self._MUTATIONS or not callable(attribute):
            return attribute

        def mutation(*args: Any, **kwargs: Any) -> Any:
            self._publish()
            try:
                return attribute(*args, **kwargs)
            finally:
                self._publish()

        return mutation


class ProfileUpdateProcessingWorkflow:
    """Run Case preparation, staging, disk validation, then interactive review."""

    def __init__(
        self,
        case_service: Any,
        staging_service: ProfileUpdateStagingService,
        processor: InteractiveProfileUpdateProcessor,
        *,
        staging_writer: Callable[
            [list[dict[str, str]], Path], Path
        ] = write_staged_profile_updates,
        output_fn: Callable[[str], None] = print,
    ):
        self.case_service = case_service
        self.staging_service = staging_service
        self.processor = processor
        self.staging_writer = staging_writer
        self.output_fn = output_fn

    def run(self, output_dir: Path) -> ProcessingResult | Any:
        """Execute setup in the required order and review the published CSV."""
        prepare_queue = getattr(self.processor, "prepare_review_queue", None)
        if prepare_queue is not None:
            return self._run_with_preflight_queue(output_dir, prepare_queue)

        # Compatibility path for small injected processors that predate the
        # queue interface.  The production processor always uses the preflight
        # path above.
        resolve_accounts = getattr(
            self.processor, "resolve_missing_submission_accounts", None
        )
        if resolve_accounts is not None:
            self.output_fn(_section_heading("Resolving Submission Accounts"))
            repaired = resolve_accounts()
            self.output_fn(
                f"Submission Account resolution complete: {repaired} repaired."
            )

        self.output_fn(_section_heading("Preparing Profile Update Cases"))
        counts: AutomationCounts = self.case_service.run()
        if counts.failed:
            details = "; ".join(getattr(self.case_service, "errors", []))
            suffix = f": {details}" if details else ""
            raise ProcessingError(
                f"{counts.failed} required Case operation(s) failed{suffix}"
            )
        self.output_fn("Case preparation complete.")

        self.output_fn(_section_heading("Staging Profile Updates"))
        staged: StagingResult = self.staging_service.stage()
        self.output_fn(f"Staging complete: {len(staged.rows)} row(s).")

        self.output_fn(_section_heading("Publishing staging CSV"))
        staging_path = self.staging_writer(staged.rows, output_dir)
        csv_path = staging_path / "profile_updates.csv"
        self.output_fn(f"Staging CSV published: {csv_path}")

        self.output_fn(_section_heading("Validating published staging CSV"))
        rows = read_staged_profile_updates(csv_path)
        self.output_fn(f"Staging CSV validated: {len(rows)} row(s).")

        self.output_fn(_section_heading("Starting interactive review"))
        result = self.processor.review(rows, staging_path)
        if isinstance(result, ProcessingResult):
            return ProcessingResult(
                staging_path=staging_path,
                audit_path=result.audit_path,
                response_path=result.response_path,
                queue_path=result.queue_path,
                completed_batches=result.completed_batches,
                pending_batches=result.pending_batches,
                stopped_early=result.stopped_early,
            )
        return result

    def _run_with_preflight_queue(
        self,
        output_dir: Path,
        prepare_queue: Callable[[list[dict[str, str]], Path], Any],
    ) -> ProcessingResult | Any:
        """Publish a complete queue before the first question or write."""
        self.output_fn(_section_heading("Read-only Profile Update preflight"))
        staged: StagingResult = self.staging_service.stage()
        self.output_fn(f"Preflight staging complete: {len(staged.rows)} row(s).")

        self.output_fn(_section_heading("Publishing preflight artifacts"))
        staging_path = self.staging_writer(staged.rows, output_dir)
        csv_path = staging_path / "profile_updates.csv"
        rows = read_staged_profile_updates(csv_path)
        prepare_queue(rows, staging_path)
        self.output_fn(
            f"Preflight queue published: {staging_path / 'review_queue.json'}"
        )

        resolve_accounts = getattr(
            self.processor, "resolve_missing_submission_accounts", None
        )
        if resolve_accounts is not None:
            self.processor.transition_setup(
                "Company_Profile_Change__c", "Account__c", QueueStatus.IN_PROGRESS
            )
            try:
                repaired = resolve_accounts()
            except Exception:
                self.processor.transition_setup(
                    "Company_Profile_Change__c", "Account__c", QueueStatus.FAILED
                )
                raise
            self.processor.transition_setup(
                "Company_Profile_Change__c",
                "Account__c",
                QueueStatus.COMPLETED,
                outcome=ActionStatus.APPLIED.value,
            )
            self.output_fn(
                f"Submission Account resolution complete: {repaired} repaired."
            )

        self.output_fn(_section_heading("Preparing Profile Update Cases"))
        self.processor.transition_setup("Case", "Case", QueueStatus.IN_PROGRESS)
        try:
            counts: AutomationCounts = self._run_case_service_with_snapshots()
        except Exception:
            self.processor.transition_setup("Case", "Case", QueueStatus.FAILED)
            raise
        if counts.failed:
            self.processor.transition_setup("Case", "Case", QueueStatus.FAILED)
            details = "; ".join(getattr(self.case_service, "errors", []))
            suffix = f": {details}" if details else ""
            raise ProcessingError(
                f"{counts.failed} required Case operation(s) failed{suffix}"
            )
        self.processor.transition_setup(
            "Case",
            "Case",
            QueueStatus.COMPLETED,
            outcome=ActionStatus.APPLIED.value,
        )
        self.output_fn("Case preparation complete.")

        self.output_fn(_section_heading("Refreshing staged Profile Updates"))
        refreshed: StagingResult = self.staging_service.stage()
        _replace_staged_profile_updates(csv_path, refreshed.rows)
        rows = read_staged_profile_updates(csv_path)
        self.processor.refresh_review_queue(rows)
        self.output_fn(f"Staging refresh complete: {len(rows)} row(s).")

        self.output_fn(_section_heading("Starting interactive review"))
        result = self.processor.review(rows, staging_path)
        if isinstance(result, ProcessingResult):
            return replace(
                result,
                staging_path=staging_path,
            )
        return result

    def _run_case_service_with_snapshots(self) -> AutomationCounts:
        """Observe writes made inside the separate Case preparation service."""
        client = getattr(self.case_service, "client", None)
        publish = getattr(self.processor, "_publish_queue_snapshot", None)
        if client is None or publish is None:
            return self.case_service.run()
        self.case_service.client = _QueuePublishingClient(client, publish)
        try:
            return self.case_service.run()
        finally:
            self.case_service.client = client


def read_staged_profile_updates(path: Path) -> list[dict[str, str]]:
    """Read and validate the exact CSV that was published for review."""
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        missing = [
            column for column in CSV_COLUMNS if column not in (reader.fieldnames or [])
        ]
        if missing:
            raise ProcessingError(
                "Staging CSV is missing required columns: " + ", ".join(missing)
            )
        rows = list(reader)
    for number, row in enumerate(rows, start=2):
        try:
            source_ids = _json_string_list(row["source_submission_ids"])
        except (TypeError, ValueError) as error:
            raise ProcessingError(
                f"Staging CSV row {number} has invalid source submission IDs."
            ) from error
        if not source_ids:
            raise ProcessingError(
                f"Staging CSV row {number} has no source submission IDs."
            )
        contact_resolutions = row.get("contact_resolutions", "").strip()
        if contact_resolutions:
            try:
                raw_resolutions = json.loads(contact_resolutions)
                if not isinstance(raw_resolutions, list):
                    raise TypeError
                for raw_resolution in raw_resolutions:
                    if not isinstance(raw_resolution, dict):
                        raise TypeError
                    ContactResolution.from_dict(raw_resolution)
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise ProcessingError(
                    f"Staging CSV row {number} has invalid Contact resolutions."
                ) from error
    return rows


def _replace_staged_profile_updates(path: Path, rows: list[dict[str, str]]) -> None:
    """Atomically refresh the CSV inside an already-published run folder."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_case_batches(
    rows: list[dict[str, str]], *, now: datetime | None = None
) -> list[CaseBatch]:
    """Group rows by Account/Case and put overdue Key Updates first."""
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        case_id = row.get("case_id", "").strip()
        match_status = row.get("case_match_status", "").strip()
        if not case_id or match_status != "matched":
            names = row.get("source_submission_names", "")
            raise ProcessingError(
                f"Staging row {names} has a blocking Case match ({match_status or 'missing'})."
            )
        account_id = row.get("account_id", "").strip()
        if not account_id:
            raise ProcessingError(
                f"Case {case_id} has a staging row without an Account."
            )
        grouped.setdefault((account_id, case_id), []).append(row)

    batches: list[CaseBatch] = []
    for (account_id, case_id), batch_rows in grouped.items():
        batch_rows.sort(
            key=lambda row: (
                _required_datetime(row.get("earliest_submission_date", "")),
                tuple(sorted(_json_string_list(row["source_submission_ids"]))),
                stable_queue_id(
                    "staged_row",
                    object_name="Company_Profile_Change__c",
                    source_submission_ids=tuple(
                        _json_string_list(row["source_submission_ids"])
                    ),
                    target_context=f"account:{account_id}|case:{case_id}",
                ),
            )
        )
        earliest_submission = min(
            _required_datetime(row.get("earliest_submission_date", ""))
            for row in batch_rows
        )
        key_dates = [
            _required_datetime(row["earliest_key_update_date"])
            for row in batch_rows
            if row.get("has_key_updates") == "true"
            and row.get("earliest_key_update_date", "").strip()
        ]
        batches.append(
            CaseBatch(
                account_id=account_id,
                case_id=case_id,
                case_number=next(
                    (
                        row.get("case_number", "").strip()
                        for row in batch_rows
                        if row.get("case_number", "").strip()
                    ),
                    "",
                ),
                rows=batch_rows,
                earliest_submission=earliest_submission,
                earliest_key_update=min(key_dates) if key_dates else None,
            )
        )

    current = _aware_datetime(now or datetime.now(UTC))
    overdue_before = current - timedelta(days=7)
    batches.sort(
        key=lambda batch: (
            0
            if batch.earliest_key_update is not None
            and batch.earliest_key_update < overdue_before
            else 1,
            batch.earliest_submission,
            batch.account_id,
            batch.case_id,
            stable_queue_id(
                "case_batch",
                object_name="Case",
                source_submission_ids=batch.source_submission_ids,
                target_context=(f"account:{batch.account_id}|case:{batch.case_id}"),
            ),
        )
    )
    return batches


class _AuditWriter:
    def __init__(self, path: Path, now: Callable[[], datetime]):
        self.path = path
        self.now = now
        self.output: TextIO | None = None

    def __enter__(self) -> _AuditWriter:
        self.output = self.path.open("a", encoding="utf-8")
        return self

    def __exit__(self, *args: Any) -> None:
        if self.output is not None:
            self.output.close()

    def append(self, result: ActionResult) -> None:
        if self.output is None:
            raise RuntimeError("Audit writer is not open.")
        proposal = result.proposal
        entry = {
            "source_submission_ids": list(proposal.source_submission_ids),
            "case_id": proposal.case_id,
            "case_number": proposal.case_number,
            "account_id": proposal.account_id,
            "submitter_email": proposal.submitter_email,
            "target_object": proposal.target_object,
            "target_record_id": proposal.target_record_id,
            "field": proposal.field_name,
            "label": proposal.label,
            "original_value": proposal.original_value,
            "proposed_value": proposal.proposed_value,
            "context": proposal.context,
            "warnings": proposal.warnings,
            "classification": proposal.classification,
            "comparison_key": proposal.comparison_key,
            "candidates": list(proposal.candidates),
            "selected_contact": proposal.selected_contact,
            "reason": proposal.reason,
            "confidence": proposal.confidence,
            "decision": result.decision.value if result.decision else "",
            "action": result.action,
            "result": result.status.value,
            "error": result.error,
            "error_code": result.error_code,
            "salesforce_message": result.salesforce_message,
            "timestamp": self.now().astimezone(UTC).isoformat(),
        }
        self.output.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        self.output.flush()
        os.fsync(self.output.fileno())


class _ResponseWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.touch()

    def append(self, case_id: str, email: str, text: str) -> None:
        with self.path.open("a", encoding="utf-8") as output:
            output.write(f"Case {case_id}\nTo: {email}\n\n{text}\n\n")
            output.flush()
            os.fsync(output.fileno())


class InteractiveProfileUpdateProcessor:
    """Review each fresh Salesforce value and audit every decision immediately."""

    def __init__(
        self,
        client: SalesforceClient,
        ui: ReviewUI | None = None,
        *,
        input_fn: Callable[[str], str] | None = None,
        output_fn: Callable[[str], None] | None = None,
        now: datetime | None = None,
    ):
        self.client = client
        if ui is None:
            # Kept as a compatibility bridge for callers of the old constructor.
            # The application entry point constructs this adapter explicitly.
            from .cli_review_ui import CLIReviewUI

            ui = CLIReviewUI(
                input_fn=input_fn or input,
                output_fn=output_fn or print,
            )
        elif input_fn is not None or output_fn is not None:
            raise TypeError("Pass either ui or input_fn/output_fn, not both.")
        self.ui = ui
        self.now = _aware_datetime(now or datetime.now(UTC))
        self._audit: _AuditWriter | None = None
        self._queue_store: ReviewQueueStore | None = None
        self._active_change_id: str | None = None
        self._review_rows: list[dict[str, str]] | None = None

    def prepare_review_queue(
        self, rows: list[dict[str, str]], artifact_dir: Path
    ) -> ReviewQueueManifest:
        """Build and publish the initial queue before any question or write."""
        manifest = build_review_queue(rows, now=self.now)
        self._queue_store = ReviewQueueStore(
            artifact_dir / "review_queue.json", manifest
        )
        self._publish_queue_snapshot()
        return self._queue_store.manifest

    def refresh_review_queue(self, rows: list[dict[str, str]]) -> ReviewQueueManifest:
        """Refresh setup references while retaining stable item outcomes."""
        if self._queue_store is None:
            raise RuntimeError("Review queue has not been prepared.")
        self._queue_store.refresh(build_review_queue(rows, now=self.now))
        self._display_event(ReviewQueueSnapshot(self._queue_store.manifest))
        return self._queue_store.manifest

    def transition_setup(
        self,
        object_name: str,
        field: str,
        status: QueueStatus,
        *,
        outcome: str | None = None,
    ) -> None:
        """Transition every matching setup item and publish each snapshot."""
        if self._queue_store is None:
            return
        item_ids = [
            change.id
            for change in iter_changes(self._queue_store.manifest)
            if change.phase is QueuePhase.SETUP
            and change.salesforce.object_name == object_name
            and change.field == field
        ]
        for item_id in item_ids:
            self._queue_store.transition(item_id, status, outcome=outcome)
        if item_ids:
            self._display_event(ReviewQueueSnapshot(self._queue_store.manifest))

    def _publish_queue_snapshot(self) -> None:
        """Persist and send the complete model through the UI-neutral boundary."""
        if self._queue_store is None:
            return
        self._queue_store.publish()
        self._display_event(ReviewQueueSnapshot(self._queue_store.manifest))

    def _update_record(
        self, object_name: str, record_id: str, values: dict[str, Any]
    ) -> None:
        """Persist queue snapshots immediately around a Salesforce update."""
        self._publish_queue_snapshot()
        try:
            self.client.update_record(object_name, record_id, values)
        finally:
            self._publish_queue_snapshot()

    def _create_record(self, object_name: str, values: dict[str, Any]) -> str:
        """Persist queue snapshots immediately around a Salesforce create."""
        self._publish_queue_snapshot()
        try:
            return self.client.create_record(object_name, values)
        finally:
            self._publish_queue_snapshot()

    def _display_event(self, event: ReviewEvent) -> None:
        """Send a typed event to the injected renderer."""
        self.ui.display(event)

    def _ask_choice(self, question: ChoiceQuestion) -> str:
        """Ask a choice question and reject a mismatched UI answer."""
        self._publish_queue_snapshot()
        answer = self.ui.ask(question)
        if not isinstance(answer, ChoiceAnswer):
            raise UnsupportedReviewInteractionError(
                f"ChoiceQuestion requires ChoiceAnswer, got {type(answer).__name__}."
            )
        allowed = {choice.key: choice for choice in question.choices}
        if answer.choice.key not in allowed:
            raise UnsupportedReviewInteractionError(
                f"ChoiceAnswer key {answer.choice.key!r} is not available."
            )
        return answer.choice.key

    def _ask_free_text(self, question: FreeTextQuestion) -> str:
        """Ask a free-text question and reject a mismatched UI answer."""
        self._publish_queue_snapshot()
        answer = self.ui.ask(question)
        if not isinstance(answer, FreeTextAnswer):
            raise UnsupportedReviewInteractionError(
                f"FreeTextQuestion requires FreeTextAnswer, got {type(answer).__name__}."
            )
        return answer.text

    def _acknowledge(self, question: AcknowledgementQuestion) -> None:
        """Pause for acknowledgement and reject a mismatched UI answer."""
        self._publish_queue_snapshot()
        answer = self.ui.ask(question)
        if not isinstance(answer, AcknowledgementAnswer):
            raise UnsupportedReviewInteractionError(
                "AcknowledgementQuestion requires AcknowledgementAnswer, got "
                f"{type(answer).__name__}."
            )

    def resolve_missing_submission_accounts(self) -> int:
        """Let the reviewer repair blank Account lookups before Case creation."""
        submissions = self.client.query_records(
            "Company_Profile_Change__c",
            ["Id", "Name", "CreatedDate", "Account__c", "Certification_ID__c"],
            where=(f"Status__c = '{ProfileChangeStatus.NEW}' AND Account__c = NULL"),
            order_by="CreatedDate ASC, Id ASC",
        )
        repaired = 0
        for submission in submissions:
            submission_id = _required_record_text(submission, "Id", "Profile Update ID")
            submission_name = _display(submission.get("Name") or submission_id)
            profile_id = _display(submission.get("Certification_ID__c")).strip()
            account = self._choose_submission_account(submission_name, profile_id)
            account_id = _required_record_text(account, "Id", "Account ID")
            self._update_record(
                "Company_Profile_Change__c",
                submission_id,
                {"Account__c": account_id},
            )
            repaired += 1
            self._display_event(
                Notice(
                    styled(
                        "Assigned Profile Update ",
                        ValueFragment(submission_name),
                        " to ",
                        ValueFragment(_display(account.get("Name")) or account_id),
                        ".",
                    )
                )
            )
        return repaired

    def _choose_submission_account(
        self, submission_name: str, profile_id: str
    ) -> dict[str, Any]:
        """Choose an Account, defaulting to the first Profile ID match."""
        while True:
            if not profile_id:
                profile_id = self._ask_free_text(
                    FreeTextQuestion(
                        styled(
                            "Profile Update ",
                            ValueFragment(submission_name),
                            " has no Submission Account. Enter its Profile ID: ",
                        )
                    )
                ).strip()
                if not profile_id:
                    self._display_event(
                        ValidationFeedback(styled("Profile ID cannot be blank."))
                    )
                    continue

            accounts = self.client.query_records(
                "Account",
                ["Id", "Name", "Certification_ID__c"],
                where=(f"Certification_ID__c = '{escape_soql_string(profile_id)}'"),
                order_by="Name ASC, Id ASC",
            )
            accounts = [account for account in accounts if _display(account.get("Id"))]
            if not accounts:
                self._display_event(
                    ValidationFeedback(
                        styled(
                            "No Account was found for Profile ID ",
                            ValueFragment(profile_id),
                            ".",
                        )
                    )
                )
                profile_id = ""
                continue

            choices = tuple(
                ReviewChoice(
                    str(index),
                    _account_choice_label(account),
                )
                for index, account in enumerate(accounts, start=1)
            ) + (
                ReviewChoice(
                    "different_profile_id",
                    "Use a different Profile ID",
                    ("p",),
                ),
            )
            prompt_fragments: list[str | ValueFragment] = [
                "Choose the Submission Account for Profile Update ",
                ValueFragment(submission_name),
                ":\n",
            ]
            for choice in choices:
                marker = "P" if choice.key == "different_profile_id" else choice.key
                prompt_fragments.extend(
                    (f"{marker}. ", ValueFragment(choice.label), "\n")
                )
            prompt_fragments.append("Selection (default 1): ")
            selected = self._ask_choice(
                ChoiceQuestion(
                    styled(*prompt_fragments),
                    choices,
                    styled("Enter an Account number or P for a different Profile ID."),
                    default_key="1",
                )
            )
            if selected == "different_profile_id":
                profile_id = ""
                continue
            return accounts[int(selected) - 1]

    def review(
        self, rows: list[dict[str, str]], artifact_dir: Path
    ) -> ProcessingResult:
        """Review all rows and return interruption-safe local artifacts."""
        artifact_dir.mkdir(parents=True, exist_ok=True)
        audit_path = artifact_dir / "review_audit.jsonl"
        response_path = artifact_dir / "response_emails.txt"
        queue_path = artifact_dir / "review_queue.json"
        if self._queue_store is None:
            # Direct callers receive the same public contract as the workflow.
            self.prepare_review_queue(rows, artifact_dir)
            for object_name, field in (
                ("Company_Profile_Change__c", "Account__c"),
                ("Case", "Case"),
            ):
                self.transition_setup(
                    object_name,
                    field,
                    QueueStatus.COMPLETED,
                    outcome=ActionStatus.NOOP.value,
                )
        response_writer = _ResponseWriter(response_path)
        self._review_rows = rows
        batches = build_case_batches(rows, now=self.now)
        completed = 0
        pending = 0
        stopped_early = False
        try:
            with _AuditWriter(audit_path, lambda: datetime.now(UTC)) as audit:
                self._audit = audit
                for batch in batches:
                    try:
                        is_pending = self._review_batch(batch, response_writer)
                    except ProcessingStoppedEarly:
                        self._append_batch_event(
                            batch,
                            ActionStatus.STOPPED_EARLY,
                            action="reviewer requested safe stop",
                            error="",
                        )
                        self._keep_case_pending(batch)
                        pending += len(batches) - completed
                        stopped_early = True
                        break
                    except _ParentPreflightInterrupted:
                        # The Case and submissions were already open, and parent
                        # preflight deliberately runs before any Salesforce write.
                        raise
                    except ProcessingInterrupted:
                        self._keep_case_pending(batch)
                        raise
                    except (KeyboardInterrupt, EOFError) as error:
                        self._append_batch_event(
                            batch,
                            ActionStatus.INTERRUPTED,
                            action="review batch",
                            error="Reviewer interrupted processing.",
                        )
                        self._keep_case_pending(batch)
                        raise ProcessingInterrupted(
                            "Profile Update review was interrupted."
                        ) from error
                    except (ProcessingError, SalesforceError) as error:
                        if isinstance(error, SalesforceError):
                            self._append_batch_event(
                                batch,
                                ActionStatus.FAILED,
                                action="review batch",
                                error=str(error),
                            )
                        self._keep_case_pending(batch)
                        if isinstance(error, ProcessingError):
                            raise
                        raise ProcessingError(str(error)) from error
                    completed += 1
                    pending += int(is_pending)
        finally:
            self._audit = None
            self._review_rows = None
        return ProcessingResult(
            staging_path=artifact_dir,
            audit_path=audit_path,
            response_path=response_path,
            queue_path=queue_path,
            completed_batches=completed,
            pending_batches=pending,
            stopped_early=stopped_early,
        )

    def _review_batch(self, batch: CaseBatch, response_writer: _ResponseWriter) -> bool:
        return self._review_resilient_batch(batch, response_writer)

    def _review_resilient_batch(
        self, batch: CaseBatch, response_writer: _ResponseWriter
    ) -> bool:
        """Reconcile Contacts once, then process Accounts and role links."""
        account_name = next(
            (
                row.get("account_name", "").strip()
                for row in batch.rows
                if row.get("account_name", "").strip()
            ),
            batch.account_id,
        )
        self._display_event(
            Heading(
                styled(
                    "Case ",
                    ValueFragment(batch.case_number or batch.case_id),
                    ": ",
                    ValueFragment(account_name),
                ),
                STAGE_SEPARATOR,
            )
        )
        fresh_by_id = self._fresh_case_submissions(batch)
        self._show_case_context(batch, fresh_by_id)
        routing = self._preflight_parent_routing(batch, fresh_by_id)
        if routing.blocked:
            return self._defer_parent_batch(batch, routing)
        self._show_account_history(batch, list(fresh_by_id.values()))

        # Every row checkpoint happens before the first Salesforce write.
        for row in batch.rows:
            self._checkpoint_row(row)

        self._update_status_with_audit(
            batch,
            batch.case_id,
            "Case",
            "Status",
            CaseStatus.PENDING,
            action="prepare batch",
        )

        self._display_event(Heading(styled("Contact Updates"), STAGE_SEPARATOR))
        contact_items, source_mapping = self._collect_contact_work(batch, fresh_by_id)
        contact_items = self._resolve_contact_identities(batch, contact_items)
        contact_items.sort(
            key=lambda item: (
                item.contact_id or item.key,
                tuple(sorted(item.source_submission_ids)),
                stable_queue_id(
                    "contact_work",
                    object_name="Contact",
                    source_submission_ids=item.source_submission_ids,
                    target_context=item.contact_id or item.key,
                ),
            )
        )
        source_mapping = {
            source_key: next(
                (item.key for item in contact_items if source_key in item.source_keys),
                item_key,
            )
            for source_key, item_key in source_mapping.items()
        }
        for item in contact_items:
            self._reconcile_contact_fields(batch, item)
        for item in contact_items:
            self._prepare_contact_decision(batch, item)

        results: list[ActionResult] = []
        for item in contact_items:
            contact_results = self._execute_contact_work(batch, item)
            results.extend(contact_results)

        self._display_event(Heading(styled("Account Updates"), STAGE_SEPARATOR))
        account_results: list[ActionResult] = []
        for target_index, target_account in enumerate(routing.target_accounts):
            target_account_id = _required_record_text(
                target_account, "Id", "Affected Account ID"
            )
            target_account_name = _display(target_account.get("Name"))
            for row in batch.rows:
                fresh_submissions = [
                    fresh_by_id[source_id]
                    for source_id in _json_string_list(row["source_submission_ids"])
                ]
                reviewed = self._review_account_proposals(
                    batch,
                    row,
                    fresh_submissions,
                    target_account_id=target_account_id,
                    target_account_name=target_account_name,
                    parent_routed=routing.is_parent,
                )
                if target_index == 0:
                    account_results.extend(reviewed)
                results.extend(reviewed)

        self._display_event(Heading(styled("Role Links"), STAGE_SEPARATOR))
        role_responses: list[_RoleResponse] = []
        for target_index, target_account in enumerate(routing.target_accounts):
            target_account_id = _required_record_text(
                target_account, "Id", "Affected Account ID"
            )
            target_account_name = _display(target_account.get("Name"))
            for row in batch.rows:
                fresh_submissions = [
                    fresh_by_id[source_id]
                    for source_id in _json_string_list(row["source_submission_ids"])
                ]
                reviewed, responses = self._assign_reconciled_roles(
                    batch,
                    row,
                    fresh_submissions,
                    contact_items,
                    source_mapping,
                    target_account_id=target_account_id,
                    target_account_name=target_account_name,
                    parent_routed=routing.is_parent,
                )
                results.extend(reviewed)
                if target_index == 0:
                    role_responses.extend(responses)

        return self._finish_batch(
            batch,
            response_writer,
            results,
            account_results,
            role_responses,
        )

    def _collect_contact_work(
        self,
        batch: CaseBatch,
        fresh_by_id: dict[str, dict[str, Any]],
    ) -> tuple[
        list[_ContactWorkItem],
        dict[tuple[str, str, str], str],
    ]:
        """Collect only fresh, explicitly submitted Contact values."""
        staged_by_source: dict[tuple[str, str, str], ContactResolution] = {}
        staged_submitter_ids: set[str] = set()
        for row in batch.rows:
            try:
                raw_items = json.loads(row.get("contact_resolutions", "") or "[]")
            except (TypeError, ValueError) as error:
                raise ProcessingError(
                    "A staged contact_resolutions value is not valid JSON."
                ) from error
            if not isinstance(raw_items, list):
                raise ProcessingError(
                    "A staged contact_resolutions value must contain a JSON list."
                )
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    raise ProcessingError(
                        "A staged Contact resolution must be a JSON object."
                    )
                try:
                    resolution = ContactResolution.from_dict(raw_item)
                except (AttributeError, KeyError, TypeError, ValueError) as error:
                    raise ProcessingError(
                        "A staged Contact resolution has invalid fields."
                    ) from error
                has_submitter = False
                for source in resolution.sources:
                    source_key = (source.kind, source.role, source.submission_id)
                    staged_by_source[source_key] = resolution
                    has_submitter = has_submitter or source.kind == "submitter"
                if has_submitter:
                    row_source_ids = _json_string_list(row["source_submission_ids"])
                    staged_submitter_ids.update(row_source_ids)
                    for submission_id in row_source_ids:
                        staged_by_source.setdefault(
                            ("submitter", "", submission_id),
                            resolution,
                        )

        account_fields = [
            "Id",
            "ParentId",
            *(role.account_lookup for role in ROLE_DEFINITIONS),
        ]
        account = self.client.get_record("Account", batch.account_id, account_fields)
        family_ids = self._fresh_family_account_ids(batch)

        occurrences: list[
            tuple[
                ContactSource,
                dict[str, str],
                dict[str, str],
                str,
            ]
        ] = []
        for row in batch.rows:
            for submission_id in _json_string_list(row["source_submission_ids"]):
                record = fresh_by_id[submission_id]
                if submission_id in staged_submitter_ids:
                    name = normalize_contact_value("name", record.get("Name__c"))
                    first_name, last_name = _split_person_name(name)
                    details = {
                        "first_name": first_name,
                        "last_name": last_name,
                        "email": normalize_contact_value(
                            "email", record.get("Email__c")
                        ),
                        "phone": normalize_contact_value(
                            "phone", record.get("Phone__c")
                        ),
                    }
                    details = {
                        field_name: value
                        for field_name, value in details.items()
                        if value
                    }
                    if details:
                        source = ContactSource("submitter", submission_id=submission_id)
                        occurrences.append((source, details, row, ""))

                for role in ROLE_DEFINITIONS:
                    details = {
                        suffix: normalize_contact_value(
                            suffix, record.get(source_field)
                        )
                        for suffix, source_field in role.submitted_fields
                    }
                    details = {
                        field_name: value
                        for field_name, value in details.items()
                        if value
                    }
                    if not details:
                        continue
                    source = ContactSource(
                        "role",
                        role=role.prefix,
                        submission_id=submission_id,
                    )
                    action = row.get(f"{role.prefix}_resolution_action", "").strip()
                    staged_id = row.get(
                        f"{role.prefix}_salesforce_contact_id", ""
                    ).strip()
                    current_id = _display(account.get(role.account_lookup))
                    identity_id = ""
                    # A submitted email identifies the Contact independently of
                    # the role. Only a partial proposal with no email falls back
                    # to the Contact currently linked to that role.
                    if not details.get("email") and action != "create_contact":
                        identity_id = staged_id or current_id
                    staged_resolution = staged_by_source.get(
                        ("role", role.prefix, submission_id)
                    )
                    selected = (
                        staged_resolution.selected_contact
                        if staged_resolution is not None
                        else None
                    )
                    if (
                        not details.get("email")
                        and not identity_id
                        and selected is not None
                    ):
                        identity_id = _display(selected.get("Id"))
                    occurrences.append((source, details, row, identity_id))

        email_identity_ids: dict[str, set[str]] = {}
        for _, details, _, identity_id in occurrences:
            _, comparison_key, _ = normalize_email(details.get("email"))
            if comparison_key and identity_id:
                email_identity_ids.setdefault(comparison_key, set()).add(identity_id)

        grouped: dict[str, _ContactWorkItem] = {}
        source_mapping: dict[tuple[str, str, str], str] = {}
        invalid_index = 0
        for source, details, row, identity_id in occurrences:
            normalized_email, comparison_key, warnings = normalize_email(
                details.get("email")
            )
            linked_ids = email_identity_ids.get(comparison_key, set())
            if not identity_id and len(linked_ids) == 1:
                identity_id = next(iter(linked_ids))
            if identity_id:
                key = f"id:{identity_id}"
            elif comparison_key:
                key = f"email:{comparison_key}"
            else:
                invalid_index += 1
                key = (
                    f"unresolved:{invalid_index}:{source.kind}:"
                    f"{source.role}:{source.submission_id}"
                )
            source_key = (source.kind, source.role, source.submission_id)
            source_mapping[source_key] = key
            item = grouped.get(key)
            if item is None:
                if identity_id:
                    contact = self.client.get_record(
                        "Contact", identity_id, CONTACT_REVIEW_FIELDS
                    )
                    resolution = ContactResolution(
                        ContactResolutionClassification.USE_EXISTING,
                        normalized_email,
                        comparison_key,
                        sources=[source],
                        selected_contact=contact,
                        reason=(
                            "The fresh Salesforce Contact ID identified the "
                            "existing Contact."
                        ),
                        confidence="exact Contact ID",
                        warnings=warnings,
                    )
                else:
                    classification = (
                        ContactResolutionClassification.CREATE_NEW
                        if source.kind == "role"
                        and row.get(f"{source.role}_resolution_action", "").strip()
                        == "create_contact"
                        else ContactResolutionClassification.AMBIGUOUS
                    )
                    resolution = ContactResolution(
                        classification,
                        normalized_email,
                        comparison_key,
                        sources=[source],
                        reason="Fresh Contact proposals require matching.",
                        confidence="unresolved",
                        warnings=warnings,
                    )
                item = _ContactWorkItem(
                    key,
                    resolution,
                    row,
                    proposals={},
                    source_keys=set(),
                    contact_id=identity_id,
                )
                grouped[key] = item
            elif source not in item.resolution.sources:
                item.resolution.sources.append(source)
            item.source_keys.add(source_key)
            for field_name, value in details.items():
                item.proposals.setdefault(field_name, {}).setdefault(value, []).append(
                    source
                )

        contacts: list[dict[str, Any]] = []
        for item in grouped.values():
            if item.contact_id or not item.resolution.normalized_email:
                continue
            contacts.extend(
                self.client.query_records(
                    "Contact",
                    CONTACT_REVIEW_FIELDS,
                    where=(
                        "Email = "
                        f"'{escape_soql_string(item.resolution.normalized_email)}'"
                    ),
                    order_by="Id ASC",
                )
            )
        if family_ids:
            quoted_ids = ", ".join(
                f"'{escape_soql_string(account_id)}'"
                for account_id in sorted(family_ids)
            )
            contacts.extend(
                self.client.query_records(
                    "Contact",
                    CONTACT_REVIEW_FIELDS,
                    where=f"AccountId IN ({quoted_ids})",
                    order_by="AccountId ASC, Id ASC",
                )
            )
        contacts = list(
            {
                _display(contact.get("Id")) or repr(contact): dict(contact)
                for contact in contacts
            }.values()
        )

        for item in grouped.values():
            if item.contact_id:
                continue
            if (
                not item.resolution.comparison_key
                and item.resolution.classification
                is not ContactResolutionClassification.CREATE_NEW
            ):
                item.ignored = True
                item.resolution.reason = (
                    "A partial Contact proposal has no current role Contact ID "
                    "and no valid email."
                )
                item.resolution.warnings.append(
                    "The Contact proposal was ignored because it cannot be "
                    "identified safely."
                )
                continue
            if not item.resolution.comparison_key:
                continue
            submitted = {
                field_name: next(iter(values))
                for field_name, values in item.proposals.items()
                if values
            }
            fresh_resolution = resolve_contact(
                item.resolution.normalized_email,
                contacts,
                family_ids,
                sources=item.resolution.sources,
                submitted=submitted,
            )
            fresh_resolution.warnings = list(
                dict.fromkeys([*item.resolution.warnings, *fresh_resolution.warnings])
            )
            item.resolution = fresh_resolution
        # Keep different email identities separate even when they appeared in
        # the same role. Role assignment happens later, after every submitted
        # Contact has been reviewed and brought up to date.
        return list(grouped.values()), source_mapping

    def _resolve_contact_identities(
        self,
        batch: CaseBatch,
        items: list[_ContactWorkItem],
    ) -> list[_ContactWorkItem]:
        """Finish identity choices and merge items that select the same ID."""
        for item in items:
            if item.ignored:
                self._audit_resolution(
                    batch,
                    item.row,
                    item.resolution,
                    ActionStatus.REJECTED,
                    "ignore Contact without safe identity",
                )
                continue
            state = self._review_resolution_choice(batch, item.row, item.resolution)
            item.contact_id = state.contact_id
            item.ignored = state.ignored

        merged: dict[str, _ContactWorkItem] = {}
        for item in items:
            key = f"id:{item.contact_id}" if item.contact_id else item.key
            existing = merged.get(key)
            if existing is None:
                item.key = key
                merged[key] = item
                continue
            existing.source_keys.update(item.source_keys)
            existing.ignored = existing.ignored and item.ignored
            for source in item.resolution.sources:
                if source not in existing.resolution.sources:
                    existing.resolution.sources.append(source)
            for field_name, values in item.proposals.items():
                for value, sources in values.items():
                    target = existing.proposals.setdefault(field_name, {}).setdefault(
                        value, []
                    )
                    target.extend(source for source in sources if source not in target)
        return list(merged.values())

    def _reconcile_contact_fields(
        self,
        batch: CaseBatch,
        item: _ContactWorkItem,
    ) -> None:
        """Resolve every field conflict without writing to Salesforce."""
        if item.ignored:
            item.reconciled = {}
            item.write_values = {}
            return
        if item.contact_id:
            item.current_contact = self.client.get_record(
                "Contact", item.contact_id, CONTACT_REVIEW_FIELDS
            )
            item.original_contact = dict(item.current_contact)
        else:
            item.current_contact = {}
            item.original_contact = {}

        reconciled: dict[str, str] = {}
        for suffix, contact_field, field_label in CONTACT_SUFFIX_FIELDS:
            values = item.proposals.get(suffix, {})
            if (
                suffix == "email"
                and item.resolution.classification
                is ContactResolutionClassification.LIKELY_TYPO
            ):
                values = {}
            if not values:
                continue
            if len(values) == 1:
                reconciled[suffix] = next(iter(values))
                continue
            current = _display((item.current_contact or {}).get(contact_field))
            self._display_event(
                ContactFieldConflict(
                    field_label,
                    ValueFragment(current),
                    tuple(
                        ConflictCandidate(
                            str(index),
                            ValueFragment(value),
                            ValueFragment(self._format_contact_sources(sources)),
                        )
                        for index, (value, sources) in enumerate(
                            values.items(), start=1
                        )
                    ),
                )
            )
            conflict_sources = "\n".join(
                f"{value}: {self._format_contact_sources(sources)}"
                for value, sources in values.items()
            )
            while True:
                try:
                    answer = self._ask_choice(
                        ChoiceQuestion(
                            styled(
                                "Choose reconciled ",
                                ValueFragment(field_label),
                                f" [1-{len(values)}/current]: ",
                            ),
                            tuple(
                                ReviewChoice(str(index), value)
                                for index, value in enumerate(values, start=1)
                            )
                            + (ReviewChoice("current", "current"),),
                            styled("Choose a candidate number or current."),
                        )
                    )
                except StopIteration as error:
                    proposal = self._contact_proposal(
                        batch,
                        item,
                        field_name=contact_field,
                        label=f"{field_label} conflict",
                        original_value=current,
                        proposed_value="",
                        warnings=conflict_sources,
                    )
                    self._append_audit(
                        ActionResult(
                            proposal,
                            None,
                            ActionStatus.FAILED,
                            action="resolve Contact field conflict",
                            error=(
                                f"Conflicting {field_label} values require an "
                                "explicit choice."
                            ),
                        )
                    )
                    raise ProcessingError(
                        f"Conflicting {field_label} values require an explicit choice."
                    ) from error
                except (KeyboardInterrupt, EOFError) as error:
                    proposal = self._contact_proposal(
                        batch,
                        item,
                        field_name=contact_field,
                        label=f"{field_label} conflict",
                        original_value=current,
                        proposed_value="",
                        warnings=conflict_sources,
                    )
                    self._append_audit(
                        ActionResult(
                            proposal,
                            None,
                            ActionStatus.INTERRUPTED,
                            action="resolve Contact field conflict",
                            error="Reviewer interrupted conflict resolution.",
                        )
                    )
                    raise ProcessingInterrupted(
                        "Profile Update review was interrupted."
                    ) from error
                if answer == "current":
                    chosen = current
                    break
                if answer.isdigit() and 1 <= int(answer) <= len(values):
                    chosen = list(values)[int(answer) - 1]
                    break
            reconciled[suffix] = chosen
            proposal = self._contact_proposal(
                batch,
                item,
                field_name=contact_field,
                label=f"{field_label} conflict",
                original_value=current,
                proposed_value=chosen,
                warnings=conflict_sources,
            )
            self._append_audit(
                ActionResult(
                    proposal,
                    None,
                    ActionStatus.VERIFIED_MANUAL,
                    action="resolve Contact field conflict",
                )
            )

        item.reconciled = reconciled
        current = item.current_contact or {}
        item.write_values = {
            contact_field: reconciled[suffix]
            for suffix, contact_field, _ in CONTACT_SUFFIX_FIELDS
            if suffix in reconciled
            and reconciled[suffix]
            and not _values_equal(current.get(contact_field), reconciled[suffix])
        }

    @staticmethod
    def _format_contact_sources(sources: list[ContactSource]) -> str:
        return ", ".join(
            (
                f"{source.submission_id or '(unknown submission)'} / "
                f"{source.role.replace('_', ' ').title()}"
                if source.kind == "role"
                else f"{source.submission_id or '(unknown submission)'} / Submitter"
            )
            for source in sources
        )

    def _show_reconciled_contact(self, item: _ContactWorkItem) -> None:
        identity = self._contact_review_identity(item)
        current = item.current_contact or {}
        reconciled = item.reconciled or {}
        rows = []
        for suffix, contact_field, field_label in CONTACT_SUFFIX_FIELDS:
            values = item.proposals.get(suffix, {})
            if not values:
                continue
            sources = [
                source
                for proposal_sources in values.values()
                for source in proposal_sources
            ]
            rows.append(
                ContactComparisonRow(
                    field_label,
                    ValueFragment(_display(current.get(contact_field))),
                    ValueFragment(reconciled.get(suffix, "")),
                    ValueFragment(self._format_contact_sources(sources)),
                )
            )
        self._display_event(ContactComparison(ValueFragment(identity), tuple(rows)))

    def _prepare_contact_decision(
        self,
        batch: CaseBatch,
        item: _ContactWorkItem,
    ) -> None:
        """Show one final Contact proposal and collect one A/M/N decision."""
        if item.ignored:
            return
        self._show_reconciled_contact(item)
        payload = dict(item.write_values or {})
        if not item.contact_id:
            payload = {"AccountId": batch.account_id, **payload}
        proposal = self._contact_proposal(
            batch,
            item,
            field_name="Contact",
            label=f"Contact: {self._contact_review_identity(item)}",
            original_value=item.current_contact or {},
            proposed_value=payload,
        )
        if item.contact_id and not item.write_values:
            item.result = ActionResult(
                proposal,
                None,
                ActionStatus.NOOP,
                action="reconciled Contact already current",
            )
            self._append_audit(item.result)
            self._display_event(
                Notice(styled("Contact is already current; no change needed."))
            )
            return
        has_last_name = bool((item.reconciled or {}).get("last_name"))
        if not item.contact_id and not has_last_name:
            self._display_event(
                Notice(
                    styled(
                        "This Contact cannot be created automatically because the "
                        "required Last Name field is missing."
                    )
                )
            )
        self._show_proposal(proposal)
        item.decision = self._review_decision(
            proposal,
            automatic_allowed=bool(item.contact_id)
            or (has_last_name and bool(item.resolution.comparison_key)),
            action="review reconciled Contact",
        )

    def _execute_contact_work(
        self,
        batch: CaseBatch,
        item: _ContactWorkItem,
    ) -> list[ActionResult]:
        """Apply or verify one aggregate Contact decision."""
        if item.ignored:
            return []
        if item.result is not None:
            return [item.result]
        proposal = self._contact_proposal(
            batch,
            item,
            field_name="Contact",
            label=f"Contact: {self._contact_review_identity(item)}",
            original_value=item.current_contact or {},
            proposed_value=(
                dict(item.write_values or {})
                if item.contact_id
                else {"AccountId": batch.account_id, **dict(item.write_values or {})}
            ),
        )
        decision = item.decision
        if decision is ReviewDecision.WILL_NOT_BE_MADE:
            result = ActionResult(
                proposal,
                decision,
                ActionStatus.REJECTED,
                action="no Contact write",
            )
            self._append_audit(result)
            item.result = result
            return [result]
        if decision is ReviewDecision.MAKE_MANUALLY:
            result = self._verify_manual_contact(batch, item, proposal)
        elif item.contact_id:
            try:
                self._update_record(
                    "Contact", item.contact_id, dict(item.write_values or {})
                )
            except SalesforceError as error:
                result = ActionResult(
                    proposal,
                    decision,
                    ActionStatus.FAILED,
                    action="update Contact from reconciled proposals",
                    error=str(error),
                    error_code=error.error_code or "",
                    salesforce_message=error.salesforce_message or "",
                )
                self._append_audit(result)
                raise ProcessingError(str(error)) from error
            result = ActionResult(
                proposal,
                decision,
                ActionStatus.APPLIED,
                action="update Contact from reconciled proposals",
            )
            self._append_audit(result)
        else:
            try:
                contact_id = self._create_record(
                    "Contact", dict(proposal.proposed_value)
                )
            except SalesforceError as error:
                if _is_duplicate_contact_error(error):
                    result = self._recover_duplicate_contact(
                        batch,
                        item.row,
                        proposal,
                        item.resolution,
                        error,
                    )
                    contact_id = result.proposal.target_record_id
                else:
                    result = ActionResult(
                        proposal,
                        decision,
                        ActionStatus.FAILED,
                        action="create Contact",
                        error=str(error),
                        error_code=error.error_code or "",
                        salesforce_message=error.salesforce_message or "",
                    )
                    self._append_audit(result)
                    raise ProcessingError(str(error)) from error
            else:
                created_proposal = ChangeProposal(
                    **{
                        **proposal.__dict__,
                        "target_record_id": contact_id,
                        "selected_contact": contact_snapshot(
                            {"Id": contact_id, **dict(proposal.proposed_value)}
                        ),
                    }
                )
                result = ActionResult(
                    created_proposal,
                    decision,
                    ActionStatus.APPLIED,
                    action="create Contact",
                )
                self._append_audit(result)
            if result.status in {
                ActionStatus.APPLIED,
                ActionStatus.VERIFIED_MANUAL,
            }:
                item.contact_id = contact_id

        item.result = result
        results = [result]
        if result.status in {
            ActionStatus.APPLIED,
            ActionStatus.VERIFIED_MANUAL,
        }:
            item.current_contact = self.client.get_record(
                "Contact", item.contact_id, CONTACT_REVIEW_FIELDS
            )
            item.resolution.selected_contact = item.current_contact
            if (
                not proposal.target_record_id or proposal.target_record_id == "(new)"
            ) and any(source.kind == "submitter" for source in item.sources):
                results.append(
                    self._assign_created_submitter_to_case(
                        batch,
                        item.row,
                        item.resolution,
                        item.contact_id,
                    )
                )
        return results

    def _verify_manual_contact(
        self,
        batch: CaseBatch,
        item: _ContactWorkItem,
        proposal: ChangeProposal,
    ) -> ActionResult:
        """Verify all reconciled Contact fields together."""
        try:
            if item.contact_id:
                self._acknowledge(
                    AcknowledgementQuestion(
                        styled(
                            "Make the complete Contact change in Salesforce, then "
                            "press Enter to verify it: "
                        )
                    )
                )
                contact_id = item.contact_id
            else:
                while True:
                    contact_id = self._ask_free_text(
                        FreeTextQuestion(
                            styled(
                                "Create the Contact in Salesforce, then enter its "
                                "Contact ID: "
                            )
                        )
                    ).strip()
                    if contact_id:
                        break
                    self._display_event(
                        ValidationFeedback(
                            styled("A Contact ID is required for manual verification.")
                        )
                    )
            fresh = self.client.get_record("Contact", contact_id, CONTACT_REVIEW_FIELDS)
        except (KeyboardInterrupt, EOFError) as error:
            result = ActionResult(
                proposal,
                ReviewDecision.MAKE_MANUALLY,
                ActionStatus.INTERRUPTED,
                action="verify reconciled Contact manually",
                error="Reviewer interrupted processing.",
            )
            self._append_audit(result)
            raise ProcessingInterrupted(
                "Profile Update review was interrupted."
            ) from error
        except ProcessingError as error:
            result = ActionResult(
                proposal,
                ReviewDecision.MAKE_MANUALLY,
                ActionStatus.FAILED,
                action="verify reconciled Contact manually",
                error=str(error),
            )
            self._append_audit(result)
            raise
        except SalesforceError as error:
            result = ActionResult(
                proposal,
                ReviewDecision.MAKE_MANUALLY,
                ActionStatus.FAILED,
                action="verify reconciled Contact manually",
                error=str(error),
            )
            self._append_audit(result)
            raise ProcessingError(str(error)) from error
        expected = item.write_values or {}
        if not item.contact_id:
            expected = {
                field_name: value
                for field_name, value in proposal.proposed_value.items()
                if field_name != "AccountId"
            }
        if (
            not item.contact_id
            and not _values_equal(fresh.get("AccountId"), batch.account_id)
        ) or any(
            not _values_equal(fresh.get(field_name), value)
            for field_name, value in expected.items()
        ):
            error = "Salesforce Contact does not match all reconciled proposed fields."
            result = ActionResult(
                proposal,
                ReviewDecision.MAKE_MANUALLY,
                ActionStatus.FAILED,
                action="verify reconciled Contact manually",
                error=error,
            )
            self._append_audit(result)
            raise ProcessingError(error)
        item.contact_id = contact_id
        verified_proposal = ChangeProposal(
            **{
                **proposal.__dict__,
                "target_record_id": contact_id,
                "selected_contact": contact_snapshot(fresh),
            }
        )
        result = ActionResult(
            verified_proposal,
            ReviewDecision.MAKE_MANUALLY,
            ActionStatus.VERIFIED_MANUAL,
            action="verify reconciled Contact manually",
        )
        self._append_audit(result)
        return result

    def _contact_proposal(
        self,
        batch: CaseBatch,
        item: _ContactWorkItem,
        *,
        field_name: str,
        label: str,
        original_value: Any,
        proposed_value: Any,
        warnings: str = "",
    ) -> ChangeProposal:
        """Build an aggregate Contact proposal with every source ID."""
        base = self._proposal(
            batch,
            item.row,
            target_object="Contact",
            target_record_id=item.contact_id or "(new)",
            field_name=field_name,
            label=label,
            original_value=original_value,
            proposed_value=proposed_value,
            resolution=item.resolution,
        )
        return ChangeProposal(
            **{
                **base.__dict__,
                "source_submission_ids": item.source_submission_ids,
                "warnings": "\n".join(
                    value for value in (base.warnings, warnings) if value
                ),
            }
        )

    def _fresh_family_account_ids(self, batch: CaseBatch) -> set[str]:
        target = self.client.get_record("Account", batch.account_id, ["Id", "ParentId"])
        parent_id = _display(target.get("ParentId"))
        family_accounts = [target]
        if parent_id:
            family_accounts.extend(
                self.client.query_records(
                    "Account",
                    ["Id", "ParentId"],
                    where=(
                        f"Id = '{escape_soql_string(parent_id)}' OR "
                        f"ParentId = '{escape_soql_string(parent_id)}'"
                    ),
                    order_by="Id ASC",
                )
            )
        return family_account_ids(target, family_accounts)

    def _review_resolution_choice(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        resolution: ContactResolution,
    ) -> _ResolvedContact:
        """Finish all operator choices without changing Salesforce."""
        if resolution.classification is ContactResolutionClassification.USE_EXISTING:
            self._audit_resolution(
                batch,
                row,
                resolution,
                ActionStatus.NOOP,
                "automatic exact Contact selection",
            )
            return _ResolvedContact(
                resolution,
                contact_id=_display((resolution.selected_contact or {}).get("Id")),
            )

        if resolution.classification is ContactResolutionClassification.CREATE_NEW:
            self._audit_resolution(
                batch,
                row,
                resolution,
                ActionStatus.NOOP,
                "no safe existing Contact match",
            )
            return _ResolvedContact(resolution)

        if resolution.classification is ContactResolutionClassification.LIKELY_TYPO:
            candidate = resolution.selected_contact or resolution.candidates[0]
            self._show_contact_details("Suggested Contact", candidate)
            candidate_email, candidate_key, _ = normalize_email(candidate.get("Email"))
            self._display_event(
                Notice(
                    styled(
                        "Likely email typo: ",
                        ValueFragment(resolution.normalized_email),
                        " was compared as ",
                        ValueFragment(resolution.comparison_key),
                        ". The suggested correction is ",
                        ValueFragment(candidate_email or "(blank)"),
                        " (",
                        ValueFragment(candidate_key or "no valid key"),
                        ").",
                    )
                )
            )
            try:
                confirmed = self._prompt_yes_no(
                    styled("Use this suggested Contact? [yes/no]: ")
                )
            except StopIteration as error:
                raise ProcessingError(
                    "A likely typo Contact requires operator confirmation."
                ) from error
            if confirmed:
                resolution.selected_contact = candidate
                self._audit_resolution(
                    batch,
                    row,
                    resolution,
                    ActionStatus.VERIFIED_MANUAL,
                    "confirm likely typo Contact",
                )
                return _ResolvedContact(
                    resolution, contact_id=_display(candidate.get("Id"))
                )
            resolution.classification = ContactResolutionClassification.AMBIGUOUS
            resolution.reason = "The operator rejected the likely typo suggestion."

        return self._prompt_ambiguous_resolution(batch, row, resolution)

    def _prompt_ambiguous_resolution(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        resolution: ContactResolution,
    ) -> _ResolvedContact:
        self._display_event(
            Heading(
                styled(
                    "Contact resolution for ",
                    ValueFragment(resolution.normalized_email or "(invalid email)"),
                ),
                ITEM_SEPARATOR,
            )
        )
        for index, candidate in enumerate(resolution.candidates, start=1):
            self._show_contact_details(f"Candidate {index}", candidate)
        while True:
            candidate_range = (
                f"1-{len(resolution.candidates)}, " if resolution.candidates else ""
            )
            try:
                answer = self._ask_choice(
                    ChoiceQuestion(
                        styled(f"Contact choice [{candidate_range}create/ignore]: "),
                        tuple(
                            ReviewChoice(str(index), f"Candidate {index}")
                            for index in range(1, len(resolution.candidates) + 1)
                        )
                        + (
                            ReviewChoice("create", "create", ("c",)),
                            ReviewChoice("ignore", "ignore", ("i",)),
                        ),
                        styled("Choose a candidate number, create, or ignore."),
                    )
                )
            except StopIteration as error:
                self._audit_resolution(
                    batch,
                    row,
                    resolution,
                    ActionStatus.FAILED,
                    "resolve ambiguous Contact",
                )
                raise ProcessingError(
                    "Multiple Salesforce Contacts require an explicit "
                    "operator selection."
                ) from error
            if answer == "create":
                resolution.classification = ContactResolutionClassification.CREATE_NEW
                resolution.selected_contact = None
                resolution.reason = "The operator chose to create a new Contact."
                self._audit_resolution(
                    batch,
                    row,
                    resolution,
                    ActionStatus.NOOP,
                    "operator selected create new Contact",
                )
                return _ResolvedContact(resolution)
            if answer == "ignore":
                resolution.selected_contact = None
                resolution.reason = "The operator ignored this email entry."
                self._audit_resolution(
                    batch,
                    row,
                    resolution,
                    ActionStatus.REJECTED,
                    "operator ignored Contact resolution",
                )
                return _ResolvedContact(resolution, ignored=True)
            if answer.isdigit() and 1 <= int(answer) <= len(resolution.candidates):
                selected = resolution.candidates[int(answer) - 1]
                resolution.selected_contact = selected
                resolution.reason = "The operator selected an existing Contact."
                self._audit_resolution(
                    batch,
                    row,
                    resolution,
                    ActionStatus.VERIFIED_MANUAL,
                    "operator selected existing Contact",
                )
                return _ResolvedContact(
                    resolution, contact_id=_display(selected.get("Id"))
                )

    def _audit_resolution(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        resolution: ContactResolution,
        status: ActionStatus,
        action: str,
    ) -> None:
        proposal = self._proposal(
            batch,
            row,
            target_object="Contact",
            target_record_id=_display((resolution.selected_contact or {}).get("Id")),
            field_name="resolution",
            label="Contact resolution",
            original_value="",
            proposed_value=resolution.normalized_email,
            resolution=resolution,
        )
        self._append_audit(ActionResult(proposal, None, status, action=action))

    def _assign_created_submitter_to_case(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        resolution: ContactResolution,
        contact_id: str,
    ) -> ActionResult:
        proposal = self._proposal(
            batch,
            row,
            target_object="Case",
            target_record_id=batch.case_id,
            field_name="ContactId",
            label="Case Submitter Contact",
            original_value="",
            proposed_value=contact_id,
            resolution=resolution,
        )
        try:
            self._update_record("Case", batch.case_id, {"ContactId": contact_id})
        except SalesforceError as error:
            result = ActionResult(
                proposal,
                None,
                ActionStatus.FAILED,
                action="assign created submitter Contact to Case",
                error=str(error),
            )
            self._append_audit(result)
            raise ProcessingError(str(error)) from error
        result = ActionResult(
            proposal,
            None,
            ActionStatus.APPLIED,
            action="assign created submitter Contact to Case",
        )
        self._append_audit(result)
        return result

    def _assign_reconciled_roles(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        submissions: list[dict[str, Any]],
        items: list[_ContactWorkItem],
        source_mapping: dict[tuple[str, str, str], str],
        *,
        target_account_id: str,
        target_account_name: str,
        parent_routed: bool,
    ) -> tuple[list[ActionResult], list[_RoleResponse]]:
        """Link roles using the preserved source mapping; never mutate Contacts."""
        results: list[ActionResult] = []
        responses: list[_RoleResponse] = []
        items_by_key = {item.key: item for item in items}
        for role in ROLE_DEFINITIONS:
            submitted = _submitted_role_values(submissions, row, role)
            if not any(submitted.values()):
                continue
            original_id, original_contact = self._fresh_role_contact(
                target_account_id, role.account_lookup
            )
            item: _ContactWorkItem | None = None
            for submission in reversed(submissions):
                if not any(
                    normalize_contact_value(suffix, submission.get(source_field))
                    for suffix, source_field in role.submitted_fields
                ):
                    continue
                source_key = (
                    "role",
                    role.prefix,
                    _display(submission.get("Id")),
                )
                mapped_key = source_mapping.get(source_key)
                if mapped_key:
                    item = items_by_key.get(mapped_key)
                    break
            if item is None or item.ignored or not item.contact_id:
                responses.append(
                    self._build_role_response(
                        row,
                        role.label,
                        original_contact,
                        original_contact,
                        changed=False,
                    )
                )
                continue

            final_contact = item.current_contact
            if final_contact is None:
                final_contact = self.client.get_record(
                    "Contact", item.contact_id, CONTACT_REVIEW_FIELDS
                )
            result = self._review_proposal(
                self._proposal(
                    batch,
                    row,
                    target_object="Account",
                    target_record_id=target_account_id,
                    field_name=role.account_lookup,
                    label=(
                        f"{role.label} Account Role — "
                        f"{target_account_name or target_account_id}"
                        if parent_routed
                        else f"{role.label} Account Role"
                    ),
                    proposed_value=item.contact_id,
                    resolution=item.resolution,
                ),
                original_display=_contact_name_email(original_contact),
                proposed_display=_contact_name_email(final_contact),
            )
            results.append(result)
            previous_contact = (
                item.original_contact
                if original_id == item.contact_id and item.original_contact
                else original_contact
            )
            contact_changed = item.result is not None and item.result.status in {
                ActionStatus.APPLIED,
                ActionStatus.VERIFIED_MANUAL,
            }
            changed = contact_changed or result.status in {
                ActionStatus.APPLIED,
                ActionStatus.VERIFIED_MANUAL,
            }
            responses.append(
                self._build_role_response(
                    row,
                    role.label,
                    (
                        final_contact
                        if result.status is not ActionStatus.REJECTED
                        else previous_contact
                    ),
                    previous_contact,
                    changed=changed,
                )
            )
        return results, responses

    @staticmethod
    def _contact_review_identity(item: _ContactWorkItem) -> str:
        """Return a person/email label that does not depend on a role."""
        contact = item.current_contact or item.resolution.selected_contact or {}
        reconciled = item.reconciled or {}
        name = " ".join(
            value
            for value in (
                reconciled.get("first_name") or _display(contact.get("FirstName")),
                reconciled.get("last_name") or _display(contact.get("LastName")),
            )
            if value
        )
        email = (
            reconciled.get("email")
            or item.resolution.normalized_email
            or _display(contact.get("Email"))
        )
        if name and email:
            return f"{name} <{email}>"
        return name or email or "(unidentified)"

    def _finish_batch(
        self,
        batch: CaseBatch,
        response_writer: _ResponseWriter,
        results: list[ActionResult],
        account_results: list[ActionResult],
        role_responses: list[_RoleResponse],
    ) -> bool:
        emails = format_response_emails(account_results, role_responses)
        all_sent = True
        for email, text in emails.items():
            response_writer.append(batch.case_id, email, text)
            self._display_event(ResponseEmail(ValueFragment(email), text))
            sent = self._prompt_yes_no(
                styled(
                    "Was the response email to ",
                    ValueFragment(email),
                    " sent? [yes/no]: ",
                )
            )
            all_sent = all_sent and sent

        successful_without_email = any(
            result.status in {ActionStatus.APPLIED, ActionStatus.VERIFIED_MANUAL}
            and not result.proposal.submitter_email.strip()
            for result in results
        )
        missing_role_email = any(
            not response.submitter_email.strip() for response in role_responses
        )
        all_sent = all_sent and not successful_without_email and not missing_role_email
        for source_id in batch.source_submission_ids:
            self._update_status_with_audit(
                batch,
                source_id,
                "Company_Profile_Change__c",
                "Status__c",
                ProfileChangeStatus.CLOSED,
            )
        case_status = CaseStatus.CLOSED if all_sent else CaseStatus.PENDING
        self._update_status_with_audit(
            batch,
            batch.case_id,
            "Case",
            "Status",
            case_status,
        )
        return not all_sent

    def _checkpoint_row(self, row: dict[str, str]) -> None:
        account_name = (
            row.get("account_name", "").strip() or row.get("account_id", "").strip()
        )
        submitter_name = row.get("submitter_name", "").strip() or "(name unavailable)"
        submitter_email = (
            row.get("submitter_email", "").strip() or "(email unavailable)"
        )
        source_names = ", ".join(
            _json_string_list(row.get("source_submission_names", "[]"))
        )
        self._display_event(
            StagedRowSummary(
                ValueFragment(account_name or "(unavailable)"),
                ValueFragment(submitter_name),
                ValueFragment(submitter_email),
                ValueFragment(source_names or "(unnamed)"),
                contact_details_supplemented=(
                    row.get("has_contact_derived_values") == "true"
                ),
                has_no_update_content=(row.get("has_no_update_content") == "true"),
            )
        )
        answer = self._ask_choice(
            ChoiceQuestion(
                styled(
                    "Continue with this staged row? [C/Continue/Q/Quit] "
                    "(default Continue): "
                ),
                (
                    ReviewChoice("continue", "Continue", ("c",)),
                    ReviewChoice("quit", "Quit", ("q",)),
                ),
                styled("Enter C or Continue, Q or Quit, or press Enter."),
                default_key="continue",
            )
        )
        if answer == "quit":
            raise ProcessingStoppedEarly

    def _fresh_case_submissions(self, batch: CaseBatch) -> dict[str, dict[str, Any]]:
        return {
            source_id: self.client.get_record(
                "Company_Profile_Change__c",
                source_id,
                SUBMISSION_FIELDS,
            )
            for source_id in batch.source_submission_ids
        }

    def _preflight_parent_routing(
        self,
        batch: CaseBatch,
        submissions_by_id: dict[str, dict[str, Any]],
    ) -> _ParentRouting:
        """Refetch one direct child level and validate all child-specific work."""
        account = self.client.get_record(
            "Account", batch.account_id, ACCOUNT_REVIEW_FIELDS
        )
        queried_children = self.client.query_records(
            "Account",
            ACCOUNT_REVIEW_FIELDS,
            where=(f"ParentId = '{escape_soql_string(batch.account_id)}'"),
            order_by="Id ASC",
        )
        direct_children = tuple(
            sorted(
                (
                    child
                    for child in queried_children
                    if not _display(child.get("ParentId"))
                    or _display(child.get("ParentId")) == batch.account_id
                ),
                key=lambda child: _display(child.get("Id")),
            )
        )
        if not direct_children:
            routing = _ParentRouting(account, (), (account,))
            self._refresh_affected_account_queue(batch, routing)
            return routing

        active_children = tuple(
            child
            for child in direct_children
            if _display(child.get("Cert_Certification_Status__c"))
            in ACTIVE_CERTIFICATION_STATUSES
        )
        conflicts: list[ParentAccountFieldConflict] = []
        if active_children:
            seen: set[tuple[str, str]] = set()
            for field_name, label, requested in self._parent_field_requests(
                batch, submissions_by_id
            ):
                identity = (field_name, requested)
                if identity in seen:
                    continue
                seen.add(identity)
                values = [child.get(field_name) for child in active_children]
                if all(_values_equal(values[0], value) for value in values[1:]):
                    continue
                conflicts.append(
                    ParentAccountFieldConflict(
                        label,
                        ValueFragment(requested),
                        tuple(
                            ParentAccountChildValue(
                                ValueFragment(_display(child.get("Id"))),
                                ValueFragment(
                                    _display(child.get("Name")) or "(unnamed)"
                                ),
                                ValueFragment(_display(child.get(field_name))),
                            )
                            for child in active_children
                        ),
                    )
                )
        routing = _ParentRouting(
            account,
            direct_children,
            active_children,
            tuple(conflicts),
        )
        self._refresh_affected_account_queue(batch, routing)
        return routing

    def _refresh_affected_account_queue(
        self,
        batch: CaseBatch,
        routing: _ParentRouting,
    ) -> None:
        """Keep child references and child-specific queue IDs fresh before writes."""
        affected_accounts = json.dumps(
            [
                {
                    "id": _display(account.get("Id")),
                    "name": _display(account.get("Name")),
                    "certification_status": _display(
                        account.get("Cert_Certification_Status__c")
                    ),
                }
                for account in routing.target_accounts
                if _display(account.get("Id"))
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        for row in batch.rows:
            row["is_parent_account"] = "true" if routing.is_parent else "false"
            row["affected_accounts"] = affected_accounts
        if self._queue_store is None or self._review_rows is None:
            return
        self._queue_store.refresh(build_review_queue(self._review_rows, now=self.now))
        self._display_event(ReviewQueueSnapshot(self._queue_store.manifest))

    def _parent_field_requests(
        self,
        batch: CaseBatch,
        submissions_by_id: dict[str, dict[str, Any]],
    ) -> list[tuple[str, str, str]]:
        """Return only Account fields and role lookups actually submitted."""
        requests: list[tuple[str, str, str]] = []
        for row in batch.rows:
            submissions = [
                submissions_by_id[source_id]
                for source_id in _json_string_list(row["source_submission_ids"])
            ]
            for _, source_field, account_field, label in ACCOUNT_PROPOSALS:
                requested = _latest_nonblank(submissions, source_field)
                if requested:
                    requests.append((account_field, label, requested))
            for role in ROLE_DEFINITIONS:
                submitted = _submitted_role_values(submissions, row, role)
                has_fresh_role_value = any(
                    _latest_nonblank(submissions, source_field)
                    for _, source_field in role.submitted_fields
                )
                if not has_fresh_role_value:
                    continue
                requested = row.get(f"{role.prefix}_salesforce_contact_id", "").strip()
                if not requested:
                    name = " ".join(
                        value
                        for value in (
                            submitted.get("first_name", ""),
                            submitted.get("last_name", ""),
                        )
                        if value
                    )
                    email = submitted.get("email", "")
                    requested = (
                        f"{name} <{email}>"
                        if name and email
                        else name or email or "new Contact"
                    )
                requests.append(
                    (
                        role.account_lookup,
                        f"{role.label} Account Role",
                        requested,
                    )
                )
        return requests

    def _defer_parent_batch(
        self,
        batch: CaseBatch,
        routing: _ParentRouting,
    ) -> bool:
        """Explain, acknowledge, audit, and defer one unsafe Parent batch."""
        parent = routing.submitted_account
        parent_label = _display(parent.get("Name")) or batch.rows[0].get(
            "account_name", ""
        )
        parent_display = f"{parent_label or '(unnamed)'} ({batch.account_id})"
        if routing.conflicts:
            self._display_event(
                ParentAccountConflict(
                    ValueFragment(parent_display),
                    routing.conflicts,
                )
            )
            reason = "Active direct child Account values conflict."
        else:
            self._display_event(
                ParentAccountNoActiveChildren(
                    ValueFragment(parent_display),
                    tuple(
                        ParentAccountChildValue(
                            ValueFragment(_display(child.get("Id"))),
                            ValueFragment(_display(child.get("Name")) or "(unnamed)"),
                            ValueFragment(
                                _display(child.get("Cert_Certification_Status__c"))
                            ),
                        )
                        for child in routing.direct_children
                    ),
                )
            )
            reason = "Parent Account has no active direct children."
        try:
            self._acknowledge(
                AcknowledgementQuestion(
                    styled(
                        "This Case batch needs manual follow-up and will remain "
                        "open. Press Enter to acknowledge and continue: "
                    )
                )
            )
        except (KeyboardInterrupt, EOFError) as error:
            self._append_batch_event(
                batch,
                ActionStatus.INTERRUPTED,
                action="acknowledge deferred parent Account batch",
                error="Reviewer interrupted processing.",
            )
            raise _ParentPreflightInterrupted(
                "Profile Update review was interrupted during parent preflight."
            ) from error
        self._append_batch_event(
            batch,
            ActionStatus.DEFERRED_MANUAL,
            action="defer parent Account batch for manual follow-up",
            error=reason,
        )
        return True

    def _show_case_context(
        self,
        batch: CaseBatch,
        submissions_by_id: dict[str, dict[str, Any]],
    ) -> None:
        account_name = next(
            (
                row.get("account_name", "").strip()
                for row in batch.rows
                if row.get("account_name", "").strip()
            ),
            batch.account_id,
        )
        self._display_event(ContextLine("Account", (ValueFragment(account_name),)))
        submitters = dict.fromkeys(
            (
                row.get("submitter_name", "").strip(),
                row.get("submitter_email", "").strip(),
            )
            for row in batch.rows
        )
        for name, email in submitters:
            self._display_event(
                ContextLine(
                    "Submitter",
                    styled(ValueFragment(name), " <", ValueFragment(email), ">"),
                )
            )

        for submission in submissions_by_id.values():
            self._display_event(
                Notice(
                    styled(
                        "Profile Update ",
                        ValueFragment(
                            _display(submission.get("Name") or submission.get("Id"))
                        ),
                        " (status: ",
                        ValueFragment(_display(submission.get("Status__c"))),
                        ")",
                    )
                )
            )
            comments = _display(submission.get("Comments__c"))
            notes = _display(submission.get("Other_Personnel_Notes__c"))
            if comments:
                self._display_event(ContextLine("Comments", (ValueFragment(comments),)))
            if notes:
                self._display_event(
                    ContextLine("Other Personnel notes", (ValueFragment(notes),))
                )

        self._show_unique_row_context(batch.rows, "effective_date", "Effective date")
        self._show_unique_row_context(batch.rows, "key_answers", "Key Update answers")
        self._show_unique_row_context(batch.rows, "warnings", "Warnings")

    def _show_unique_row_context(
        self,
        rows: list[dict[str, str]],
        field_name: str,
        label: str,
    ) -> None:
        values = dict.fromkeys(
            row.get(field_name, "").strip()
            for row in rows
            if row.get(field_name, "").strip()
        )
        for value in values:
            self._display_event(ContextLine(label, (ValueFragment(value),)))

    def _show_account_history(
        self,
        batch: CaseBatch,
        submissions: list[dict[str, Any]],
    ) -> None:
        account_id = batch.account_id
        days = {
            _required_datetime(_display(submission.get("CreatedDate")))
            .astimezone(CHICAGO)
            .date()
            for submission in submissions
            if _display(submission.get("CreatedDate"))
        }
        for day in sorted(days):
            local_start = datetime.combine(day, time.min, tzinfo=CHICAGO)
            local_end = local_start + timedelta(days=1)
            start = local_start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            end = local_end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            history = self.client.query_records(
                "AccountHistory",
                ACCOUNT_HISTORY_FIELDS,
                where=(
                    f"AccountId = '{escape_soql_string(account_id)}' "
                    f"AND CreatedDate >= {start} AND CreatedDate < {end}"
                ),
                order_by="CreatedDate ASC, Id ASC",
            )
            for item in history:
                self._display_event(
                    AccountHistory(
                        ValueFragment(_display(item.get("Field"))),
                        ValueFragment(_display(item.get("OldValue"))),
                        ValueFragment(_display(item.get("NewValue"))),
                        ValueFragment(_display(item.get("CreatedDate"))),
                    )
                )

    def _review_account_proposals(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        submissions: list[dict[str, Any]],
        *,
        target_account_id: str,
        target_account_name: str,
        parent_routed: bool,
    ) -> list[ActionResult]:
        results = []
        for csv_name, source_field, account_field, label in ACCOUNT_PROPOSALS:
            proposed = row.get(csv_name, "").strip()
            fresh_value = _latest_nonblank(submissions, source_field)
            if fresh_value:
                proposed = fresh_value
            elif parent_routed:
                # Parent routing intentionally leaves unsubmitted child fields alone.
                continue
            if not proposed:
                continue
            proposal = self._proposal(
                batch,
                row,
                target_object="Account",
                target_record_id=target_account_id,
                field_name=account_field,
                label=(
                    f"{label} — {target_account_name or target_account_id}"
                    if parent_routed
                    else label
                ),
                proposed_value=proposed,
            )
            results.append(self._review_proposal(proposal))
        return results

    def _fresh_role_contact(
        self,
        account_id: str,
        account_lookup: str,
    ) -> tuple[str, dict[str, Any] | None]:
        account = self.client.get_record(
            "Account",
            account_id,
            ["Id", account_lookup],
        )
        contact_id = _display(account.get(account_lookup))
        if not contact_id:
            return "", None
        contact = self.client.get_record(
            "Contact",
            contact_id,
            CONTACT_REVIEW_FIELDS,
        )
        return contact_id, contact

    def _build_role_response(
        self,
        row: dict[str, str],
        role_label: str,
        final_contact: dict[str, Any] | None,
        original_contact: dict[str, Any] | None,
        *,
        changed: bool,
    ) -> _RoleResponse:
        current = _contact_summary(final_contact)
        previous = _contact_summary(original_contact)
        return _RoleResponse(
            account_name=row.get("account_name", ""),
            submitter_email=row.get("submitter_email", ""),
            label=f"{role_label} Contact",
            contact_details=current,
            previous_details=(previous if changed and previous != current else ""),
            changed=changed,
        )

    def _recover_duplicate_contact(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        proposal: ChangeProposal,
        resolution: ContactResolution,
        error: SalesforceError,
    ) -> ActionResult:
        """Recover a Contact create that Salesforce's duplicate rule blocked."""
        self._append_audit(
            ActionResult(
                proposal,
                ReviewDecision.APPLY_AUTOMATICALLY,
                ActionStatus.FAILED,
                action="Salesforce duplicate rule blocked Contact create",
                error=str(error),
                error_code=error.error_code or "",
                salesforce_message=error.salesforce_message or "",
            )
        )
        self._display_event(
            Notice(
                styled(
                    "Salesforce blocked this Contact create as a possible duplicate."
                )
            )
        )
        while True:
            choice = self._ask_choice(
                ChoiceQuestion(
                    styled(
                        "Duplicate recovery [1/create manually, "
                        "2/update an existing Contact manually, "
                        "3/use a Contact with another email, "
                        "4/ignore this entry]: "
                    ),
                    (
                        ReviewChoice("create_manually", "create manually", ("1",)),
                        ReviewChoice(
                            "update_manually",
                            "update an existing Contact manually",
                            ("2",),
                        ),
                        ReviewChoice(
                            "alternate_email",
                            "use a Contact with another email",
                            ("3",),
                        ),
                        ReviewChoice("ignore", "ignore this entry", ("4",)),
                    ),
                    styled("Choose 1, 2, 3, or 4."),
                )
            )
            if choice == "ignore":
                resolution.reason = (
                    "The operator ignored a Salesforce duplicate-rule failure."
                )
                ignored_proposal = ChangeProposal(
                    **{
                        **proposal.__dict__,
                        "reason": resolution.reason,
                    }
                )
                ignored = ActionResult(
                    ignored_proposal,
                    ReviewDecision.WILL_NOT_BE_MADE,
                    ActionStatus.REJECTED,
                    action="ignore duplicate Contact entry",
                    error=str(error),
                    error_code=error.error_code or "",
                    salesforce_message=error.salesforce_message or "",
                )
                self._append_audit(ignored)
                return ignored

            if choice == "alternate_email":
                alternate = self._ask_free_text(
                    FreeTextQuestion(
                        styled("Enter the existing Contact's other email: ")
                    )
                )
                normalized, _, warnings = normalize_email(alternate)
                if warnings or not normalized:
                    self._display_event(
                        ValidationFeedback(styled("Enter a valid email address."))
                    )
                    continue
                contact = self._select_contact_by_email(normalized)
                action = "use alternate-email Contact after duplicate failure"
            else:
                instruction = (
                    "Create the Contact manually"
                    if choice == "create_manually"
                    else "Update an existing Contact manually"
                )
                self._acknowledge(
                    AcknowledgementQuestion(
                        styled(
                            instruction,
                            ", then press Enter to verify by email: ",
                        )
                    )
                )
                contact = self._select_contact_by_email(resolution.normalized_email)
                action = (
                    "verify manual Contact creation after duplicate failure"
                    if choice == "create_manually"
                    else "verify manual Contact update after duplicate failure"
                )
            if contact is None:
                self._display_event(
                    ValidationFeedback(
                        styled(
                            "No Contact was found with that email; choose a recovery "
                            "option again."
                        )
                    )
                )
                continue
            contact_id = _display(contact.get("Id"))
            resolution.selected_contact = contact
            resolution.reason = "The operator completed duplicate-create recovery."
            verified_proposal = ChangeProposal(
                **{
                    **proposal.__dict__,
                    "target_record_id": contact_id,
                    "selected_contact": contact_snapshot(contact),
                    "reason": resolution.reason,
                }
            )
            result = ActionResult(
                verified_proposal,
                ReviewDecision.MAKE_MANUALLY,
                ActionStatus.VERIFIED_MANUAL,
                action=action,
            )
            self._append_audit(result)
            return result

    def _select_contact_by_email(self, email: str) -> dict[str, Any] | None:
        contacts = self.client.query_records(
            "Contact",
            CONTACT_REVIEW_FIELDS,
            where=f"Email = '{escape_soql_string(email)}'",
            order_by="Id ASC",
        )
        if not contacts:
            return None
        if len(contacts) == 1:
            return dict(contacts[0])
        for index, contact in enumerate(contacts, start=1):
            self._show_contact_details(f"Candidate {index}", contact)
        answer = self._ask_choice(
            ChoiceQuestion(
                styled(f"Select Contact [1-{len(contacts)}]: "),
                tuple(
                    ReviewChoice(str(index), f"Candidate {index}")
                    for index in range(1, len(contacts) + 1)
                ),
                styled("Choose one candidate number."),
            )
        )
        return dict(contacts[int(answer) - 1])

    def _proposal(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        *,
        target_object: str,
        target_record_id: str,
        field_name: str,
        label: str,
        proposed_value: Any,
        original_value: Any | None = None,
        resolution: ContactResolution | None = None,
    ) -> ChangeProposal:
        return ChangeProposal(
            source_submission_ids=tuple(
                _json_string_list(row["source_submission_ids"])
            ),
            case_id=batch.case_id,
            case_number=batch.case_number,
            account_id=batch.account_id,
            account_name=row.get("account_name", ""),
            submitter_email=row.get("submitter_email", ""),
            target_object=target_object,
            target_record_id=target_record_id,
            field_name=field_name,
            label=label,
            original_value=original_value,
            proposed_value=proposed_value,
            context=_proposal_context(row),
            warnings="\n".join(
                value
                for value in (
                    row.get("warnings", ""),
                    ("\n".join(resolution.warnings) if resolution is not None else ""),
                )
                if value
            ),
            classification=(
                resolution.classification.value if resolution is not None else ""
            ),
            comparison_key=(
                resolution.comparison_key if resolution is not None else ""
            ),
            candidates=(
                tuple(contact_snapshot(item) for item in resolution.candidates)
                if resolution is not None
                else ()
            ),
            selected_contact=(
                contact_snapshot(resolution.selected_contact)
                if resolution is not None and resolution.selected_contact is not None
                else None
            ),
            reason=resolution.reason if resolution is not None else "",
            confidence=resolution.confidence if resolution is not None else "",
        )

    def _review_proposal(
        self,
        proposal: ChangeProposal,
        *,
        original_display: str | None = None,
        proposed_display: str | None = None,
    ) -> ActionResult:
        """Refresh and present a proposal before asking for a decision."""
        try:
            fresh = self.client.get_record(
                proposal.target_object,
                proposal.target_record_id,
                ["Id", proposal.field_name],
            )
        except SalesforceError as error:
            failed = ActionResult(
                proposal,
                None,
                ActionStatus.FAILED,
                action="fetch current value",
                error=str(error),
            )
            self._append_audit(failed)
            raise ProcessingError(str(error)) from error
        proposal = ChangeProposal(
            **{
                **proposal.__dict__,
                "original_value": fresh.get(proposal.field_name),
            }
        )
        if _values_equal(proposal.original_value, proposal.proposed_value):
            result = ActionResult(
                proposal,
                None,
                ActionStatus.NOOP,
                action="already current",
            )
            self._append_audit(result)
            self._display_event(
                Notice(
                    styled(
                        ValueFragment(proposal.label),
                        ": already current; no change needed.",
                    )
                )
            )
            return result

        self._show_proposal(
            proposal,
            original_display=original_display,
            proposed_display=proposed_display,
        )
        decision = self._review_decision(proposal)
        return self._execute_proposal(proposal, decision)

    def _review_decision(
        self,
        proposal: ChangeProposal,
        *,
        automatic_allowed: bool = True,
        action: str = "review",
    ) -> ReviewDecision:
        """Ask for one validated reviewer decision and audit interruptions."""
        self._transition_proposal(proposal, QueueStatus.IN_PROGRESS)
        try:
            return self._prompt_decision(automatic_allowed=automatic_allowed)
        except (KeyboardInterrupt, EOFError) as error:
            interrupted = ActionResult(
                proposal,
                None,
                ActionStatus.INTERRUPTED,
                action=action,
                error="Reviewer interrupted processing.",
            )
            self._append_audit(interrupted)
            raise ProcessingInterrupted(
                "Profile Update review was interrupted."
            ) from error

    def _execute_proposal(
        self,
        proposal: ChangeProposal,
        decision: ReviewDecision,
    ) -> ActionResult:
        """Execute an already-made decision and persist its audit result."""
        if decision is ReviewDecision.WILL_NOT_BE_MADE:
            result = ActionResult(
                proposal,
                decision,
                ActionStatus.REJECTED,
                action="no Salesforce write",
            )
            self._append_audit(result)
            return result
        if decision is ReviewDecision.MAKE_MANUALLY:
            try:
                self._acknowledge(
                    AcknowledgementQuestion(
                        styled(
                            "Make the change in Salesforce, then press Enter to verify it: "
                        )
                    )
                )
                verified = self.client.get_record(
                    proposal.target_object,
                    proposal.target_record_id,
                    ["Id", proposal.field_name],
                )
            except (KeyboardInterrupt, EOFError) as error:
                interrupted = ActionResult(
                    proposal,
                    decision,
                    ActionStatus.INTERRUPTED,
                    action="manual verification",
                    error="Reviewer interrupted processing.",
                )
                self._append_audit(interrupted)
                raise ProcessingInterrupted(
                    "Profile Update review was interrupted."
                ) from error
            except SalesforceError as error:
                failed = ActionResult(
                    proposal,
                    decision,
                    ActionStatus.FAILED,
                    action="manual verification",
                    error=str(error),
                )
                self._append_audit(failed)
                raise ProcessingError(str(error)) from error
            if not _values_equal(
                verified.get(proposal.field_name), proposal.proposed_value
            ):
                error = (
                    f"Salesforce {proposal.label} does not match the proposed value."
                )
                failed = ActionResult(
                    proposal,
                    decision,
                    ActionStatus.FAILED,
                    action="manual verification",
                    error=error,
                )
                self._append_audit(failed)
                raise ProcessingError(error)
            result = ActionResult(
                proposal,
                decision,
                ActionStatus.VERIFIED_MANUAL,
                action="manual verification",
            )
            self._append_audit(result)
            return result

        try:
            self._update_record(
                proposal.target_object,
                proposal.target_record_id,
                {proposal.field_name: proposal.proposed_value},
            )
        except SalesforceError as error:
            failed = ActionResult(
                proposal,
                decision,
                ActionStatus.FAILED,
                action="update Salesforce",
                error=str(error),
            )
            self._append_audit(failed)
            raise ProcessingError(str(error)) from error
        result = ActionResult(
            proposal,
            decision,
            ActionStatus.APPLIED,
            action="update Salesforce",
        )
        self._append_audit(result)
        return result

    def _show_proposal(
        self,
        proposal: ChangeProposal,
        *,
        original_display: str | None = None,
        proposed_display: str | None = None,
    ) -> None:
        current = (
            original_display
            if original_display is not None
            else _display(proposal.original_value)
        )
        proposed = (
            proposed_display
            if proposed_display is not None
            else _display(proposal.proposed_value)
        )
        if (
            original_display is None
            and proposed_display is None
            and isinstance(proposal.original_value, dict)
            and isinstance(proposal.proposed_value, dict)
        ):
            self._show_mapping_proposal(proposal)
            return
        self._display_event(
            ScalarComparison(
                proposal.label,
                ValueFragment(current),
                ValueFragment(proposed),
            )
        )

    def _show_mapping_proposal(self, proposal: ChangeProposal) -> None:
        """Show dictionary-backed changes as readable field comparisons."""
        current = proposal.original_value
        proposed = proposal.proposed_value
        is_new = not proposal.target_record_id or proposal.target_record_id == "(new)"
        rows = []
        for field_name, proposed_value in proposed.items():
            label = CONTACT_FIELD_LABELS.get(field_name, field_name)
            rows.append(
                MappingComparisonRow(
                    label,
                    ValueFragment(_display(current.get(field_name))),
                    ValueFragment(_display(proposed_value)),
                )
            )
        self._display_event(MappingComparison(proposal.label, tuple(rows), is_new))

    def _show_contact_details(
        self,
        heading: str,
        contact: dict[str, Any],
    ) -> None:
        name = " ".join(
            value
            for value in (
                _display(contact.get("FirstName")),
                _display(contact.get("LastName")),
            )
            if value
        )
        self._display_event(
            ContactCard(
                heading,
                ValueFragment(name),
                ValueFragment(_display(contact.get("Title"))),
                ValueFragment(_display(contact.get("Email"))),
                ValueFragment(_display(contact.get("Phone"))),
            )
        )

    def _prompt_decision(self, *, automatic_allowed: bool = True) -> ReviewDecision:
        choices = (
            (
                ReviewChoice(
                    ReviewDecision.APPLY_AUTOMATICALLY.value,
                    ReviewDecision.APPLY_AUTOMATICALLY.value,
                    ("a",),
                ),
            )
            if automatic_allowed
            else ()
        )
        choices += (
            ReviewChoice(
                ReviewDecision.MAKE_MANUALLY.value,
                ReviewDecision.MAKE_MANUALLY.value,
                ("m",),
            ),
            ReviewChoice(
                ReviewDecision.WILL_NOT_BE_MADE.value,
                ReviewDecision.WILL_NOT_BE_MADE.value,
                ("n",),
            ),
        )
        answer = self._ask_choice(
            ChoiceQuestion(
                styled(
                    "Decision [A/apply automatically / M/make manually / "
                    "N/will not be made]: "
                ),
                choices,
                styled(
                    "Enter A, M, or N, or one of the three complete decision phrases."
                    if automatic_allowed
                    else "This incomplete Contact cannot be created automatically; "
                    "choose make manually or will not be made."
                ),
            )
        )
        try:
            return ReviewDecision(answer)
        except ValueError as error:
            raise UnsupportedReviewInteractionError(
                f"Unknown review decision answer {answer!r}."
            ) from error

    def _prompt_yes_no(self, prompt: StyledText) -> bool:
        answer = self._ask_choice(
            ChoiceQuestion(
                prompt,
                (
                    ReviewChoice("yes", "yes"),
                    ReviewChoice("no", "no"),
                ),
                styled("Enter yes or no."),
            )
        )
        return answer == "yes"

    def _update_status_with_audit(
        self,
        batch: CaseBatch,
        record_id: str,
        object_name: str,
        field_name: str,
        status: str,
        *,
        action: str = "finalize batch",
    ) -> None:
        proposal = ChangeProposal(
            source_submission_ids=batch.source_submission_ids,
            case_id=batch.case_id,
            case_number=batch.case_number,
            account_id=batch.account_id,
            account_name=batch.rows[0].get("account_name", ""),
            submitter_email="",
            target_object=object_name,
            target_record_id=record_id,
            field_name=field_name,
            label=f"{object_name} {field_name}",
            original_value="",
            proposed_value=status,
        )
        try:
            self._update_record(object_name, record_id, {field_name: status})
        except SalesforceError as error:
            result = ActionResult(
                proposal,
                None,
                ActionStatus.FAILED,
                action=action,
                error=str(error),
            )
            self._append_audit(result)
            raise ProcessingError(str(error)) from error
        result = ActionResult(
            proposal,
            None,
            ActionStatus.APPLIED,
            action=action,
        )
        self._append_audit(result)

    def _keep_case_pending(self, batch: CaseBatch) -> None:
        proposal = ChangeProposal(
            source_submission_ids=batch.source_submission_ids,
            case_id=batch.case_id,
            case_number=batch.case_number,
            account_id=batch.account_id,
            account_name=batch.rows[0].get("account_name", ""),
            submitter_email="",
            target_object="Case",
            target_record_id=batch.case_id,
            field_name="Status",
            label="Case Status",
            original_value="",
            proposed_value=CaseStatus.PENDING,
        )
        try:
            self._update_record("Case", batch.case_id, {"Status": CaseStatus.PENDING})
        except SalesforceError as error:
            self._append_audit(
                ActionResult(
                    proposal,
                    None,
                    ActionStatus.FAILED,
                    action="keep interrupted batch pending",
                    error=str(error),
                )
            )
        else:
            self._append_audit(
                ActionResult(
                    proposal,
                    None,
                    ActionStatus.APPLIED,
                    action="keep interrupted batch pending",
                )
            )

    def _append_batch_event(
        self,
        batch: CaseBatch,
        status: ActionStatus,
        *,
        action: str,
        error: str,
    ) -> None:
        proposal = ChangeProposal(
            source_submission_ids=batch.source_submission_ids,
            case_id=batch.case_id,
            case_number=batch.case_number,
            account_id=batch.account_id,
            account_name=batch.rows[0].get("account_name", ""),
            submitter_email="",
            target_object="CaseBatch",
            target_record_id=batch.case_id,
            field_name="workflow",
            label="Case batch workflow",
            original_value="",
            proposed_value="complete",
        )
        self._append_audit(
            ActionResult(
                proposal,
                None,
                status,
                action=action,
                error=error,
            )
        )

    def _append_audit(self, result: ActionResult) -> None:
        if self._audit is None:
            raise RuntimeError("Audit writer is unavailable.")
        self._audit.append(result)
        if result.status in {
            ActionStatus.APPLIED,
            ActionStatus.VERIFIED_MANUAL,
            ActionStatus.REJECTED,
            ActionStatus.NOOP,
        }:
            status = QueueStatus.COMPLETED
        elif result.status is ActionStatus.STOPPED_EARLY:
            status = QueueStatus.STOPPED_EARLY
        elif result.status in {ActionStatus.FAILED, ActionStatus.INTERRUPTED}:
            status = QueueStatus.FAILED
        elif result.status is ActionStatus.DEFERRED_MANUAL:
            status = QueueStatus.BLOCKED
        else:
            return
        if (
            result.status is ActionStatus.DEFERRED_MANUAL
            and result.proposal.target_object == "CaseBatch"
        ):
            self._block_queue_batch(result.proposal, outcome=result.status.value)
            return
        transitioned = self._transition_proposal(
            result.proposal,
            status,
            outcome=result.status.value,
        )
        if not transitioned and result.status in {
            ActionStatus.STOPPED_EARLY,
            ActionStatus.FAILED,
            ActionStatus.INTERRUPTED,
            ActionStatus.DEFERRED_MANUAL,
        }:
            self._transition_default(status, outcome=result.status.value)

    def _block_queue_batch(
        self,
        proposal: ChangeProposal,
        *,
        outcome: str,
    ) -> None:
        """Map a manual-follow-up audit event to an explicit blocked batch."""
        if self._queue_store is None:
            return
        source_ids = set(proposal.source_submission_ids)
        batch = next(
            (
                queued
                for queued in self._queue_store.manifest.batches
                if queued.case.record_id == proposal.case_id
                and {
                    source_id
                    for row in queued.rows
                    for source_id in row.source_submission_ids
                }
                == source_ids
            ),
            None,
        )
        if batch is None:
            return
        self._queue_store.block_batch(
            batch.id,
            QueueBlocker(
                "parent_account_manual_follow_up",
                "Parent Account routing requires manual follow-up.",
            ),
            outcome=outcome,
        )
        self._active_change_id = None
        self._display_event(ReviewQueueSnapshot(self._queue_store.manifest))

    def _transition_default(
        self, status: QueueStatus, *, outcome: str | None = None
    ) -> None:
        if self._queue_store is None:
            return
        item_id = (
            self._active_change_id or self._queue_store.manifest.default_next_item_id
        )
        if item_id is None:
            return
        self._queue_store.transition(item_id, status, outcome=outcome)
        self._active_change_id = None
        self._display_event(ReviewQueueSnapshot(self._queue_store.manifest))

    def _transition_proposal(
        self,
        proposal: ChangeProposal,
        status: QueueStatus,
        *,
        outcome: str | None = None,
    ) -> bool:
        """Map an executed proposal back to one or more discovered changes."""
        if self._queue_store is None:
            return False
        changes = list(iter_changes(self._queue_store.manifest))
        exact_id = stable_queue_id(
            "proposed_change",
            object_name=proposal.target_object,
            source_submission_ids=proposal.source_submission_ids,
            target_context=proposal.target_record_id,
            field=proposal.field_name,
        )
        item_ids = [change.id for change in changes if change.id == exact_id]
        if proposal.target_object == "Contact" and proposal.field_name == "Contact":
            source_ids = set(proposal.source_submission_ids)
            item_ids.extend(
                change.id
                for change in changes
                if change.phase is QueuePhase.CONTACT
                and set(change.source_submission_ids) == source_ids
            )
        item_ids = list(dict.fromkeys(item_ids))
        for item_id in item_ids:
            self._queue_store.transition(item_id, status, outcome=outcome)
        if item_ids:
            self._active_change_id = (
                item_ids[0] if status is QueueStatus.IN_PROGRESS else None
            )
            self._display_event(ReviewQueueSnapshot(self._queue_store.manifest))
        return bool(item_ids)


def format_response_emails(
    results: list[ActionResult],
    role_responses: list[_RoleResponse] | None = None,
) -> dict[str, str]:
    """Return one response paragraph per submitter email."""
    grouped: dict[str, list[ChangeProposal]] = {}
    for result in results:
        if result.status not in {
            ActionStatus.APPLIED,
            ActionStatus.VERIFIED_MANUAL,
        }:
            continue
        proposal = result.proposal
        email = proposal.submitter_email.strip()
        if not email:
            continue
        grouped.setdefault(email, []).append(proposal)

    roles_by_email: dict[str, list[_RoleResponse]] = {}
    for response in role_responses or []:
        email = response.submitter_email.strip()
        if not email:
            continue
        roles_by_email.setdefault(email, []).append(response)

    emails: dict[str, str] = {}
    ordered_emails = dict.fromkeys([*grouped, *roles_by_email])
    for email in ordered_emails:
        proposals = grouped.get(email, [])
        roles = roles_by_email.get(email, [])
        account_name = next(
            (
                proposal.account_name.strip()
                for proposal in proposals
                if proposal.account_name.strip()
            ),
            next(
                (
                    response.account_name.strip()
                    for response in roles
                    if response.account_name.strip()
                ),
                "your account",
            ),
        )
        lines = [ACCOUNT_EMAIL_OPENING.format(account_name=account_name)]
        seen: set[tuple[str, str, str, str]] = set()
        for proposal in proposals:
            identity = (
                proposal.target_object,
                proposal.target_record_id,
                proposal.field_name,
                _display(proposal.proposed_value),
            )
            if identity in seen:
                continue
            seen.add(identity)
            lines.extend(
                [
                    "",
                    f"{proposal.label}: {_display(proposal.proposed_value) or '(blank)'}",
                    f"Replaces {_display(proposal.original_value) or '(blank)'}",
                ]
            )
        seen_roles: set[tuple[str, str, str, bool]] = set()
        for response in roles:
            identity = (
                response.label,
                response.contact_details,
                response.previous_details,
                response.changed,
            )
            if identity in seen_roles:
                continue
            seen_roles.add(identity)
            suffix = "" if response.changed else " - no change"
            lines.extend(["", f"{response.label}: {response.contact_details}{suffix}"])
            if response.previous_details:
                lines.append(f"Replaces {response.previous_details}")
        emails[email] = "\n".join(lines)
    return emails


def _proposal_context(row: dict[str, str]) -> str:
    parts = []
    if row.get("effective_date"):
        parts.append(f"Effective date: {row['effective_date']}")
    if row.get("key_answers"):
        parts.append(row["key_answers"])
    if row.get("comments"):
        parts.append(f"Comments: {row['comments']}")
    if row.get("personnel_notes"):
        parts.append(f"Other Personnel notes: {row['personnel_notes']}")
    return "\n".join(parts)


def _latest_nonblank(records: list[dict[str, Any]], field_name: str) -> str:
    value = ""
    for record in records:
        candidate = _display(record.get(field_name))
        if candidate:
            value = candidate
    return value


def _submitted_role_values(
    submissions: list[dict[str, Any]],
    row: dict[str, str],
    role: RoleDefinition,
) -> dict[str, str]:
    """Prefer and normalize fresh submitted values for one Contact role."""
    values: dict[str, str] = {}
    for suffix, source_field in role.submitted_fields:
        fresh_value = _latest_nonblank(submissions, source_field)
        values[suffix] = (
            normalize_contact_value(suffix, fresh_value)
            if fresh_value
            else row.get(f"{role.prefix}_{suffix}", "").strip()
        )
    return values


def _json_string_list(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) and item.strip() for item in parsed
    ):
        raise ValueError("Expected a JSON list of nonblank strings.")
    return [item.strip() for item in parsed]


def _required_datetime(value: str) -> datetime:
    try:
        return _aware_datetime(
            datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        )
    except (AttributeError, ValueError) as error:
        raise ProcessingError(f"Invalid Salesforce date/time: {value!r}.") from error


def _aware_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value).strip()


def _required_record_text(record: dict[str, Any], field_name: str, label: str) -> str:
    """Read one required text value from a Salesforce record."""
    value = _display(record.get(field_name))
    if not value:
        raise ProcessingError(f"{label} is missing.")
    return value


def _split_person_name(value: Any) -> tuple[str, str]:
    """Split a submitter name at the final space."""
    parts = _display(value).rsplit(maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]
    if parts:
        return "", parts[0]
    return "", ""


def _values_equal(current: Any, proposed: Any) -> bool:
    return _display(current) == _display(proposed)


def _is_duplicate_contact_error(error: SalesforceError) -> bool:
    """Recognize duplicate-rule responses without hiding unrelated failures."""
    if error.error_code == "DUPLICATES_DETECTED":
        return True
    duplicate_messages = {
        "use one of these records?",
        (
            "you're creating a duplicate record. we recommend you use an "
            "existing record instead."
        ),
    }
    salesforce_message = (error.salesforce_message or "").strip().casefold()
    if salesforce_message in duplicate_messages:
        return True
    readable = str(error).strip().casefold()
    return any(message in readable for message in duplicate_messages)


def _section_heading(title: str, separator: str = STAGE_SEPARATOR) -> str:
    return f"\n{separator}\n{title}\n{separator}"


def _account_choice_label(account: dict[str, Any]) -> str:
    """Return the identifying Account text shown in a selection control."""
    name = _display(account.get("Name")) or "(name unavailable)"
    profile_id = _display(account.get("Certification_ID__c")) or "(blank)"
    return f"{name} (Profile ID {profile_id})"


def _contact_name_email(contact: dict[str, Any] | None) -> str:
    if not contact:
        return "(blank)"
    name = " ".join(
        value
        for value in (
            _display(contact.get("FirstName")),
            _display(contact.get("LastName")),
        )
        if value
    )
    email = _display(contact.get("Email"))
    if name and email:
        return f"{name} <{email}>"
    return name or email or "(blank)"


def _contact_summary(contact: dict[str, Any] | None) -> str:
    if not contact:
        return "(blank)"
    name = " ".join(
        value
        for value in (
            _display(contact.get("FirstName")),
            _display(contact.get("LastName")),
        )
        if value
    )
    details = [
        value
        for value in (
            name,
            _display(contact.get("Title")),
            _display(contact.get("Email")),
            _display(contact.get("Phone")),
        )
        if value
    ]
    return ", ".join(details) or "(blank)"
