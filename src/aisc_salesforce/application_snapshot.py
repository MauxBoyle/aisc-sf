"""Build a read-only CSV summary of Salesforce certification applications."""

from __future__ import annotations

import csv
import os
import shutil
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

RECORD_TYPE_ALIASES = {
    "0125w000000BaAdAAK": "Scope Change",
    "0125w000000BVTTAA4": "Web Question",
    "0125w000000BXdEAAW": "Audit Date",
    "0125w000000BZyIAAG": "Membership",
    "0125w0000013incAAA": "Erector Application",
    "0125w0000013inhAAA": "Fabricator Application",
    "0125w0000013inrAAA": "International Application",
    "012f20000012CCDAA2": "Certification",
    "012f20000012CCEAA2": "Solutions Center",
}

APPLICATION_RECORD_TYPE_IDS = frozenset(
    record_type_id
    for record_type_id, alias in RECORD_TYPE_ALIASES.items()
    if alias
    in {
        "Fabricator Application",
        "Erector Application",
        "International Application",
    }
)

CASE_FIELDS = (
    "Id",
    "AccountId",
    "CreatedDate",
    "RecordTypeId",
    "Cert_Stage__c",
    "Cert_Is_this_a_scope_change__c",
    "Cert_Expedited_Application__c",
    "Account.BillingCountry",
    "Account.Cert_Certification_Status__c",
)

AUDIT_FIELDS = (
    "Id",
    "Cert_Account__c",
    "Cert_Audit_Date__c",
    "CreatedDate",
    "Cert_Audit_Status__c",
    "Cert_Audit_Type__c",
)

APPLICATION_STAGES = (
    "Initial Review",
    "Eligibility Review",
    "Doc Audit",
    "Awaiting Audit Assignment",
    "Awaiting Audit",
    "Awaiting CRG Decision",
)

CSV_COLUMNS = (
    "application_stage",
    "domestic_expedited",
    "domestic_regular",
    "international_regular",
)

# Keep Salesforce picklist values in one place so query and Python filters agree.
CASE_CANCELLED_STAGE = "Cancel"
INVALID_AUDIT_STATUSES = frozenset({"Canceled", "Withdrawn"})
INVALID_AUDIT_TYPES = frozenset({"Additional", "Appeal", "SA-NYC", "Preassessment"})

_CASE_WHERE = (
    "RecordTypeId IN "
    "('0125w0000013incAAA', '0125w0000013inhAAA', '0125w0000013inrAAA') "
    "AND Account.Cert_Certification_Status__c = 'Initials' "
    f"AND (Cert_Stage__c != '{CASE_CANCELLED_STAGE}' OR Cert_Stage__c = NULL) "
    "AND (Cert_Is_this_a_scope_change__c != 'Yes' "
    "OR Cert_Is_this_a_scope_change__c = NULL)"
)

_AUDIT_WHERE = (
    "(Cert_Audit_Status__c NOT IN ('Canceled', 'Withdrawn') "
    "OR Cert_Audit_Status__c = NULL) "
    "AND (Cert_Audit_Type__c NOT IN "
    "('Additional', 'Appeal', 'SA-NYC', 'Preassessment') "
    "OR Cert_Audit_Type__c = NULL)"
)


class ApplicationSnapshotError(ValueError):
    """Salesforce report data could not be classified safely."""


@dataclass(frozen=True)
class ApplicationSnapshotResult:
    """Rows and summary metadata for one application snapshot."""

    rows: Sequence[Mapping[str, str | int]]
    qualifying_case_count: int
    unexpected_stages: Mapping[str, int]


class ApplicationSnapshotService:
    """Query Salesforce and build one in-memory application snapshot."""

    def __init__(self, client: Any, *, today: date | None = None):
        self.client = client
        self.today = today or datetime.now(ZoneInfo("America/Chicago")).date()

    def build(self) -> ApplicationSnapshotResult:
        """Query Cases and Audits through the paginated Salesforce client."""
        cases = self.client.query_records(
            "Case",
            list(CASE_FIELDS),
            where=_CASE_WHERE,
        )
        audits = self.client.query_records(
            "Cert_Audit__c",
            list(AUDIT_FIELDS),
            where=_AUDIT_WHERE,
        )
        return aggregate_application_snapshot(
            cases,
            audits,
            today=self.today,
        )


