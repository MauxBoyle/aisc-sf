"""Deterministic, UI-neutral queue for Profile Update review work.

The queue intentionally contains no run timestamp.  The timestamped artifact
directory already records when a run started, while omitting a timestamp here
makes identical Salesforce input produce identical JSON bytes.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from .stage_profile_updates import ROLE_DEFINITIONS

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
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
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


def iter_changes(manifest: ReviewQueueManifest):
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
                    context="Proposed value is the Profile ID used to choose an Account.",
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
    account_id = raw.get("account_id", "").strip()
    return [
        _change(
            row,
            phase=QueuePhase.ACCOUNT,
            object_name="Account",
            record_id=account_id,
            target_context=account_id
            or f"sources:{','.join(row.source_submission_ids)}",
            field=field,
            label=label,
            current_value=None,
            proposed_value=proposed,
        )
        for csv_name, field, label in ACCOUNT_PROPOSALS
        if (proposed := raw.get(csv_name, "").strip())
    ]


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
    account_id = raw.get("account_id", "").strip()
    changes = []
    for role in ROLE_DEFINITIONS:
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
                    "unresolved_role_contact", f"{role.label} Contact is unresolved."
                ),
            )
        changes.append(
            _change(
                row,
                phase=QueuePhase.ROLE_LINK,
                object_name="Account",
                record_id=account_id,
                target_context=account_id
                or f"sources:{','.join(row.source_submission_ids)}",
                field=role.account_lookup,
                label=f"{role.label} Contact role",
                current_value=None,
                proposed_value=proposed
                or ("new Contact" if action == "create_contact" else None),
                extra_blockers=blockers,
            )
        )
    return changes


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
    if statuses and all(status is QueueStatus.COMPLETED for status in statuses):
        return QueueStatus.COMPLETED
    if blockers or (
        statuses
        and all(
            status in {QueueStatus.BLOCKED, QueueStatus.COMPLETED}
            for status in statuses
        )
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
