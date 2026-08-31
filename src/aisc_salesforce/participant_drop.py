"""Reusable participant-withdrawal intake and Account resolution workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ParticipantDropScenario(StrEnum):
    """The reason a participant withdrawal is being started."""

    UNPAID_INVOICE = "Unpaid Invoice"
    WITHDRAWAL_REQUEST = "Withdrawal Request"
    CRG_DROP = "CRG drop"
    OTHER = "Other participant drop"


@dataclass(frozen=True)
class AccountCandidate:
    """The small amount of Account information needed for human selection."""

    id: str
    name: str
    certification_id: str | None


@dataclass(frozen=True)
class ParticipantDropResult:
    """The outcome of one intake run."""

    account: AccountCandidate | None
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

    def show(self, message: str) -> None: ...


_REFERENCE_LOOKUPS = {
    ParticipantDropScenario.UNPAID_INVOICE: ("Cert_Invoice__c", "Name", "Cert_Account__c"),
    ParticipantDropScenario.WITHDRAWAL_REQUEST: (
        "Withdrawal_Request__c",
        "Name",
        "Account__c",
    ),
    ParticipantDropScenario.CRG_DROP: ("Cert_Audit__c", "Name", "Cert_Account__c"),
}
_NORMALIZED_SEARCH = re.compile(r"[^a-z0-9]+")


class ParticipantDropService:
    """Find one Account and record the start of its withdrawal in Chatter."""

    def __init__(self, client: object):
        self.client = client

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
            interaction.show("No Account matched that reference; using fallback search.")

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
                interaction.show("No Accounts matched that company name; try again or cancel.")
                continue
            if len(candidates) == 1:
                account = candidates[0]
                continue
            selected = interaction.select_account(candidates)
            if selected is None:
                return self._cancel(interaction)
            if selected not in candidates:
                raise ValueError("Selected Account was not one of the presented candidates.")
            account = selected

        message = f"Withdrawal in progress: {scenario.value}."
        self.client.post_feed_message(account.id, message)
        interaction.show(f"Withdrawal intake recorded for {account.name}.")
        return ParticipantDropResult(account)

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
            ["Id", "Name", "Certification_ID__c"],
            where=f"Id = '{_soql_literal(account_id)}'",
        )
        candidates = _account_candidates(records)
        return candidates[0] if len(candidates) == 1 else None

    def _find_by_certification_id(self, certification_id: str) -> AccountCandidate | None:
        normalized = _normalize_search(certification_id)
        if not normalized:
            return None
        records = self.client.query_records(
            "Account",
            ["Id", "Name", "Certification_ID__c"],
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
            ["Id", "Name", "Certification_ID__c"],
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


def _account_candidates(records: list[dict[str, object]]) -> tuple[AccountCandidate, ...]:
    """Turn valid Account query rows into unique candidates in Salesforce order."""
    candidates: list[AccountCandidate] = []
    known_ids: set[str] = set()
    for record in records:
        account_id = record.get("Id")
        name = record.get("Name")
        certification_id = record.get("Certification_ID__c")
        if not isinstance(account_id, str) or not isinstance(name, str) or account_id in known_ids:
            continue
        candidates.append(
            AccountCandidate(
                account_id,
                name,
                certification_id if isinstance(certification_id, str) else None,
            )
        )
        known_ids.add(account_id)
    return tuple(candidates)


def _normalize_search(value: str | None) -> str:
    return _NORMALIZED_SEARCH.sub("", (value or "").casefold())


def _soql_literal(value: str) -> str:
    """Escape user text placed inside one quoted SOQL literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")
