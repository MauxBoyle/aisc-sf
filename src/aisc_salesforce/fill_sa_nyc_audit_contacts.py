"""Fill missing SA-NYC Audit contact lookups from their participant Accounts."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .salesforce import SalesforceClient, SalesforceError


@dataclass(frozen=True)
class ContactRoleMapping:
    """One Audit lookup and the Account lookup that supplies it."""

    label: str
    audit_field: str
    account_field: str


CONTACT_ROLE_MAPPINGS = (
    ContactRoleMapping(
        "Principal", "Principal_Contact__c", "Cert_Principal_Contact__c"
    ),
    ContactRoleMapping("AP", "AP_Contact__c", "Cert_Accounting_Contact__c"),
    ContactRoleMapping("QC", "QC_Contact__c", "Cert_Marketing_Contact__c"),
    ContactRoleMapping("New York", "New_York_Contact__c", "Cert_Safety_Contact__c"),
)

SA_NYC_AUDIT_FIELDS = (
    "Id",
    "CreatedDate",
    "Cert_Audit_Type__c",
    "Cert_Account__c",
    *(mapping.audit_field for mapping in CONTACT_ROLE_MAPPINGS),
    "Cert_Account__r.NY_Program_Participant__c",
    *(f"Cert_Account__r.{mapping.account_field}" for mapping in CONTACT_ROLE_MAPPINGS),
)


class AuditContactMetadataError(ValueError):
    """A required Salesforce lookup is missing or has an unsafe field type."""


@dataclass
class AuditContactFillCounts:
    """Totals from one preview or apply run.

    Contact outcome totals count blank Audit role fields. ``qualifying`` counts
    the returned Audits, while the other values count role-level outcomes.
    """

    qualifying: int = 0
    updated: int = 0
    would_update: int = 0
    unavailable: int = 0
    failed: int = 0


def thirteen_month_cutoff(now: datetime) -> datetime:
    """Return the matching UTC instant thirteen calendar months earlier.

    When the prior month lacks the matching day, use that month's final day.
    """
    utc_now = now.astimezone(UTC)
    total_months = utc_now.year * 12 + utc_now.month - 1 - 13
    year, month_index = divmod(total_months, 12)
    month = month_index + 1
    day = min(utc_now.day, monthrange(year, month)[1])
    return utc_now.replace(year=year, month=month, day=day)


class SANYCAuditContactFillService:
    """Preview or safely fill missing SA-NYC Audit contact lookups."""

    def __init__(
        self,
        client: SalesforceClient,
        *,
        now: datetime | None = None,
        output_fn: Callable[[str], None] = print,
    ):
        self.client = client
        self.now = now
        self.output_fn = output_fn

    def run(self, *, apply: bool = False) -> AuditContactFillCounts:
        """Validate metadata, then preview or PATCH eligible Audit lookups."""
        # Capture the run instant before network calls so the rolling window is
        # stable even when metadata preflight takes noticeable time.
        run_time = self.now or datetime.now(UTC)
        self._validate_metadata()
        cutoff = thirteen_month_cutoff(run_time)
        audits = self.client.query_records(
            "Cert_Audit__c",
            SA_NYC_AUDIT_FIELDS,
            where=(
                "Cert_Audit_Type__c = 'SA-NYC' AND "
                f"CreatedDate >= {_salesforce_datetime(cutoff)} AND "
                "Cert_Account__r.NY_Program_Participant__c = true"
            ),
            order_by="CreatedDate ASC, Id ASC",
        )
        counts = AuditContactFillCounts(qualifying=len(audits))
        for audit in audits:
            self._process_audit(audit, apply=apply, counts=counts)
        return counts

    def _validate_metadata(self) -> None:
        audit_fields = self.client.describe_object("Cert_Audit__c")
        account_fields = self.client.describe_object("Account")
        for mapping in CONTACT_ROLE_MAPPINGS:
            _require_reference_field("Cert_Audit__c", audit_fields, mapping.audit_field)
            _require_reference_field("Account", account_fields, mapping.account_field)

    def _process_audit(
        self,
        audit: dict[str, Any],
        *,
        apply: bool,
        counts: AuditContactFillCounts,
    ) -> None:
        audit_label = _audit_label(audit)
        account = audit.get("Cert_Account__r")
        account_values = account if isinstance(account, dict) else {}
        candidates: list[ContactRoleMapping] = []
        payload: dict[str, str] = {}

        for mapping in CONTACT_ROLE_MAPPINGS:
            if _has_value(audit.get(mapping.audit_field)):
                self.output_fn(f"{audit_label} {mapping.label}: already populated")
                continue
            account_contact_id = _clean_text(account_values.get(mapping.account_field))
            if not account_contact_id:
                counts.unavailable += 1
                self.output_fn(f"{audit_label} {mapping.label}: unavailable")
                continue
            candidates.append(mapping)
            payload[mapping.audit_field] = account_contact_id

        if not candidates:
            return
        if not apply:
            counts.would_update += len(candidates)
            for mapping in candidates:
                self.output_fn(f"{audit_label} {mapping.label}: would update")
            return

        try:
            self.client.update_record("Cert_Audit__c", _required_id(audit), payload)
        except SalesforceError as error:
            counts.failed += len(candidates)
            for mapping in candidates:
                self.output_fn(f"{audit_label} {mapping.label}: failed - {error}")
            return
        counts.updated += len(candidates)
        for mapping in candidates:
            self.output_fn(f"{audit_label} {mapping.label}: updated")


def _require_reference_field(
    object_name: str, field_types: dict[str, str], field_name: str
) -> None:
    field_type = field_types.get(field_name)
    if field_type != "reference":
        detail = "missing" if field_type is None else f"type {field_type!r}"
        raise AuditContactMetadataError(
            f"{object_name}.{field_name} must be a reference field ({detail})."
        )


def _salesforce_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_label(audit: dict[str, Any]) -> str:
    return _clean_text(audit.get("Id")) or "(unknown Audit)"


def _required_id(audit: dict[str, Any]) -> str:
    audit_id = _clean_text(audit.get("Id"))
    if not audit_id:
        raise SalesforceError("Audit ID is missing.")
    return audit_id


def _has_value(value: Any) -> bool:
    return bool(_clean_text(value)) if isinstance(value, str) else value is not None


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
