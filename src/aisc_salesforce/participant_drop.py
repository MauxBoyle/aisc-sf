"""Reusable participant-withdrawal intake and Account resolution workflow."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Protocol


class ParticipantDropAction(StrEnum):
    """The action selected before starting any Salesforce setup."""

    START = "Start a new withdrawal"
    COMPLETE = "Complete an existing withdrawal"


class ParticipantDropScenario(StrEnum):
    """The reason a participant withdrawal is being started."""

    UNPAID_INVOICE = "Unpaid Invoice"
    WITHDRAWAL_REQUEST = "Withdrawal Request"
    CRG_DROP = "CRG drop"
    OTHER = "Other participant drop"


class WithdrawalReason(StrEnum):
    """Reasons recorded for a participant withdrawal."""

    ECONOMY = "#Economy"
    CLOSED = "#Closed"
    ROI = "#ROI"
    NEW_OWNER = "#NewOwner"
    BUSINESS_MODEL = "#BusModel"
    CRG = "#CRG"
    NON_PAYMENT = "#NonPayment"

    # Keep these older Python member names available for existing callers.
    FACILITY_MAIN_OFFICE_CLOSURE = CLOSED
    NEW_OWNERSHIP = NEW_OWNER
    NEW_BUSINESS_MODEL = BUSINESS_MODEL


SELECTABLE_REASONS = (
    WithdrawalReason.ECONOMY,
    WithdrawalReason.CLOSED,
    WithdrawalReason.ROI,
    WithdrawalReason.NEW_OWNER,
    WithdrawalReason.BUSINESS_MODEL,
)
"""Reasons a person can select in withdrawal intake."""

ASSIGNED_REASONS = (WithdrawalReason.CRG, WithdrawalReason.NON_PAYMENT)
"""Process reasons assigned by the workflow instead of selected from Salesforce values."""

ALL_REASONS = (*SELECTABLE_REASONS, *ASSIGNED_REASONS)
"""Every supported withdrawal reason, in its workflow display order."""


@dataclass(frozen=True)
class AccountCandidate:
    """The small amount of Account information needed for human selection."""

    id: str
    name: str
    certification_id: str | None
    certification_notes: str | None = None


@dataclass(frozen=True)
class ParticipantDropResult:
    """The outcome of one intake run."""

    account: AccountCandidate | None
    withdrawal_reason: WithdrawalReason | None = None
    cancelled: bool = False


class ParticipantDropInteraction(Protocol):
    """UI boundary for the workflow; terminals are only one possible adapter."""

    def choose_scenario(self) -> ParticipantDropScenario | None: ...

    def request_reference(self, scenario: ParticipantDropScenario) -> str | None: ...

    def request_certification_id(self) -> str | None: ...

    def request_company_name(self) -> str | None: ...

    def select_account(
        self, candidates: tuple[AccountCandidate, ...]
    ) -> AccountCandidate | None: ...

    def choose_withdrawal_reason(
        self, default_reason: WithdrawalReason | None
    ) -> WithdrawalReason | None: ...

    def show(self, message: str) -> None: ...


_REFERENCE_LOOKUPS = {
    ParticipantDropScenario.UNPAID_INVOICE: (
        "Cert_Invoice__c",
        "Name",
        "Cert_Account__c",
    ),
    ParticipantDropScenario.WITHDRAWAL_REQUEST: (
        "Withdrawal_Request__c",
        "Name",
        "Account__c",
    ),
    ParticipantDropScenario.CRG_DROP: ("Cert_Audit__c", "Name", "Cert_Account__c"),
}
WITHDRAWAL_FOLLOW_UP_CONTACTS = (
    ("Data", "Maureen Boyle"),
    ("Department Head", "Lisa Patel"),
    ("Invoicing", "Karla Ruiz"),
    ("Audit Logistics", "Kim Swiss"),
)
"""Manual notification contacts, in the order shown after a withdrawal starts."""

_NORMALIZED_SEARCH = re.compile(r"[^a-z0-9]+")


class ParticipantDropService:
    """Find one Account and record the start of its withdrawal."""

    def __init__(
        self, client: object, *, date_provider: Callable[[], date] = date.today
    ):
        self.client = client
        self.date_provider = date_provider

    def run(self, interaction: ParticipantDropInteraction) -> ParticipantDropResult:
        """Run intake, posting only after one Account has been resolved."""
        scenario = interaction.choose_scenario()
        if scenario is None:
            return self._cancel(interaction)

        reference = interaction.request_reference(scenario)
        if reference is None:
            return self._cancel(interaction)
        account = self._find_by_reference(scenario, reference)
        if account is None and reference.strip():
            interaction.show(
                "No Account matched that reference; using fallback search."
            )

        if account is None:
            certification_id = interaction.request_certification_id()
            if certification_id is None:
                return self._cancel(interaction)
            account = self._find_by_certification_id(certification_id)
            if account is None and certification_id.strip():
                interaction.show(
                    "No Account matched that Certification ID; using company-name search."
                )

        while account is None:
            company_name = interaction.request_company_name()
            if company_name is None:
                return self._cancel(interaction)
            candidates = self._find_by_company_name(company_name)
            if not candidates:
                interaction.show(
                    "No Accounts matched that company name; try again or cancel."
                )
                continue
            if len(candidates) == 1:
                account = candidates[0]
                continue
            selected = interaction.select_account(candidates)
            if selected is None:
                return self._cancel(interaction)
            if selected not in candidates:
                raise ValueError(
                    "Selected Account was not one of the presented candidates."
                )
            account = selected

        withdrawal_reason = self._choose_withdrawal_reason(interaction, scenario)
        if withdrawal_reason is None:
            return self._cancel(interaction)

        note_text = _append_withdrawal_note(
            account.certification_notes,
            _format_withdrawal_note(self.date_provider(), withdrawal_reason, reference),
        )
        self.client.update_record("Account", account.id, {"Cert_Notes__c": note_text})

        message = f"Withdrawal in progress: {scenario.value}."
        self.client.post_feed_message(account.id, message)
        interaction.show(f"Withdrawal intake recorded for {account.name}.")
        for reminder in _manual_follow_up_reminders():
            interaction.show(reminder)
        return ParticipantDropResult(account, withdrawal_reason)

    @staticmethod
    def _choose_withdrawal_reason(
        interaction: ParticipantDropInteraction, scenario: ParticipantDropScenario
    ) -> WithdrawalReason | None:
        if scenario is ParticipantDropScenario.CRG_DROP:
            return WithdrawalReason.CRG

        default_reason = (
            WithdrawalReason.NON_PAYMENT
            if scenario is ParticipantDropScenario.UNPAID_INVOICE
            else None
        )
        reason = interaction.choose_withdrawal_reason(default_reason)
        allowed_reasons = SELECTABLE_REASONS + (
            (default_reason,) if default_reason is not None else ()
        )
        if reason is not None and reason not in allowed_reasons:
            raise ValueError(
                "Selected withdrawal reason is not allowed for this scenario."
            )
        return reason

    def _find_by_reference(
        self, scenario: ParticipantDropScenario, reference: str
    ) -> AccountCandidate | None:
        if not reference.strip() or scenario not in _REFERENCE_LOOKUPS:
            return None
        object_name, key_field, account_field = _REFERENCE_LOOKUPS[scenario]
        records = self.client.query_records(
            object_name,
            [key_field, account_field],
            where=f"{key_field} = '{_soql_literal(reference.strip())}'",
        )
        account_ids = {
            record.get(account_field)
            for record in records
            if isinstance(record.get(account_field), str) and record[account_field]
        }
        if len(account_ids) != 1:
            return None
        return self._find_account_by_id(account_ids.pop())

    def _find_account_by_id(self, account_id: str) -> AccountCandidate | None:
        records = self.client.query_records(
            "Account",
            ["Id", "Name", "Certification_ID__c", "Cert_Notes__c"],
            where=f"Id = '{_soql_literal(account_id)}'",
        )
        candidates = _account_candidates(records)
        return candidates[0] if len(candidates) == 1 else None

    def _find_by_certification_id(
        self, certification_id: str
    ) -> AccountCandidate | None:
        normalized = _normalize_search(certification_id)
        if not normalized:
            return None
        records = self.client.query_records(
            "Account",
            ["Id", "Name", "Certification_ID__c", "Cert_Notes__c"],
            where="Certification_ID__c != NULL",
        )
        candidates = [
            candidate
            for candidate in _account_candidates(records)
            if _normalize_search(candidate.certification_id) == normalized
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _find_by_company_name(self, company_name: str) -> tuple[AccountCandidate, ...]:
        normalized = _normalize_search(company_name)
        if not normalized:
            return ()
        records = self.client.query_records(
            "Account",
            ["Id", "Name", "Certification_ID__c", "Cert_Notes__c"],
            where="Name != NULL",
        )
        return tuple(
            candidate
            for candidate in _account_candidates(records)
            if normalized in _normalize_search(candidate.name)
        )

    @staticmethod
    def _cancel(interaction: ParticipantDropInteraction) -> ParticipantDropResult:
        interaction.show("Participant drop cancelled; no Salesforce changes were made.")
        return ParticipantDropResult(None, cancelled=True)


def _account_candidates(
    records: list[dict[str, object]],
) -> tuple[AccountCandidate, ...]:
    """Turn valid Account query rows into unique candidates in Salesforce order."""
    candidates: list[AccountCandidate] = []
    known_ids: set[str] = set()
    for record in records:
        account_id = record.get("Id")
        name = record.get("Name")
        certification_id = record.get("Certification_ID__c")
        certification_notes = record.get("Cert_Notes__c")
        if (
            not isinstance(account_id, str)
            or not isinstance(name, str)
            or account_id in known_ids
        ):
            continue
        candidates.append(
            AccountCandidate(
                account_id,
                name,
                certification_id if isinstance(certification_id, str) else None,
                certification_notes if isinstance(certification_notes, str) else None,
            )
        )
        known_ids.add(account_id)
    return tuple(candidates)


def _normalize_search(value: str | None) -> str:
    return _NORMALIZED_SEARCH.sub("", (value or "").casefold())


def _soql_literal(value: str) -> str:
    """Escape user text placed inside one quoted SOQL literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _format_withdrawal_note(
    withdrawal_date: date, reason: WithdrawalReason, reference: str
) -> str:
    """Create the dated Certification Notes entry for one withdrawal."""
    formatted_date = f"{withdrawal_date.month}/{withdrawal_date.day}/{withdrawal_date.year % 100:02d}"
    entry = f"{formatted_date} Withdrawal: {reason.value}"
    return f"{entry} {reference.strip()}" if reference.strip() else entry


def _manual_follow_up_reminders() -> tuple[str, ...]:
    """Return the terminal-only tasks required after both Salesforce writes."""
    audit_logistics_name = dict(WITHDRAWAL_FOLLOW_UP_CONTACTS)["Audit Logistics"]
    notifications = tuple(
        f"  - Notify {role}: {name}." for role, name in WITHDRAWAL_FOLLOW_UP_CONTACTS
    )
    return (
        "Manual follow-up required:",
        *notifications,
        (
            "  - Ask Audit Logistics "
            f"({audit_logistics_name}) to remove the Account's Audit Package."
        ),
        (
            "These notifications and Audit Package removal are not performed or "
            "verified by the script."
        ),
    )


def _append_withdrawal_note(existing_note: str | None, entry: str) -> str:
    """Append an entry while preserving all existing Certification Notes text."""
    if not existing_note:
        return entry
    separator = "" if existing_note.endswith("\n") else "\n"
    return f"{existing_note}{separator}{entry}"
