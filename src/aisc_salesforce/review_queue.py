"""Deterministic, UI-neutral queue for Profile Update review work.

The queue intentionally contains no run timestamp.  The timestamped artifact
directory already records when a run started, while omitting a timestamp here
makes identical Salesforce input produce identical JSON bytes.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from .account_roles import ACCOUNT_ROLE_DEFINITIONS
from .filesystem import sync_directory

SCHEMA_VERSION = 1
REVIEW_QUEUE_FILENAME = "review_queue.json"
QUEUE_NAMESPACE = UUID("d44ea56d-33c8-5be8-a6a1-495c32dd70b7")


class QueueStatus(StrEnum):
    """Lifecycle shared by changes, rows, and Case batches."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED_EARLY = "stopped_early"


class QueuePhase(StrEnum):
    """Business execution phases in their required order."""

    SETUP = "setup"
    CONTACT = "contact"
    ACCOUNT = "account"
    ROLE_LINK = "role_link"


@dataclass(frozen=True)
class QueueWarning:
    """Informational condition that does not by itself prevent review."""

    code: str
    message: str


@dataclass(frozen=True)
class QueueBlocker:
    """Condition that makes an item unsafe to review automatically."""

    code: str
    message: str


@dataclass(frozen=True)
class SalesforceReference:
    """Explicit Salesforce record context retained by the queue."""

    object_name: str
    record_id: str
    field_name: str = ""
    relationship: str = ""
    label: str = ""
    status: str = ""


@dataclass(frozen=True)
class ProposedChange:
    """One field-level unit of proposed work."""

    id: str
    label: str
    status: QueueStatus
    warnings: tuple[QueueWarning, ...]
    blockers: tuple[QueueBlocker, ...]
    phase: QueuePhase
    source_submission_ids: tuple[str, ...]
    source_row_ids: tuple[str, ...]
    salesforce: SalesforceReference
    field: str
    current_value: Any
    proposed_value: Any
    outcome: str | None = None
    context: str = ""

    @property
    def reviewable(self) -> bool:
        """Return whether this pending change can be selected next."""
        return self.status is QueueStatus.PENDING and not self.blockers


@dataclass(frozen=True)
class StagedRow:
    """One deterministic row within a Case batch."""

    id: str
    label: str
    status: QueueStatus
    warnings: tuple[QueueWarning, ...]
    blockers: tuple[QueueBlocker, ...]
    source_submission_ids: tuple[str, ...]
    earliest_submission: str
    changes: tuple[ProposedChange, ...]
    references: tuple[SalesforceReference, ...] = ()


@dataclass(frozen=True)
class CaseBatch:
    """One ordered Account/Case group in the review queue."""

    id: str
    label: str
    status: QueueStatus
    warnings: tuple[QueueWarning, ...]
    blockers: tuple[QueueBlocker, ...]
    account: SalesforceReference
    case: SalesforceReference
    earliest_submission: str
    earliest_key_update: str | None
    rows: tuple[StagedRow, ...]
    references: tuple[SalesforceReference, ...] = ()


@dataclass(frozen=True)
class ReviewQueueManifest:
    """Versioned root object published as ``review_queue.json``."""

    schema_version: int
    default_next_item_id: str | None
    batches: tuple[CaseBatch, ...]


ACCOUNT_PROPOSALS = (
    ("revised_company_name", "Name", "Company Name"),
    ("revised_company_owner", "Company_Owner__c", "Company Owner"),
    ("revised_facility_street", "BillingStreet", "Billing Street"),
    ("revised_facility_city", "BillingCity", "Billing City"),
    ("revised_facility_state", "BillingState", "Billing State"),
    ("revised_facility_zip", "BillingPostalCode", "Billing ZIP"),
    ("revised_facility_country", "BillingCountry", "Billing Country"),
)

CONTACT_FIELDS = {
    "first_name": "FirstName",
    "last_name": "LastName",
    "title": "Title",
    "email": "Email",
    "phone": "Phone",
}

PHASE_ORDER = {
    QueuePhase.SETUP: 0,
    QueuePhase.CONTACT: 1,
    QueuePhase.ACCOUNT: 2,
    QueuePhase.ROLE_LINK: 3,
}