def is_qualifying_case(record: Mapping[str, Any]) -> bool:
    """Return whether a Case passes every application report filter."""
    account = record.get("Account")
    return (
        isinstance(account, Mapping)
        and account.get("Cert_Certification_Status__c") == "Initials"
        and record.get("Cert_Stage__c") != CASE_CANCELLED_STAGE
        and record.get("Cert_Is_this_a_scope_change__c") != "Yes"
        and record.get("RecordTypeId") in APPLICATION_RECORD_TYPE_IDS
    )


def is_valid_audit(record: Mapping[str, Any]) -> bool:
    """Return whether an Audit can influence an application's stage."""
    return (
        record.get("Cert_Audit_Status__c") not in INVALID_AUDIT_STATUSES
        and record.get("Cert_Audit_Type__c") not in INVALID_AUDIT_TYPES
    )


def select_latest_valid_audits(
    audits: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Select the greatest valid Audit ranking key for each Account."""
    selected: dict[str, Mapping[str, Any]] = {}
    selected_keys: dict[str, tuple[date, datetime, str]] = {}
    for audit in audits:
        if not is_valid_audit(audit):
            continue
        account_id = audit.get("Cert_Account__c")
        if not isinstance(account_id, str) or not account_id:
            continue
        ranking_key = _audit_ranking_key(audit)
        if account_id not in selected_keys or ranking_key > selected_keys[account_id]:
            selected[account_id] = audit
            selected_keys[account_id] = ranking_key
    return selected


def application_stage(
    case: Mapping[str, Any],
    audit: Mapping[str, Any] | None,
    *,
    today: date,
) -> str:
    """Apply the supplied Tableau application-stage decision order."""
    audit_status = audit.get("Cert_Audit_Status__c") if audit else None
    audit_date_value = audit.get("Cert_Audit_Date__c") if audit else None
    case_stage = case.get("Cert_Stage__c")

    if case_stage is None:
        return "Initial Review"

    normalized_stage = str(case_stage).replace("_", " ")
    if normalized_stage == "New Application":
        return "Initial Review"
    if case_stage == "Doc_Audit" and audit_date_value is not None:
        return _stage_for_audit_date(audit_date_value, today=today)
    if audit_status == "Pending Acceptance":
        return "Awaiting Audit Assignment"
    if case_stage != "Pending_AuditAssignment":
        return normalized_stage
    if audit_status == "Reschedule in Progress" or audit_date_value is None:
        return "Awaiting Audit Assignment"

    return _stage_for_audit_date(audit_date_value, today=today)


def _stage_for_audit_date(audit_date_value: Any, *, today: date) -> str:
    """Return the application stage determined by an Audit's date."""
    audit_date = _salesforce_date(audit_date_value, field_name="Cert_Audit_Date__c")
    return "Awaiting Audit" if audit_date >= today else "Awaiting CRG Decision"


def classify_application_type(case: Mapping[str, Any]) -> str:
    """Return the report column for a Case's origin and speed."""
    account = case.get("Account")
    billing_country = (
        account.get("BillingCountry") if isinstance(account, Mapping) else None
    )
    if billing_country != "United States":
        return "international_regular"
    if case.get("Cert_Expedited_Application__c") is True:
        return "domestic_expedited"
    return "domestic_regular"


def aggregate_application_snapshot(
    cases: Sequence[Mapping[str, Any]],
    audits: Sequence[Mapping[str, Any]],
    *,
    today: date,
) -> ApplicationSnapshotResult:
    """Filter and count Cases into the fixed application snapshot cross-tab."""

    qualifying_cases: list[Mapping[str, Any]] = []
    seen_case_ids: set[str] = set()
    account_ids: set[str] = set()
    for case in cases:
        if not is_qualifying_case(case):
            continue
        case_id = case.get("Id")
        if not isinstance(case_id, str) or not case_id:
            raise ApplicationSnapshotError("A qualifying Case record is missing Id.")
        if case_id in seen_case_ids:
            continue
        account_id = case.get("AccountId")
        if not isinstance(account_id, str) or not account_id:
            raise ApplicationSnapshotError(
                f"Qualifying Case {case_id} is missing AccountId."
            )
        seen_case_ids.add(case_id)
        account_ids.add(account_id)
        qualifying_cases.append(case)

    status_audits = [
        audit
        for audit in audits
        if audit.get("Cert_Audit_Status__c") not in INVALID_AUDIT_STATUSES
    ]
    valid_audits = [
        audit
        for audit in status_audits
        if audit.get("Cert_Audit_Type__c") not in INVALID_AUDIT_TYPES
    ]
    relevant_audits = [
        audit for audit in valid_audits if audit.get("Cert_Account__c") in account_ids
    ]
    audits_by_case = _select_latest_valid_audits_by_case(
        qualifying_cases,
        relevant_audits,
    )
    counts: dict[str, Counter[str]] = {}
    for case in qualifying_cases:
        stage = application_stage(
            case,
            audits_by_case[case["Id"]],
            today=today,
        )
        counts.setdefault(stage, Counter())[classify_application_type(case)] += 1

    unexpected_labels = sorted(set(counts) - set(APPLICATION_STAGES))
    stage_labels = [*APPLICATION_STAGES, *unexpected_labels]
    rows = tuple(
        {
            "application_stage": stage,
            "domestic_regular": counts.get(stage, Counter())["domestic_regular"],
            "domestic_expedited": counts.get(stage, Counter())["domestic_expedited"],
            "international_regular": counts.get(stage, Counter())[
                "international_regular"
            ],
        }
        for stage in stage_labels
    )
    unexpected_stages = {
        stage: sum(counts[stage].values()) for stage in unexpected_labels
    }
    return ApplicationSnapshotResult(
        rows=rows,
        qualifying_case_count=len(qualifying_cases),
        unexpected_stages=unexpected_stages,
    )


def _select_latest_valid_audits_by_case(
    cases: Sequence[Mapping[str, Any]],
    audits: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any] | None]:
    """Select each Case's newest Audit created on or after the Case."""
    audits_by_account: dict[str, list[Mapping[str, Any]]] = {}
    for audit in audits:
        account_id = audit.get("Cert_Account__c")
        if isinstance(account_id, str) and account_id:
            audits_by_account.setdefault(account_id, []).append(audit)

    selected: dict[str, Mapping[str, Any] | None] = {}
    for case in cases:
        case_id = case["Id"]
        case_created = _salesforce_datetime(
            case.get("CreatedDate"),
            field_name="Case CreatedDate",
        )
        matching_audits = [
            audit
            for audit in audits_by_account.get(case["AccountId"], [])
            if _salesforce_datetime(
                audit.get("CreatedDate"),
                field_name="Audit CreatedDate",
            )
            >= case_created
        ]
        selected[case_id] = select_latest_valid_audits(matching_audits).get(
            case["AccountId"]
        )
    return selected


