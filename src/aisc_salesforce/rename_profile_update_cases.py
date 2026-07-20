"""Safely preview or apply one-time corrections to legacy Case subjects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .profile_update_subjects import (
    build_aisc_profile_update_subject,
    parse_legacy_profile_update_subject,
)
from .salesforce import SalesforceClient, SalesforceError

CHICAGO = ZoneInfo("America/Chicago")
RENAME_CASE_FIELDS = ["Id", "CaseNumber", "Subject", "CreatedDate"]


@dataclass
class RenameCounts:
    """Totals from one preview or apply run."""

    matched: int = 0
    updated: int = 0
    would_update: int = 0
    skipped: int = 0
    failed: int = 0


def correction_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return UTC bounds for today and the prior six Chicago calendar dates."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_today = current.astimezone(CHICAGO).date()
    local_start = datetime.combine(
        local_today - timedelta(days=6), time.min, tzinfo=CHICAGO
    )
    local_end = datetime.combine(
        local_today + timedelta(days=1), time.min, tzinfo=CHICAGO
    )
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


class RenameProfileUpdateCasesService:
    """Correct dated legacy subjects, using preview mode unless apply is explicit."""

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

    def run(self, *, apply: bool = False) -> RenameCounts:
        """Preview changes or PATCH only ``Subject`` when ``apply`` is true."""
        start, end = correction_window(self.now)
        cases = self.client.query_records(
            "Case",
            RENAME_CASE_FIELDS,
            where=(
                "Subject LIKE '%Profile Update Received%' AND "
                f"CreatedDate >= {_salesforce_datetime(start)} AND "
                f"CreatedDate < {_salesforce_datetime(end)}"
            ),
            order_by="CreatedDate ASC, Id ASC",
        )
        counts = RenameCounts()
        for case in cases:
            self._process_case(case, apply=apply, counts=counts)
        return counts

    def _process_case(
        self,
        case: dict[str, Any],
        *,
        apply: bool,
        counts: RenameCounts,
    ) -> None:
        case_id = _case_label(case)
        old_subject = _clean_text(case.get("Subject"))
        parsed = parse_legacy_profile_update_subject(old_subject)
        if parsed is None:
            counts.skipped += 1
            self.output_fn(
                f"{case_id}: skipped - subject has no trustworthy embedded "
                "received date."
            )
            return

        counts.matched += 1
        try:
            new_subject = build_aisc_profile_update_subject(
                parsed.account_name, parsed.updates
            )
        except ValueError as error:
            counts.skipped += 1
            self.output_fn(f"{case_id}: skipped - {error}")
            return

        if not apply:
            counts.would_update += 1
            self.output_fn(f"{case_id}: would update: {old_subject} -> {new_subject}")
            return

        try:
            self.client.update_record(
                "Case", _required_id(case), {"Subject": new_subject}
            )
        except SalesforceError as error:
            counts.failed += 1
            self.output_fn(f"{case_id}: failed - {error}")
            return
        counts.updated += 1
        self.output_fn(f"{case_id}: updated: {old_subject} -> {new_subject}")


def _salesforce_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _case_label(case: dict[str, Any]) -> str:
    return (
        _clean_text(case.get("CaseNumber"))
        or _clean_text(case.get("Id"))
        or "(unknown)"
    )


def _required_id(case: dict[str, Any]) -> str:
    case_id = _clean_text(case.get("Id"))
    if not case_id:
        raise SalesforceError("Case ID is missing.")
    return case_id


def _clean_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
