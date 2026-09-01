"""Terminal adapter for the participant-drop workflow."""

from __future__ import annotations

from collections.abc import Callable

from .participant_drop import (
    AccountCandidate,
    ParticipantDropAction,
    ParticipantDropScenario,
)


class CLIParticipantDropInteraction:
    """Ask terminal users for the values required by participant-drop intake."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ):
        self.input_fn = input_fn
        self.output_fn = output_fn

    def choose_action(self) -> ParticipantDropAction | None:
        choices = tuple(ParticipantDropAction)
        self.output_fn("Participant-drop action:")
        for index, action in enumerate(choices, start=1):
            self.output_fn(f"  {index}. {action.value}")
        while True:
            value = self.input_fn("Choose an action (or 'cancel'): ").strip()
            if _is_cancel(value):
                return None
            if value.isdigit() and 1 <= int(value) <= len(choices):
                return choices[int(value) - 1]
            self.output_fn("Enter one of the listed numbers, or 'cancel'.")

    def choose_scenario(self) -> ParticipantDropScenario | None:
        choices = tuple(ParticipantDropScenario)
        self.output_fn("Participant-drop scenario:")
        for index, scenario in enumerate(choices, start=1):
            self.output_fn(f"  {index}. {scenario.value}")
        while True:
            value = self.input_fn("Choose a scenario (or 'cancel'): ").strip()
            if _is_cancel(value):
                return None
            if value.isdigit() and 1 <= int(value) <= len(choices):
                return choices[int(value) - 1]
            self.output_fn("Enter one of the listed numbers, or 'cancel'.")

    def request_reference(self, scenario: ParticipantDropScenario) -> str | None:
        labels = {
            ParticipantDropScenario.UNPAID_INVOICE: "Invoice number",
            ParticipantDropScenario.WITHDRAWAL_REQUEST: "Withdrawal Request name",
            ParticipantDropScenario.CRG_DROP: "CRG audit name",
            ParticipantDropScenario.OTHER: "Related reference",
        }
        value = self.input_fn(
            f"{labels[scenario]} (optional; press Enter to skip, or 'cancel'): "
        ).strip()
        return None if _is_cancel(value) else value

    def request_certification_id(self) -> str | None:
        value = self.input_fn(
            "Certification ID (optional; press Enter to skip, or 'cancel'): "
        ).strip()
        return None if _is_cancel(value) else value

    def request_company_name(self) -> str | None:
        value = self.input_fn("Company name (or 'cancel'): ").strip()
        return None if _is_cancel(value) else value

    def select_account(
        self, candidates: tuple[AccountCandidate, ...]
    ) -> AccountCandidate | None:
        self.output_fn("Multiple Accounts matched:")
        for index, candidate in enumerate(candidates, start=1):
            certification_id = candidate.certification_id or "no Certification ID"
            self.output_fn(f"  {index}. {candidate.name} ({certification_id})")
        while True:
            value = self.input_fn("Choose an Account (or 'cancel'): ").strip()
            if _is_cancel(value):
                return None
            if value.isdigit() and 1 <= int(value) <= len(candidates):
                return candidates[int(value) - 1]
            self.output_fn("Enter one of the listed numbers, or 'cancel'.")

    def show(self, message: str) -> None:
        self.output_fn(message)


def _is_cancel(value: str) -> bool:
    return value.casefold() in {"cancel", "c", "q", "quit"}