def write_application_snapshot(
    result: ApplicationSnapshotResult,
    output_dir: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Atomically publish one timestamped ``application_snapshot.csv``."""
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    folder_name = timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = _available_output_path(output_dir, folder_name)
    temporary_path = output_dir / f".{folder_name}-{uuid4().hex}.tmp"
    try:
        temporary_path.mkdir()
        _write_application_csv(
            temporary_path / "application_snapshot.csv",
            result.rows,
        )
        os.replace(temporary_path, final_path)
    except Exception:
        shutil.rmtree(temporary_path, ignore_errors=True)
        raise
    return final_path


def _audit_ranking_key(
    audit: Mapping[str, Any],
) -> tuple[date, datetime, str]:
    created = _salesforce_datetime(
        audit.get("CreatedDate"),
        field_name="CreatedDate",
    )
    audit_date_value = audit.get("Cert_Audit_Date__c")
    effective_date = (
        _salesforce_date(
            audit_date_value,
            field_name="Cert_Audit_Date__c",
        )
        if audit_date_value is not None
        else created.date()
    )
    return effective_date, created, str(audit.get("Id") or "")


def _salesforce_date(value: Any, *, field_name: str) -> date:
    if not isinstance(value, str):
        raise ApplicationSnapshotError(
            f"Salesforce {field_name} must be an ISO date; received {value!r}."
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ApplicationSnapshotError(
            f"Salesforce {field_name} has an invalid date: {value!r}."
        ) from error


def _salesforce_datetime(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ApplicationSnapshotError(
            f"Salesforce {field_name} must be an ISO date/time; received {value!r}."
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ApplicationSnapshotError(
            f"Salesforce {field_name} has an invalid date/time: {value!r}."
        ) from error
    if parsed.tzinfo is None:
        raise ApplicationSnapshotError(
            f"Salesforce {field_name} is missing a timezone: {value!r}."
        )
    return parsed.astimezone(UTC)


def _available_output_path(output_dir: Path, folder_name: str) -> Path:
    candidate = output_dir / folder_name
    number = 1
    while candidate.exists():
        candidate = output_dir / f"{folder_name}-{number:02d}"
        number += 1
    return candidate


def _write_application_csv(
    path: Path,
    rows: Sequence[Mapping[str, str | int]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
