"""Read-only audit of recently stored Salesforce picklist values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .salesforce import SalesforceClient, SalesforceError
from .salesforce_enums import SALESFORCE_ENUMS

PICKLIST_TYPES = frozenset({"picklist", "multipicklist"})


class PicklistAuditError(ValueError):
    """The audit input or Salesforce record data was not usable."""


@dataclass(frozen=True)
class PicklistAuditFinding:
    """Unknown values observed for one Salesforce picklist field."""

    object_name: str
    field_name: str
    values: tuple[str, ...]
    has_enum: bool


@dataclass(frozen=True)
class PicklistAuditResult:
    """Stable findings and the lower date boundary used by one audit."""

    cutoff: datetime
    findings: tuple[PicklistAuditFinding, ...]


class PicklistEnumAuditService:
    """Compare recently stored picklist values with the Python enum catalog."""

    def __init__(
        self,
        client: SalesforceClient,
        *,
        cutoff: datetime | None = None,
        now: datetime | None = None,
        enum_catalog: Mapping[tuple[str, str], type[StrEnum]] = SALESFORCE_ENUMS,
    ):
        if cutoff is not None and now is not None:
            raise PicklistAuditError("Provide either cutoff or now, not both.")
        current = now or datetime.now(UTC)
        self.cutoff = (
            _as_utc(cutoff) if cutoff is not None else two_year_cutoff(current)
        )
        self.client = client
        self.enum_catalog = enum_catalog

    def audit(self, inventory: Mapping[str, Sequence[str]]) -> PicklistAuditResult:
        """Describe inventoried objects and report unknown recent values."""
        findings: list[PicklistAuditFinding] = []

        for object_name in sorted(inventory):
            field_types = self.client.describe_object(object_name)
            picklist_fields = [
                field_name
                for field_name in sorted(set(inventory[object_name]))
                if field_types.get(field_name) in PICKLIST_TYPES
            ]
            if not picklist_fields:
                continue

            date_field = audit_date_field(object_name)
            where = f"{date_field} >= {_salesforce_datetime(self.cutoff)}"
            records = self.client.query_records(
                object_name,
                picklist_fields,
                where=where,
                order_by="Id ASC",
            )
            for field_name in picklist_fields:
                values = _observed_values(
                    records,
                    field_name,
                    multipicklist=field_types[field_name] == "multipicklist",
                    object_name=object_name,
                )
                enum_type = self.enum_catalog.get((object_name, field_name))
                known_values = (
                    {member.value for member in enum_type} if enum_type else set()
                )
                missing = tuple(sorted(values - known_values))
                if missing:
                    findings.append(
                        PicklistAuditFinding(
                            object_name,
                            field_name,
                            missing,
                            has_enum=enum_type is not None,
                        )
                    )

        return PicklistAuditResult(self.cutoff, tuple(findings))


def audit_date_field(object_name: str) -> str:
    """Return the date field used to select recent records for an object.

    Salesforce field-history objects do not have ``LastModifiedDate``. Their
    immutable history entries are dated with ``CreatedDate`` instead.
    """
    return "CreatedDate" if object_name.endswith("History") else "LastModifiedDate"


def two_year_cutoff(now: datetime) -> datetime:
    """Move a UTC instant back two calendar years.

    February 29 becomes February 28 when the target year is not a leap year.
    """
    current = _as_utc(now)
    try:
        return current.replace(year=current.year - 2)
    except ValueError:
        if current.month == 2 and current.day == 29:
            return current.replace(year=current.year - 2, day=28)
        raise


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PicklistAuditError("Audit dates must include a UTC offset.")
    return value.astimezone(UTC)


def _salesforce_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _observed_values(
    records: Sequence[Mapping[str, Any]],
    field_name: str,
    *,
    multipicklist: bool,
    object_name: str,
) -> set[str]:
    values: set[str] = set()
    for record in records:
        raw_value = record.get(field_name)
        if raw_value is None or raw_value == "":
            continue
        if not isinstance(raw_value, str):
            raise SalesforceError(
                f"Invalid picklist value for {object_name}.{field_name}."
            )
        candidates = raw_value.split(";") if multipicklist else (raw_value,)
        values.update(value for value in candidates if value != "")
    return values
