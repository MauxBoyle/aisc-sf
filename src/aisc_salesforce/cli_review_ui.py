"""Terminal adapter for the renderer-neutral Profile Update review interface."""

from __future__ import annotations

from collections.abc import Callable

from .review_ui import (
    AccountHistory,
    AcknowledgementAnswer,
    AcknowledgementQuestion,
    ChoiceAnswer,
    ChoiceQuestion,
    ContactCard,
    ContactComparison,
    ContactFieldConflict,
    ContextLine,
    FreeTextAnswer,
    FreeTextQuestion,
    Heading,
    MappingComparison,
    Notice,
    ResponseEmail,
    ReviewAnswer,
    ReviewEvent,
    ReviewQuestion,
    ScalarComparison,
    StagedRowSummary,
    StyledText,
    UnsupportedReviewInteractionError,
    ValidationFeedback,
    ValueFragment,
)


class CLIReviewUI:
    """Render typed review interactions with the existing terminal experience."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ):
        self.input_fn = input_fn
        self.output_fn = output_fn

    def display(self, event: ReviewEvent) -> None:
        """Render every supported event and reject unknown objects explicitly."""
        if isinstance(event, Heading):
            self.output_fn(_section_heading(_render_text(event.title), event.separator))
        elif isinstance(event, (Notice, ValidationFeedback)):
            self.output_fn(_render_text(event.message))
        elif isinstance(event, ContextLine):
            self.output_fn(f"{event.label}: {_render_text(event.value)}")
        elif isinstance(event, ScalarComparison):
            self.output_fn(
                f"\n{event.label}\n"
                f"Current Salesforce value: {_value(event.current)}\n"
                f"Proposed value: {_value(event.proposed)}"
            )
        elif isinstance(event, MappingComparison):
            heading = "New Salesforce values:" if event.is_new else "Salesforce changes:"
            lines = [f"\n{event.label}", heading]
            for row in event.rows:
                if event.is_new:
                    lines.append(f"{row.label}: {_value(row.proposed)}")
                else:
                    lines.append(
                        f"{row.label}: {_value(row.current)} -> {_value(row.proposed)}"
                    )
            self.output_fn("\n".join(lines))
        elif isinstance(event, ContactCard):
            self.output_fn(
                f"{event.heading}:\n"
                f"Name: {_value(event.name)}\n"
                f"Title: {_value(event.title)}\n"
                f"Email: {_value(event.email)}\n"
                f"Phone: {_value(event.phone)}"
            )
        elif isinstance(event, ContactComparison):
            self.output_fn(
                _section_heading(
                    f"Reconciled Contact: {event.identity.value}", "-" * 72
                )
            )
            self.output_fn("Field | Current Salesforce | Reconciled | Sources")
            for row in event.rows:
                self.output_fn(
                    f"{row.label} | {_value(row.current)} | "
                    f"{_value(row.reconciled)} | {_value(row.sources)}"
                )
        elif isinstance(event, ContactFieldConflict):
            self.output_fn(
                _section_heading(f"Contact field conflict: {event.label}", "-" * 72)
            )
            for candidate in event.candidates:
                self.output_fn(
                    f"{candidate.key}. {candidate.value.value}\n"
                    f"   Sources: {candidate.sources.value}"
                )
            self.output_fn(f"current. {_value(event.current)}")
        elif isinstance(event, StagedRowSummary):
            heading = (
                "Staged row\n"
                f"Account: {_value(event.account)}\n"
                f"Submitter: {event.submitter_name.value} "
                f"<{event.submitter_email.value}>\n"
                f"Profile Updates: {_value(event.profile_updates)}"
            )
            if event.contact_details_supplemented:
                heading += (
                    "\nNote: contact details were supplemented from available "
                    "contact information."
                )
            if event.has_no_update_content:
                heading += (
                    "\nNote: this combined profile update has no submitted update content."
                )
            self.output_fn(_section_heading(heading, "-" * 72))
        elif isinstance(event, AccountHistory):
            self.output_fn(
                "Account History: "
                f"{event.field.value} changed from {_value(event.old_value)} to "
                f"{_value(event.new_value)} at {event.created_at.value}"
            )
        elif isinstance(event, ResponseEmail):
            self.output_fn(
                f"{_section_heading(f'Response email for {event.recipient.value}', '-' * 72)}"
                f"\n{event.body}"
            )
        else:
            raise UnsupportedReviewInteractionError(
                f"CLIReviewUI cannot display {type(event).__name__}."
            )

    def ask(self, question: ReviewQuestion) -> ReviewAnswer:
        """Parse terminal input for each supported question type."""
        if isinstance(question, ChoiceQuestion):
            aliases = {
                alias.casefold(): choice
                for choice in question.choices
                for alias in (choice.key, choice.label, *choice.shortcuts)
            }
            while True:
                raw = self.input_fn(_render_text(question.prompt))
                normalized = raw.strip().casefold()
                if not normalized and question.default_key is not None:
                    normalized = question.default_key.casefold()
                choice = aliases.get(normalized)
                if choice is not None:
                    return ChoiceAnswer(choice)
                self.output_fn(_render_text(question.invalid_feedback))
        if isinstance(question, FreeTextQuestion):
            return FreeTextAnswer(self.input_fn(_render_text(question.prompt)))
        if isinstance(question, AcknowledgementQuestion):
            self.input_fn(_render_text(question.prompt))
            return AcknowledgementAnswer()
        raise UnsupportedReviewInteractionError(
            f"CLIReviewUI cannot ask {type(question).__name__}."
        )


def _render_text(value: StyledText) -> str:
    return "".join(
        fragment.value if isinstance(fragment, ValueFragment) else fragment.text
        for fragment in value
    )


def _value(fragment: ValueFragment) -> str:
    return fragment.value or "(blank)"


def _section_heading(title: str, separator: str) -> str:
    return f"\n{separator}\n{title}\n{separator}"
