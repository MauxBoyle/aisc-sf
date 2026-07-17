"""Interactive, audited processing for staged Profile Update submissions."""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO
from zoneinfo import ZoneInfo

from .profile_updates import SUBMISSION_FIELDS, AutomationCounts, escape_soql_string
from .salesforce import SalesforceClient, SalesforceError
from .stage_profile_updates import (
    CSV_COLUMNS,
    ROLE_DEFINITIONS,
    ProfileUpdateStagingService,
    StagingResult,
    write_staged_profile_updates,
)

CHICAGO = ZoneInfo("America/Chicago")

ACCOUNT_REVIEW_FIELDS = [
    "Id",
    "Name",
    "Company_Owner__c",
    "BillingStreet",
    "BillingCity",
    "BillingState",
    "BillingPostalCode",
    "BillingCountry",
    *(role.account_lookup for role in ROLE_DEFINITIONS),
]

CONTACT_REVIEW_FIELDS = [
    "Id",
    "AccountId",
    "FirstName",
    "LastName",
    "Title",
    "Email",
    "Phone",
]

ACCOUNT_HISTORY_FIELDS = [
    "Id",
    "AccountId",
    "Field",
    "OldValue",
    "NewValue",
    "CreatedDate",
]

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


@dataclass(frozen=True)
class ActionResult:
    """The reviewer decision and the resulting Salesforce outcome."""

    proposal: ChangeProposal
    decision: ReviewDecision | None
    status: ActionStatus
    action: str = ""
    error: str = ""


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
class ProcessingResult:
    """Artifacts and counts returned after interactive review."""

    staging_path: Path
    audit_path: Path
    response_path: Path
    completed_batches: int
    pending_batches: int


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
    ):
        self.case_service = case_service
        self.staging_service = staging_service
        self.processor = processor
        self.staging_writer = staging_writer

    def run(self, output_dir: Path) -> ProcessingResult | Any:
        """Execute setup in the required order and review the published CSV."""
        counts: AutomationCounts = self.case_service.run()
        if counts.failed:
            details = "; ".join(getattr(self.case_service, "errors", []))
            suffix = f": {details}" if details else ""
            raise ProcessingError(
                f"{counts.failed} required Case operation(s) failed{suffix}"
            )
        staged: StagingResult = self.staging_service.stage()
        staging_path = self.staging_writer(staged.rows, output_dir)
        rows = read_staged_profile_updates(staging_path / "profile_updates.csv")
        result = self.processor.review(rows, staging_path)
        if isinstance(result, ProcessingResult):
            return ProcessingResult(
                staging_path=staging_path,
                audit_path=result.audit_path,
                response_path=result.response_path,
                completed_batches=result.completed_batches,
                pending_batches=result.pending_batches,
            )
        return result


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
    return rows


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
            "decision": result.decision.value if result.decision else "",
            "action": result.action,
            "result": result.status.value,
            "error": result.error,
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
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        now: datetime | None = None,
    ):
        self.client = client
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.now = _aware_datetime(now or datetime.now(UTC))
        self._audit: _AuditWriter | None = None

    def review(
        self, rows: list[dict[str, str]], artifact_dir: Path
    ) -> ProcessingResult:
        """Review all rows and return interruption-safe local artifacts."""
        artifact_dir.mkdir(parents=True, exist_ok=True)
        audit_path = artifact_dir / "review_audit.jsonl"
        response_path = artifact_dir / "response_emails.txt"
        response_writer = _ResponseWriter(response_path)
        batches = build_case_batches(rows, now=self.now)
        completed = 0
        pending = 0
        try:
            with _AuditWriter(audit_path, lambda: datetime.now(UTC)) as audit:
                self._audit = audit
                for batch in batches:
                    try:
                        is_pending = self._review_batch(batch, response_writer)
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
        return ProcessingResult(
            staging_path=artifact_dir,
            audit_path=audit_path,
            response_path=response_path,
            completed_batches=completed,
            pending_batches=pending,
        )

    def _review_batch(self, batch: CaseBatch, response_writer: _ResponseWriter) -> bool:
        self.output_fn(
            f"\nReviewing Case {batch.case_number or batch.case_id} "
            f"for Account {batch.account_id}"
        )
        self._update_status_with_audit(
            batch,
            batch.case_id,
            "Case",
            "Status",
            "Pending",
            action="prepare batch",
        )
        results: list[ActionResult] = []
        for row in batch.rows:
            fresh_submissions = self._fresh_submissions(row)
            self._show_submission_context(row, fresh_submissions)
            self._show_account_history(row, fresh_submissions)
            results.extend(
                self._review_account_proposals(batch, row, fresh_submissions)
            )
            results.extend(self._review_roles(batch, row, fresh_submissions))

        emails = format_response_emails(results)
        all_sent = True
        for email, text in emails.items():
            response_writer.append(batch.case_id, email, text)
            self.output_fn(f"\nResponse email for {email}:\n{text}")
            sent = self._prompt_yes_no(
                f"Was the response email to {email} sent? [yes/no]: "
            )
            all_sent = all_sent and sent

        successful_without_email = any(
            result.status in {ActionStatus.APPLIED, ActionStatus.VERIFIED_MANUAL}
            and not result.proposal.submitter_email.strip()
            for result in results
        )
        all_sent = all_sent and not successful_without_email
        for source_id in batch.source_submission_ids:
            self._update_status_with_audit(
                batch,
                source_id,
                "Company_Profile_Change__c",
                "Status__c",
                "Closed",
            )
        case_status = "Closed" if all_sent else "Pending"
        self._update_status_with_audit(
            batch,
            batch.case_id,
            "Case",
            "Status",
            case_status,
        )
        return not all_sent

    def _fresh_submissions(self, row: dict[str, str]) -> list[dict[str, Any]]:
        return [
            self.client.get_record(
                "Company_Profile_Change__c",
                source_id,
                SUBMISSION_FIELDS,
            )
            for source_id in _json_string_list(row["source_submission_ids"])
        ]

    def _show_submission_context(
        self,
        row: dict[str, str],
        submissions: list[dict[str, Any]],
    ) -> None:
        self.output_fn(
            f"Account: {row.get('account_name') or row.get('account_id')}\n"
            f"Submitter: {row.get('submitter_name')} <{row.get('submitter_email')}>"
        )
        for submission in submissions:
            self.output_fn(
                f"Profile Update {submission.get('Name') or submission.get('Id')} "
                f"(status: {submission.get('Status__c', '')})"
            )
            comments = _display(submission.get("Comments__c"))
            notes = _display(submission.get("Other_Personnel_Notes__c"))
            if comments:
                self.output_fn(f"Comments: {comments}")
            if notes:
                self.output_fn(f"Other Personnel notes: {notes}")
        if row.get("effective_date"):
            self.output_fn(f"Effective date: {row['effective_date']}")
        if row.get("key_answers"):
            self.output_fn(f"Key Update answers:\n{row['key_answers']}")
        if row.get("warnings"):
            self.output_fn(f"Warnings:\n{row['warnings']}")

    def _show_account_history(
        self,
        row: dict[str, str],
        submissions: list[dict[str, Any]],
    ) -> None:
        account_id = row["account_id"]
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
                self.output_fn(
                    "Account History: "
                    f"{item.get('Field')} changed from "
                    f"{_display(item.get('OldValue')) or '(blank)'} to "
                    f"{_display(item.get('NewValue')) or '(blank)'} at "
                    f"{item.get('CreatedDate')}"
                )

    def _review_account_proposals(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        submissions: list[dict[str, Any]],
    ) -> list[ActionResult]:
        results = []
        for csv_name, source_field, account_field, label in ACCOUNT_PROPOSALS:
            proposed = row.get(csv_name, "").strip()
            fresh_value = _latest_nonblank(submissions, source_field)
            if fresh_value:
                proposed = fresh_value
            if not proposed:
                continue
            proposal = self._proposal(
                batch,
                row,
                target_object="Account",
                target_record_id=batch.account_id,
                field_name=account_field,
                label=label,
                proposed_value=proposed,
            )
            results.append(self._review_proposal(proposal))
        return results

    def _review_roles(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        submissions: list[dict[str, Any]],
    ) -> list[ActionResult]:
        results: list[ActionResult] = []
        for role in ROLE_DEFINITIONS:
            prefix = role.prefix
            submitted = {
                suffix: (
                    _latest_nonblank(submissions, source_field)
                    or row.get(f"{prefix}_{suffix}", "").strip()
                )
                for suffix, source_field in role.submitted_fields
            }
            if not any(submitted.values()):
                continue
            contacts = self._fresh_contact_candidates(
                batch.account_id,
                row.get(f"{prefix}_salesforce_contact_id", "").strip(),
            )
            self.output_fn(f"\n{role.label} Contact candidates:")
            if contacts:
                for number, contact in enumerate(contacts, start=1):
                    self.output_fn(
                        f"{number}. {contact.get('FirstName', '')} "
                        f"{contact.get('LastName', '')} "
                        f"<{contact.get('Email', '')}> [{contact.get('Id')}]"
                    )
            else:
                self.output_fn("(none)")

            choice = self._prompt_contact_choice(role.label)
            contact_id = ""
            if choice == "select existing":
                contact_id = self._select_contact_id(contacts)
            elif choice == "create contact":
                created = self._review_new_contact(batch, row, role.label, submitted)
                results.append(created)
                if created.status in {
                    ActionStatus.APPLIED,
                    ActionStatus.VERIFIED_MANUAL,
                }:
                    contact_id = created.proposal.target_record_id
            else:
                declined = self._proposal(
                    batch,
                    row,
                    target_object="Account",
                    target_record_id=batch.account_id,
                    field_name=role.account_lookup,
                    label=f"{role.label} Account Role",
                    proposed_value="No role change",
                    original_value="",
                )
                result = ActionResult(
                    declined,
                    ReviewDecision.WILL_NOT_BE_MADE,
                    ActionStatus.REJECTED,
                    action="decline contact",
                )
                self._append_audit(result)
                results.append(result)

            if not contact_id:
                continue
            for suffix, contact_field, field_label in CONTACT_SUFFIX_FIELDS:
                if suffix == "title" and role.title_field is None:
                    continue
                proposed = submitted.get(suffix, "")
                if not proposed:
                    continue
                proposal = self._proposal(
                    batch,
                    row,
                    target_object="Contact",
                    target_record_id=contact_id,
                    field_name=contact_field,
                    label=f"{role.label} Contact {field_label}",
                    proposed_value=proposed,
                )
                results.append(self._review_proposal(proposal))

            role_proposal = self._proposal(
                batch,
                row,
                target_object="Account",
                target_record_id=batch.account_id,
                field_name=role.account_lookup,
                label=f"{role.label} Account Role",
                proposed_value=contact_id,
            )
            results.append(self._review_proposal(role_proposal))
        return results

    def _fresh_contact_candidates(
        self, account_id: str, staged_contact_id: str
    ) -> list[dict[str, Any]]:
        where = f"AccountId = '{escape_soql_string(account_id)}'"
        if staged_contact_id:
            where = f"({where} OR Id = '{escape_soql_string(staged_contact_id)}')"
        contacts = self.client.query_records(
            "Contact",
            CONTACT_REVIEW_FIELDS,
            where=where,
            order_by="LastName ASC, FirstName ASC, Id ASC",
        )
        return [dict(contact) for contact in contacts]

    def _review_new_contact(
        self,
        batch: CaseBatch,
        row: dict[str, str],
        role_label: str,
        submitted: dict[str, str],
    ) -> ActionResult:
        first_name = submitted.get("first_name", "")
        last_name = submitted.get("last_name", "")
        display_name = " ".join(value for value in (first_name, last_name) if value)
        proposal = self._proposal(
            batch,
            row,
            target_object="Contact",
            target_record_id="(new)",
            field_name="Contact",
            label=f"{role_label} Contact",
            proposed_value=display_name,
            original_value="",
        )
        if not last_name:
            self.output_fn(
                f"{role_label} Contact cannot be created automatically because "
                "the required Last Name field is missing."
            )
        self._show_proposal(proposal)
        try:
            decision = self._prompt_decision(
                automatic_allowed=bool(last_name),
            )
        except (KeyboardInterrupt, EOFError) as error:
            result = ActionResult(
                proposal,
                None,
                ActionStatus.INTERRUPTED,
                action="create Contact",
                error="Reviewer interrupted processing.",
            )
            self._append_audit(result)
            raise ProcessingInterrupted(
                "Profile Update review was interrupted."
            ) from error

        if decision is ReviewDecision.WILL_NOT_BE_MADE:
            result = ActionResult(
                proposal,
                decision,
                ActionStatus.REJECTED,
                action="create Contact",
            )
            self._append_audit(result)
            return result
        if decision is ReviewDecision.MAKE_MANUALLY:
            try:
                contact_id = self.input_fn(
                    "Create/select the Contact in Salesforce, then enter its Contact ID: "
                ).strip()
                if not contact_id:
                    raise ProcessingError(
                        "A Contact ID is required for manual verification."
                    )
                fresh = self.client.get_record(
                    "Contact", contact_id, CONTACT_REVIEW_FIELDS
                )
            except (KeyboardInterrupt, EOFError) as error:
                result = ActionResult(
                    proposal,
                    decision,
                    ActionStatus.INTERRUPTED,
                    action="verify manual Contact creation",
                    error="Reviewer interrupted processing.",
                )
                self._append_audit(result)
                raise ProcessingInterrupted(
                    "Profile Update review was interrupted."
                ) from error
            except ProcessingError as error:
                result = ActionResult(
                    proposal,
                    decision,
                    ActionStatus.FAILED,
                    action="verify manual Contact creation",
                    error=str(error),
                )
                self._append_audit(result)
                raise
            except SalesforceError as error:
                result = ActionResult(
                    proposal,
                    decision,
                    ActionStatus.FAILED,
                    action="verify manual Contact creation",
                    error=str(error),
                )
                self._append_audit(result)
                raise ProcessingError(str(error)) from error
            if not _values_equal(
                fresh.get("FirstName"), first_name
            ) or not _values_equal(fresh.get("LastName"), last_name):
                error = "Manually selected Contact does not match the proposed name."
                result = ActionResult(
                    proposal,
                    decision,
                    ActionStatus.FAILED,
                    action="verify manual Contact creation",
                    error=error,
                )
                self._append_audit(result)
                raise ProcessingError(error)
            verified_proposal = ChangeProposal(
                **{
                    **proposal.__dict__,
                    "target_record_id": contact_id,
                }
            )
            result = ActionResult(
                verified_proposal,
                decision,
                ActionStatus.VERIFIED_MANUAL,
                action="verify manual Contact creation",
            )
            self._append_audit(result)
            return result

        payload = {
            "AccountId": batch.account_id,
            "LastName": last_name,
        }
        if first_name:
            payload["FirstName"] = first_name
        try:
            contact_id = self.client.create_record("Contact", payload)
        except SalesforceError as error:
            result = ActionResult(
                proposal,
                decision,
                ActionStatus.FAILED,
                action="create Contact",
                error=str(error),
            )
            self._append_audit(result)
            raise ProcessingError(str(error)) from error
        created_proposal = ChangeProposal(
            **{
                **proposal.__dict__,
                "target_record_id": contact_id,
            }
        )
        result = ActionResult(
            created_proposal,
            decision,
            ActionStatus.APPLIED,
            action="create Contact",
        )
        self._append_audit(result)
        return result

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
            warnings=row.get("warnings", ""),
        )

    def _review_proposal(self, proposal: ChangeProposal) -> ActionResult:
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
            self.output_fn(f"{proposal.label}: already current; no change needed.")
            return result

        self._show_proposal(proposal)
        try:
            decision = self._prompt_decision()
        except (KeyboardInterrupt, EOFError) as error:
            interrupted = ActionResult(
                proposal,
                None,
                ActionStatus.INTERRUPTED,
                action="review",
                error="Reviewer interrupted processing.",
            )
            self._append_audit(interrupted)
            raise ProcessingInterrupted(
                "Profile Update review was interrupted."
            ) from error

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
                self.input_fn(
                    "Make the change in Salesforce, then press Enter to verify it: "
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
            self.client.update_record(
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

    def _show_proposal(self, proposal: ChangeProposal) -> None:
        self.output_fn(
            f"\n{proposal.label}\n"
            f"Current Salesforce value: {_display(proposal.original_value) or '(blank)'}\n"
            f"Proposed value: {_display(proposal.proposed_value) or '(blank)'}"
        )
        if proposal.context:
            self.output_fn(f"Submission context: {proposal.context}")
        if proposal.warnings:
            self.output_fn(f"Warnings: {proposal.warnings}")

    def _prompt_decision(self, *, automatic_allowed: bool = True) -> ReviewDecision:
        values = {decision.value: decision for decision in ReviewDecision}
        while True:
            answer = self.input_fn(
                "Decision [apply automatically / make manually / will not be made]: "
            )
            normalized = answer.strip().casefold()
            decision = values.get(normalized)
            if decision is None:
                self.output_fn("Enter one of the three complete decision phrases.")
                continue
            if decision is ReviewDecision.APPLY_AUTOMATICALLY and not automatic_allowed:
                self.output_fn(
                    "This incomplete Contact cannot be created automatically; "
                    "choose make manually or will not be made."
                )
                continue
            return decision

    def _prompt_contact_choice(self, role_label: str) -> str:
        allowed = {"create contact", "select existing", "decline"}
        while True:
            answer = self.input_fn(
                f"{role_label} Contact choice "
                "[create contact / select existing / decline]: "
            )
            normalized = answer.strip().casefold()
            if normalized in allowed:
                return normalized
            self.output_fn("Choose create contact, select existing, or decline.")

    def _select_contact_id(self, contacts: list[dict[str, Any]]) -> str:
        while True:
            answer = self.input_fn(
                "Enter a candidate number or an existing Salesforce Contact ID: "
            ).strip()
            if answer.isdigit() and 1 <= int(answer) <= len(contacts):
                return _display(contacts[int(answer) - 1].get("Id"))
            if answer and any(
                _display(contact.get("Id")) == answer for contact in contacts
            ):
                return answer
            self.output_fn("Select one of the fresh Contact candidates shown.")

    def _prompt_yes_no(self, prompt: str) -> bool:
        while True:
            answer = self.input_fn(prompt).strip().casefold()
            if answer in {"yes", "no"}:
                return answer == "yes"
            self.output_fn("Enter yes or no.")

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
            self.client.update_record(object_name, record_id, {field_name: status})
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
            proposed_value="Pending",
        )
        try:
            self.client.update_record("Case", batch.case_id, {"Status": "Pending"})
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


def format_response_emails(results: list[ActionResult]) -> dict[str, str]:
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

    emails: dict[str, str] = {}
    for email, proposals in grouped.items():
        account_name = next(
            (
                proposal.account_name.strip()
                for proposal in proposals
                if proposal.account_name.strip()
            ),
            "your account",
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


def _values_equal(current: Any, proposed: Any) -> bool:
    return _display(current) == _display(proposed)