def stable_queue_id(
    item_type: str,
    *,
    object_name: str,
    source_submission_ids: tuple[str, ...] | list[str],
    target_context: str,
    field: str = "",
) -> str:
    """Return a UUID5 ID based only on canonical machine identity.

    Labels are deliberately absent, so improving display text cannot change an
    item's identity.
    """
    canonical = json.dumps(
        {
            "field": field,
            "item_type": item_type,
            "object_name": object_name,
            "source_submission_ids": sorted(set(source_submission_ids)),
            "target_context": target_context,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return str(uuid5(QUEUE_NAMESPACE, canonical))


def build_review_queue(
    rows: list[dict[str, str]], *, now: datetime | None = None
) -> ReviewQueueManifest:
    """Build a complete read-only queue, including unresolved setup work."""
    current = _aware(now or datetime.now(UTC))
    row_models = [_build_row_shell(row) for row in rows]
    contact_changes = _build_contact_changes(rows, row_models)

    completed_rows: list[StagedRow] = []
    for raw, shell in zip(rows, row_models, strict=True):
        changes = [*_setup_changes(raw, shell), *_account_changes(raw, shell)]
        changes.extend(contact_changes.get(shell.id, ()))
        changes.extend(_role_link_changes(raw, shell))
        ordered = tuple(sorted(changes, key=_change_sort_key))
        completed_rows.append(
            replace(
                shell, changes=ordered, status=_parent_status(ordered, shell.blockers)
            )
        )

    grouped: dict[str, list[StagedRow]] = {}
    raw_by_row_id = {
        model.id: raw for raw, model in zip(rows, completed_rows, strict=True)
    }
    for model in completed_rows:
        raw = raw_by_row_id[model.id]
        account_id = raw.get("account_id", "").strip()
        case_id = raw.get("case_id", "").strip()
        if case_id:
            context = f"account:{account_id}|case:{case_id}"
        elif account_id:
            context = f"account:{account_id}|case:unresolved"
        else:
            context = "submissions:" + ",".join(model.source_submission_ids)
        grouped.setdefault(context, []).append(model)

    batches: list[CaseBatch] = []
    for context, batch_rows in grouped.items():
        ordered_rows = tuple(sorted(batch_rows, key=_row_sort_key))
        source_ids = tuple(
            sorted(
                {
                    source_id
                    for row in ordered_rows
                    for source_id in row.source_submission_ids
                }
            )
        )
        raw_rows = [raw_by_row_id[row.id] for row in ordered_rows]
        account_id = next(
            (
                row.get("account_id", "").strip()
                for row in raw_rows
                if row.get("account_id", "").strip()
            ),
            "",
        )
        case_id = next(
            (
                row.get("case_id", "").strip()
                for row in raw_rows
                if row.get("case_id", "").strip()
            ),
            "",
        )
        case_number = next(
            (
                row.get("case_number", "").strip()
                for row in raw_rows
                if row.get("case_number", "").strip()
            ),
            "",
        )
        account_name = next(
            (
                row.get("account_name", "").strip()
                for row in raw_rows
                if row.get("account_name", "").strip()
            ),
            "",
        )
        earliest = (
            min(ordered_rows, key=_row_sort_key).earliest_submission
            if ordered_rows
            else ""
        )
        key_dates = [
            row.get("earliest_key_update_date", "").strip()
            for row in raw_rows
            if row.get("has_key_updates") == "true"
            and row.get("earliest_key_update_date", "").strip()
        ]
        earliest_key = (
            min(key_dates, key=lambda value: _parse_datetime(value) or _datetime_max())
            if key_dates
            else None
        )
        warnings = _unique_warnings(w for row in ordered_rows for w in row.warnings)
        blockers = _unique_blockers(b for row in ordered_rows for b in row.blockers)
        batch_id = stable_queue_id(
            "case_batch",
            object_name="Case",
            source_submission_ids=source_ids,
            target_context=context,
        )
        label_context = case_number or case_id or "Case setup required"
        label_account = account_name or account_id or "Account setup required"
        references = _unique_references(
            reference for row in ordered_rows for reference in row.references
        )
        batches.append(
            CaseBatch(
                id=batch_id,
                label=f"{label_context}: {label_account}",
                status=_parent_status(ordered_rows, blockers),
                warnings=warnings,
                blockers=blockers,
                account=SalesforceReference("Account", account_id),
                case=SalesforceReference("Case", case_id),
                earliest_submission=earliest,
                earliest_key_update=earliest_key,
                rows=ordered_rows,
                references=references,
            )
        )

    overdue_before = current - timedelta(days=7)
    batches.sort(key=lambda batch: _batch_sort_key(batch, overdue_before))
    return recompute_manifest(ReviewQueueManifest(SCHEMA_VERSION, None, tuple(batches)))


def recompute_manifest(manifest: ReviewQueueManifest) -> ReviewQueueManifest:
    """Recalculate parent statuses and the first reviewable pending change."""
    batches: list[CaseBatch] = []
    default_next: str | None = None
    for batch in manifest.batches:
        rows: list[StagedRow] = []
        for row in batch.rows:
            refreshed = replace(
                row,
                status=_parent_status(row.changes, row.blockers),
            )
            rows.append(refreshed)
            if default_next is None:
                default_next = next(
                    (change.id for change in refreshed.changes if change.reviewable),
                    None,
                )
        batches.append(
            replace(
                batch,
                rows=tuple(rows),
                status=_parent_status(rows, batch.blockers),
            )
        )
    return replace(manifest, batches=tuple(batches), default_next_item_id=default_next)


def transition_item(
    manifest: ReviewQueueManifest,
    item_id: str,
    status: QueueStatus,
    *,
    outcome: str | None = None,
) -> ReviewQueueManifest:
    """Return a new manifest with every occurrence of an item ID updated."""
    found = False
    batches: list[CaseBatch] = []
    for batch in manifest.batches:
        batch_rows: list[StagedRow] = []
        for row in batch.rows:
            changes = []
            for change in row.changes:
                if change.id == item_id:
                    found = True
                    change = replace(change, status=status, outcome=outcome)
                changes.append(change)
            row_status = status if row.id == item_id else row.status
            if row.id == item_id:
                found = True
            batch_rows.append(replace(row, changes=tuple(changes), status=row_status))
        batch_status = status if batch.id == item_id else batch.status
        if batch.id == item_id:
            found = True
        batches.append(replace(batch, rows=tuple(batch_rows), status=batch_status))
    if not found:
        return manifest
    return recompute_manifest(replace(manifest, batches=tuple(batches)))


def manifest_json(manifest: ReviewQueueManifest) -> str:
    """Serialize a manifest deterministically, including one final newline."""
    return (
        json.dumps(
            asdict(manifest),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def read_review_queue(path: Path) -> ReviewQueueManifest:
    """Load and strictly validate one published review queue.

    A queue can authorize Salesforce writes, so malformed or newer queue data
    is rejected instead of being guessed at.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Review queue could not be read: {error}") from error
    root = _mapping(raw, "review queue")
    _exact_fields(
        root, {"schema_version", "default_next_item_id", "batches"}, "review queue"
    )
    version = root["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported review queue schema version {version!r}; "
            f"expected {SCHEMA_VERSION}."
        )
    default_next = _optional_string(
        root["default_next_item_id"], "review queue default_next_item_id"
    )
    batches = tuple(
        _case_batch(item, f"review queue batches[{index}]")
        for index, item in enumerate(_list(root["batches"], "review queue batches"))
    )
    return ReviewQueueManifest(version, default_next, batches)


def _case_batch(raw: Any, context: str) -> CaseBatch:
    value = _mapping(raw, context)
    _exact_fields(
        value,
        {
            "id",
            "label",
            "status",
            "warnings",
            "blockers",
            "account",
            "case",
            "earliest_submission",
            "earliest_key_update",
            "rows",
            "references",
        },
        context,
    )
    return CaseBatch(
        id=_nonblank_string(value["id"], f"{context} id"),
        label=_string(value["label"], f"{context} label"),
        status=_queue_status(value["status"], f"{context} status"),
        warnings=_warnings(value["warnings"], f"{context} warnings"),
        blockers=_blockers(value["blockers"], f"{context} blockers"),
        account=_salesforce_reference(value["account"], f"{context} account"),
        case=_salesforce_reference(value["case"], f"{context} case"),
        earliest_submission=_string(
            value["earliest_submission"], f"{context} earliest_submission"
        ),
        earliest_key_update=_optional_string(
            value["earliest_key_update"], f"{context} earliest_key_update"
        ),
        rows=tuple(
            _staged_row(item, f"{context} rows[{index}]")
            for index, item in enumerate(_list(value["rows"], f"{context} rows"))
        ),
        references=_references(value["references"], f"{context} references"),
    )


def _staged_row(raw: Any, context: str) -> StagedRow:
    value = _mapping(raw, context)
    _exact_fields(
        value,
        {
            "id",
            "label",
            "status",
            "warnings",
            "blockers",
            "source_submission_ids",
            "earliest_submission",
            "changes",
            "references",
        },
        context,
    )
    source_ids = _string_tuple(
        value["source_submission_ids"], f"{context} source_submission_ids"
    )
    if not source_ids:
        raise ValueError(f"{context} source_submission_ids cannot be empty.")
    return StagedRow(
        id=_nonblank_string(value["id"], f"{context} id"),
        label=_string(value["label"], f"{context} label"),
        status=_queue_status(value["status"], f"{context} status"),
        warnings=_warnings(value["warnings"], f"{context} warnings"),
        blockers=_blockers(value["blockers"], f"{context} blockers"),
        source_submission_ids=source_ids,
        earliest_submission=_string(
            value["earliest_submission"], f"{context} earliest_submission"
        ),
        changes=tuple(
            _proposed_change(item, f"{context} changes[{index}]")
            for index, item in enumerate(_list(value["changes"], f"{context} changes"))
        ),
        references=_references(value["references"], f"{context} references"),
    )


def _proposed_change(raw: Any, context: str) -> ProposedChange:
    value = _mapping(raw, context)
    _exact_fields(
        value,
        {
            "id",
            "label",
            "status",
            "warnings",
            "blockers",
            "phase",
            "source_submission_ids",
            "source_row_ids",
            "salesforce",
            "field",
            "current_value",
            "proposed_value",
            "outcome",
            "context",
        },
        context,
    )
    try:
        phase = QueuePhase(_string(value["phase"], f"{context} phase"))
    except ValueError as error:
        raise ValueError(f"{context} phase is invalid.") from error
    return ProposedChange(
        id=_nonblank_string(value["id"], f"{context} id"),
        label=_string(value["label"], f"{context} label"),
        status=_queue_status(value["status"], f"{context} status"),
        warnings=_warnings(value["warnings"], f"{context} warnings"),
        blockers=_blockers(value["blockers"], f"{context} blockers"),
        phase=phase,
        source_submission_ids=_string_tuple(
            value["source_submission_ids"], f"{context} source_submission_ids"
        ),
        source_row_ids=_string_tuple(
            value["source_row_ids"], f"{context} source_row_ids"
        ),
        salesforce=_salesforce_reference(value["salesforce"], f"{context} salesforce"),
        field=_string(value["field"], f"{context} field"),
        current_value=value["current_value"],
        proposed_value=value["proposed_value"],
        outcome=_optional_string(value["outcome"], f"{context} outcome"),
        context=_string(value["context"], f"{context} context"),
    )


def _warnings(raw: Any, context: str) -> tuple[QueueWarning, ...]:
    warnings = []
    for index, item in enumerate(_list(raw, context)):
        item_context = f"{context}[{index}]"
        value = _mapping(item, item_context)
        _exact_fields(value, {"code", "message"}, item_context)
        warnings.append(
            QueueWarning(
                _nonblank_string(value["code"], f"{item_context} code"),
                _string(value["message"], f"{item_context} message"),
            )
        )
    return tuple(warnings)


def _blockers(raw: Any, context: str) -> tuple[QueueBlocker, ...]:
    blockers = []
    for index, item in enumerate(_list(raw, context)):
        item_context = f"{context}[{index}]"
        value = _mapping(item, item_context)
        _exact_fields(value, {"code", "message"}, item_context)
        blockers.append(
            QueueBlocker(
                _nonblank_string(value["code"], f"{item_context} code"),
                _string(value["message"], f"{item_context} message"),
            )
        )
    return tuple(blockers)


def _references(raw: Any, context: str) -> tuple[SalesforceReference, ...]:
    return tuple(
        _salesforce_reference(item, f"{context}[{index}]")
        for index, item in enumerate(_list(raw, context))
    )


def _salesforce_reference(raw: Any, context: str) -> SalesforceReference:
    value = _mapping(raw, context)
    _exact_fields(
        value,
        {"object_name", "record_id", "field_name", "relationship", "label", "status"},
        context,
    )
    return SalesforceReference(
        object_name=_nonblank_string(value["object_name"], f"{context} object_name"),
        record_id=_string(value["record_id"], f"{context} record_id"),
        field_name=_string(value["field_name"], f"{context} field_name"),
        relationship=_string(value["relationship"], f"{context} relationship"),
        label=_string(value["label"], f"{context} label"),
        status=_string(value["status"], f"{context} status"),
    )


def _queue_status(raw: Any, context: str) -> QueueStatus:
    try:
        return QueueStatus(_string(raw, context))
    except ValueError as error:
        raise ValueError(f"{context} is invalid.") from error


def _mapping(raw: Any, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ValueError(f"{context} must be a JSON object.")
    return raw


def _list(raw: Any, context: str) -> list[Any]:
    if not isinstance(raw, list):
        raise ValueError(f"{context} must be a JSON list.")
    return raw


def _exact_fields(value: dict[str, Any], expected: set[str], context: str) -> None:
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unexpected " + ", ".join(sorted(extra)))
        raise ValueError(f"{context} has invalid fields ({'; '.join(details)}).")


def _string(raw: Any, context: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{context} must be a string.")
    return raw


def _nonblank_string(raw: Any, context: str) -> str:
    value = _string(raw, context)
    if not value.strip():
        raise ValueError(f"{context} cannot be blank.")
    return value


def _optional_string(raw: Any, context: str) -> str | None:
    if raw is None:
        return None
    return _string(raw, context)


def _string_tuple(raw: Any, context: str) -> tuple[str, ...]:
    return tuple(
        _nonblank_string(item, f"{context}[{index}]")
        for index, item in enumerate(_list(raw, context))
    )


def write_review_queue(manifest: ReviewQueueManifest, path: Path) -> Path:
    """Atomically replace ``path`` with a fully flushed queue snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            output.write(manifest_json(manifest))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


class ReviewQueueStore:
    """Persist immutable queue snapshots around every lifecycle transition."""

    def __init__(self, path: Path, manifest: ReviewQueueManifest):
        self.path = path
        self.manifest = recompute_manifest(manifest)

    def publish(self) -> Path:
        """Write the current snapshot."""
        return write_review_queue(self.manifest, self.path)

    def transition(
        self,
        item_id: str,
        status: QueueStatus,
        *,
        outcome: str | None = None,
    ) -> ReviewQueueManifest:
        """Write snapshots immediately before and after a state transition."""
        self.publish()
        self.manifest = transition_item(self.manifest, item_id, status, outcome=outcome)
        self.publish()
        return self.manifest

    def refresh(self, manifest: ReviewQueueManifest) -> ReviewQueueManifest:
        """Replace discovered work while preserving statuses of stable IDs."""
        previous = {
            change.id: (change.status, change.outcome)
            for batch in self.manifest.batches
            for row in batch.rows
            for change in row.changes
        }
        refreshed = manifest
        for item_id, (status, outcome) in previous.items():
            if status not in {QueueStatus.PENDING, QueueStatus.BLOCKED}:
                refreshed = transition_item(refreshed, item_id, status, outcome=outcome)
        # A setup write can resolve a formerly missing Salesforce target.  Its
        # strict ID then changes because the new record ID is now known.  Carry
        # that setup outcome by source/object/field so setup does not reappear
        # as pending after the read-only refresh.
        prior_setup = {
            (
                change.source_submission_ids,
                change.salesforce.object_name,
                change.field,
            ): (change.status, change.outcome)
            for change in iter_changes(self.manifest)
            if change.phase is QueuePhase.SETUP
            and change.status not in {QueueStatus.PENDING, QueueStatus.BLOCKED}
        }
        for change in tuple(iter_changes(refreshed)):
            key = (
                change.source_submission_ids,
                change.salesforce.object_name,
                change.field,
            )
            if change.phase is QueuePhase.SETUP and key in prior_setup:
                status, outcome = prior_setup[key]
                refreshed = transition_item(
                    refreshed, change.id, status, outcome=outcome
                )
        self.manifest = recompute_manifest(refreshed)
        self.publish()
        return self.manifest

    def resume(self) -> ReviewQueueManifest:
        """Reset unfinished work while retaining durable terminal outcomes."""
        reset_statuses = {
            QueueStatus.IN_PROGRESS,
            QueueStatus.FAILED,
            QueueStatus.STOPPED_EARLY,
        }
        manifest = self.manifest
        for change in tuple(iter_changes(manifest)):
            if change.status in reset_statuses:
                manifest = transition_item(
                    manifest,
                    change.id,
                    QueueStatus.PENDING,
                    outcome=None,
                )
        self.manifest = recompute_manifest(manifest)
        self.publish()
        return self.manifest

    def block_batch(
        self,
        batch_id: str,
        blocker: QueueBlocker,
        *,
        outcome: str,
    ) -> ReviewQueueManifest:
        """Block one Case batch while preserving already-completed setup work."""
        self.publish()
        batches: list[CaseBatch] = []
        for batch in self.manifest.batches:
            if batch.id != batch_id:
                batches.append(batch)
                continue
            rows: list[StagedRow] = []
            for row in batch.rows:
                changes = tuple(
                    replace(change, status=QueueStatus.BLOCKED, outcome=outcome)
                    if change.status in {QueueStatus.PENDING, QueueStatus.IN_PROGRESS}
                    else change
                    for change in row.changes
                )
                rows.append(
                    replace(
                        row,
                        blockers=_unique_blockers((*row.blockers, blocker)),
                        changes=changes,
                    )
                )
            batches.append(
                replace(
                    batch,
                    blockers=_unique_blockers((*batch.blockers, blocker)),
                    rows=tuple(rows),
                )
            )
        self.manifest = recompute_manifest(
            replace(self.manifest, batches=tuple(batches))
        )
        self.publish()
        return self.manifest


def iter_changes(  # noqa: UP043 - the full generator contract is intentional.
    manifest: ReviewQueueManifest,
) -> Generator[ProposedChange, None, None]:  # noqa: UP043
    """Yield changes once in manifest order, despite cross-row references."""
    seen: set[str] = set()
    for batch in manifest.batches:
        for row in batch.rows:
            for change in row.changes:
                if change.id not in seen:
                    seen.add(change.id)
                    yield change


def _build_row_shell(raw: dict[str, str]) -> StagedRow:
    source_ids = tuple(sorted(set(_json_list(raw.get("source_submission_ids", "[]")))))
    account_id = raw.get("account_id", "").strip()
    case_id = raw.get("case_id", "").strip()
    row_id = stable_queue_id(
        "staged_row",
        object_name="Company_Profile_Change__c",
        source_submission_ids=source_ids,
        target_context=f"account:{account_id}|case:{case_id or 'unresolved'}",
    )
    warnings = tuple(
        QueueWarning("staging_warning", line)
        for line in raw.get("warnings", "").splitlines()
        if line.strip()
    )
    blockers: list[QueueBlocker] = []
    if not account_id:
        blockers.append(
            QueueBlocker("missing_account", "Submission Account must be resolved.")
        )
    elif any("could not be retrieved" in warning.message for warning in warnings):
        blockers.append(
            QueueBlocker(
                "missing_account_record", f"Account {account_id} is unavailable."
            )
        )
    match_status = raw.get("case_match_status", "").strip()
    if match_status == "ambiguous":
        blockers.append(
            QueueBlocker(
                "ambiguous_case", "Case matching is ambiguous and must not be guessed."
            )
        )
    elif not case_id or match_status != "matched":
        blockers.append(
            QueueBlocker("missing_case", "A Profile Update Case must be prepared.")
        )
    names = _json_list(raw.get("source_submission_names", "[]"))
    label = ", ".join(names) or ", ".join(source_ids) or "Unnamed staged row"
    references = [
        *(
            SalesforceReference(
                "Company_Profile_Change__c", source_id, relationship="source_submission"
            )
            for source_id in source_ids
        ),
    ]
    if account_id:
        references.append(
            SalesforceReference("Account", account_id, relationship="target_account")
        )
    references.extend(
        SalesforceReference(
            "Account",
            affected["id"],
            relationship="affected_account",
            label=affected["name"],
            status=affected["certification_status"],
        )
        for affected in _affected_accounts(raw)
        if affected["id"] != account_id or raw.get("is_parent_account") == "true"
    )
    if case_id:
        references.append(
            SalesforceReference("Case", case_id, relationship="profile_update_case")
        )
    try:
        prior_references = json.loads(raw.get("prior_activity_references", "") or "[]")
    except (TypeError, ValueError):
        prior_references = []
    if isinstance(prior_references, list):
        references.extend(
            SalesforceReference(
                str(reference.get("object_name", "")),
                str(reference.get("record_id", "")),
                relationship=str(reference.get("relationship", "prior_activity")),
                label=str(reference.get("label", "")),
                status=str(reference.get("status", "")),
            )
            for reference in prior_references
            if isinstance(reference, dict)
            and reference.get("object_name")
            and reference.get("record_id")
        )
    return StagedRow(
        id=row_id,
        label=label,
        status=QueueStatus.PENDING,
        warnings=warnings,
        blockers=tuple(blockers),
        source_submission_ids=source_ids,
        earliest_submission=raw.get("earliest_submission_date", "").strip(),
        changes=(),
        references=tuple(references),
    )


def _setup_changes(raw: dict[str, str], row: StagedRow) -> list[ProposedChange]:
    changes: list[ProposedChange] = []
    account_id = raw.get("account_id", "").strip()
    case_id = raw.get("case_id", "").strip()
    if not account_id:
        for source_id in row.source_submission_ids:
            changes.append(
                _change(
                    row,
                    phase=QueuePhase.SETUP,
                    object_name="Company_Profile_Change__c",
                    record_id=source_id,
                    target_context=source_id,
                    field="Account__c",
                    label="Resolve Submission Account",
                    current_value=None,
                    proposed_value=raw.get("certification_id", "").strip() or None,
                    context=(
                        "Proposed value is the Certification ID used to find the "
                        "Salesforce Account."
                    ),
                    ignore_parent_blockers={"missing_account", "missing_case"},
                )
            )
    match_status = raw.get("case_match_status", "").strip()
    changes.append(
        _change(
            row,
            phase=QueuePhase.SETUP,
            object_name="Case",
            record_id=case_id,
            target_context=case_id
            or f"account:{account_id}|sources:{','.join(row.source_submission_ids)}",
            field="Case",
            label="Prepare Profile Update Case",
            current_value=match_status or None,
            proposed_value="create_or_reuse",
            ignore_parent_blockers={"missing_case"},
        )
    )
    return changes


def _account_changes(raw: dict[str, str], row: StagedRow) -> list[ProposedChange]:
    submitted_parent_fields = _submitted_parent_account_fields(raw)
    return [
        _change(
            row,
            phase=QueuePhase.ACCOUNT,
            object_name="Account",
            record_id=affected["id"],
            target_context=affected["id"]
            or f"sources:{','.join(row.source_submission_ids)}",
            field=field,
            label=(
                f"{label} — {affected['name'] or affected['id']}"
                if raw.get("is_parent_account") == "true"
                else label
            ),
            current_value=None,
            proposed_value=proposed,
        )
        for affected in _affected_accounts(raw)
        for csv_name, field, label in ACCOUNT_PROPOSALS
        if (proposed := raw.get(csv_name, "").strip())
        and (submitted_parent_fields is None or field in submitted_parent_fields)
    ]


def _submitted_parent_account_fields(raw: dict[str, str]) -> set[str] | None:
    """Return staged submitted fields, or None for ordinary/legacy rows."""
    value = raw.get("submitted_account_fields", "").strip()
    if raw.get("is_parent_account") != "true" or not value:
        return None
    return set(_json_list(value))


def _build_contact_changes(
    rows: list[dict[str, str]], row_models: list[StagedRow]
) -> dict[str, tuple[ProposedChange, ...]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for raw, row in zip(rows, row_models, strict=True):
        try:
            resolutions = json.loads(raw.get("contact_resolutions", "") or "[]")
        except (TypeError, ValueError):
            resolutions = []
        if not isinstance(resolutions, list):
            resolutions = []
        for index, resolution in enumerate(resolutions):
            if not isinstance(resolution, dict):
                continue
            selected = resolution.get("selected_contact")
            selected = selected if isinstance(selected, dict) else {}
            record_id = str(selected.get("Id") or "").strip()
            comparison_key = str(
                resolution.get("comparison_key")
                or resolution.get("normalized_email")
                or f"unresolved:{row.id}:{index}"
            )
            target = record_id or f"email:{comparison_key}"
            submitted = resolution.get("submitted")
            if not isinstance(submitted, dict):
                submitted = {}
            sources = resolution.get("sources")
            if not isinstance(sources, list):
                sources = []
            source_ids = {
                str(source.get("submission_id", "")).strip()
                for source in sources
                if isinstance(source, dict)
                and str(source.get("submission_id", "")).strip()
            }
            source_ids.update(row.source_submission_ids)
            for suffix, field in CONTACT_FIELDS.items():
                proposed = submitted.get(suffix)
                if proposed in (None, ""):
                    continue
                entry = grouped.setdefault(
                    (target, field),
                    {
                        "record_id": record_id,
                        "source_ids": set(),
                        "row_ids": set(),
                        "values": [],
                        "current_values": [],
                        "warnings": [],
                        "blockers": [],
                    },
                )
                entry["source_ids"].update(source_ids)
                entry["row_ids"].add(row.id)
                entry["values"].append(proposed)
                entry["current_values"].append(selected.get(field))
                entry["warnings"].extend(
                    str(value) for value in resolution.get("warnings", []) if value
                )
                classification = str(resolution.get("classification", ""))
                if classification == "ambiguous":
                    entry["blockers"].append(
                        QueueBlocker(
                            "ambiguous_contact",
                            "Contact identity requires reviewer resolution.",
                        )
                    )

    by_row: dict[str, list[ProposedChange]] = {}
    shells = {row.id: row for row in row_models}
    for (target, field), entry in grouped.items():
        source_ids = tuple(sorted(entry["source_ids"]))
        row_ids = tuple(sorted(entry["row_ids"]))
        values = tuple(sorted(set(str(value) for value in entry["values"])))
        blockers = _unique_blockers(entry["blockers"])
        if len(values) > 1:
            blockers = _unique_blockers(
                [
                    *blockers,
                    QueueBlocker(
                        "conflicting_values", "Submitted Contact values conflict."
                    ),
                ]
            )
        warnings = tuple(
            QueueWarning("contact_warning", value)
            for value in sorted(set(entry["warnings"]))
        )
        change_id = stable_queue_id(
            "proposed_change",
            object_name="Contact",
            source_submission_ids=source_ids,
            target_context=target,
            field=field,
        )
        change = ProposedChange(
            id=change_id,
            label=f"Contact {field}",
            status=QueueStatus.BLOCKED if blockers else QueueStatus.PENDING,
            warnings=warnings,
            blockers=blockers,
            phase=QueuePhase.CONTACT,
            source_submission_ids=source_ids,
            source_row_ids=row_ids,
            salesforce=SalesforceReference("Contact", entry["record_id"], field),
            field=field,
            current_value=min(
                entry["current_values"],
                key=lambda value: json.dumps(value, default=str, sort_keys=True),
            ),
            proposed_value=values[0] if len(values) == 1 else values,
            context=target,
        )
        for row_id in row_ids:
            if row_id in shells:
                by_row.setdefault(row_id, []).append(change)
    return {row_id: tuple(changes) for row_id, changes in by_row.items()}


def _role_link_changes(raw: dict[str, str], row: StagedRow) -> list[ProposedChange]:
    changes = []
    for affected in _affected_accounts(raw):
        for role in ACCOUNT_ROLE_DEFINITIONS:
            if not any(
                raw.get(f"{role.prefix}_{suffix}", "").strip()
                for suffix, _ in role.submitted_fields
            ):
                continue
            proposed = raw.get(f"{role.prefix}_salesforce_contact_id", "").strip()
            action = raw.get(f"{role.prefix}_resolution_action", "").strip()
            blockers: tuple[QueueBlocker, ...] = ()
            if not proposed and action != "create_contact":
                blockers = (
                    QueueBlocker(
                        "unresolved_role_contact",
                        f"{role.label} Contact is unresolved.",
                    ),
                )
            label = f"{role.label} Contact role"
            if raw.get("is_parent_account") == "true":
                label += f" — {affected['name'] or affected['id']}"
            changes.append(
                _change(
                    row,
                    phase=QueuePhase.ROLE_LINK,
                    object_name="Account",
                    record_id=affected["id"],
                    target_context=affected["id"]
                    or f"sources:{','.join(row.source_submission_ids)}",
                    field=role.account_lookup,
                    label=label,
                    current_value=None,
                    proposed_value=proposed
                    or ("new Contact" if action == "create_contact" else None),
                    extra_blockers=blockers,
                )
            )
    return changes


def _affected_accounts(raw: dict[str, str]) -> list[dict[str, str]]:
    """Return deterministic Account targets, with legacy-row compatibility."""
    try:
        loaded = json.loads(raw.get("affected_accounts", "") or "[]")
    except (TypeError, ValueError):
        loaded = []
    affected: list[dict[str, str]] = []
    if isinstance(loaded, list):
        for item in loaded:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("id", "")).strip()
            if not account_id:
                continue
            affected.append(
                {
                    "id": account_id,
                    "name": str(item.get("name", "")).strip(),
                    "certification_status": str(
                        item.get("certification_status", "")
                    ).strip(),
                }
            )
    if not affected and raw.get("is_parent_account") != "true":
        account_id = raw.get("account_id", "").strip()
        if account_id:
            affected.append(
                {
                    "id": account_id,
                    "name": raw.get("account_name", "").strip(),
                    "certification_status": "",
                }
            )
    return sorted(affected, key=lambda item: item["id"])


def _change(
    row: StagedRow,
    *,
    phase: QueuePhase,
    object_name: str,
    record_id: str,
    target_context: str,
    field: str,
    label: str,
    current_value: Any,
    proposed_value: Any,
    context: str = "",
    ignore_parent_blockers: set[str] | None = None,
    extra_blockers: tuple[QueueBlocker, ...] = (),
) -> ProposedChange:
    ignored = ignore_parent_blockers or set()
    blockers = _unique_blockers(
        [blocker for blocker in row.blockers if blocker.code not in ignored]
        + list(extra_blockers)
    )
    return ProposedChange(
        id=stable_queue_id(
            "proposed_change",
            object_name=object_name,
            source_submission_ids=row.source_submission_ids,
            target_context=target_context,
            field=field,
        ),
        label=label,
        status=QueueStatus.BLOCKED if blockers else QueueStatus.PENDING,
        warnings=row.warnings,
        blockers=blockers,
        phase=phase,
        source_submission_ids=row.source_submission_ids,
        source_row_ids=(row.id,),
        salesforce=SalesforceReference(object_name, record_id, field),
        field=field,
        current_value=current_value,
        proposed_value=proposed_value,
        context=context,
    )


def _parent_status(items, blockers: tuple[QueueBlocker, ...]) -> QueueStatus:
    statuses = [item.status for item in items]
    if any(status is QueueStatus.IN_PROGRESS for status in statuses):
        return QueueStatus.IN_PROGRESS
    if any(status is QueueStatus.FAILED for status in statuses):
        return QueueStatus.FAILED
    if any(status is QueueStatus.STOPPED_EARLY for status in statuses):
        return QueueStatus.STOPPED_EARLY
    if blockers:
        return QueueStatus.BLOCKED
    if statuses and all(status is QueueStatus.COMPLETED for status in statuses):
        return QueueStatus.COMPLETED
    if statuses and all(
        status in {QueueStatus.BLOCKED, QueueStatus.COMPLETED} for status in statuses
    ):
        return QueueStatus.BLOCKED
    if not statuses:
        return QueueStatus.BLOCKED if blockers else QueueStatus.COMPLETED
    return QueueStatus.PENDING


def _change_sort_key(change: ProposedChange):
    return (
        PHASE_ORDER[change.phase],
        change.salesforce.object_name,
        change.salesforce.record_id or change.context,
        change.field,
        change.id,
    )


def _row_sort_key(row: StagedRow):
    return (
        _parse_datetime(row.earliest_submission) or _datetime_max(),
        row.source_submission_ids,
        row.id,
    )


def _batch_sort_key(batch: CaseBatch, overdue_before: datetime):
    key_date = _parse_datetime(batch.earliest_key_update)
    overdue = key_date is not None and key_date < overdue_before
    return (
        0 if overdue else 1,
        _parse_datetime(batch.earliest_submission) or _datetime_max(),
        batch.account.record_id,
        batch.case.record_id,
        batch.id,
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            return None
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _datetime_max() -> datetime:
    return datetime.max.replace(tzinfo=UTC)


def _json_list(value: str) -> list[str]:
    try:
        loaded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item).strip() for item in loaded if str(item).strip()]


def _unique_warnings(values) -> tuple[QueueWarning, ...]:
    return tuple(dict.fromkeys(values))


def _unique_blockers(values) -> tuple[QueueBlocker, ...]:
    return tuple(dict.fromkeys(values))


def _unique_references(values) -> tuple[SalesforceReference, ...]:
    return tuple(dict.fromkeys(values))
