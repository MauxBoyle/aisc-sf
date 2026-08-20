"""Terminal adapter for the renderer-neutral Profile Update review interface."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from enum import StrEnum
from io import StringIO

from rich.console import Console
from rich.text import Text
from rich.theme import Theme

from .review_queue import QueueStatus, iter_changes
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
    ParentAccountConflict,
    ParentAccountNoActiveChildren,
    ResponseEmail,
    ReviewAnswer,
    ReviewEvent,
    ReviewQuestion,
    ReviewQueueSnapshot,
    ScalarComparison,
    StagedRowSummary,
    StyledText,
    UnsupportedReviewInteractionError,
    ValidationFeedback,
    ValueFragment,
    ValueOrigin,
    WarningNotice,
)


class ColorMode(StrEnum):
    """How the terminal adapter decides whether to emit ANSI color codes."""

    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


PROFILE_UPDATE_THEME = Theme(
    {
        "profile.submitted": "bright_green",
        "profile.supplemented": "bright_yellow",
        "profile.warning": "bright_red",
        "profile.response": "bright_blue",
    }
)

_ORIGIN_STYLES = {
    ValueOrigin.NEUTRAL: None,
    ValueOrigin.SUBMITTED: "profile.submitted",
    ValueOrigin.SUPPLEMENTED: "profile.supplemented",
}


class CLIReviewUI:
    """Render typed review interactions with accessible terminal colors."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        color_mode: ColorMode | str | bool | None = None,
    ):
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.color_mode = _normalize_color_mode(color_mode)
        self._colors_enabled = _detect_color_support(
            self.color_mode,
            custom_output=output_fn is not print,
            stream=sys.stdout,
        )

    def display(self, event: ReviewEvent) -> None:
        """Render every supported event and reject unknown objects explicitly."""
        if isinstance(event, Heading):
            self._emit(_section_heading(_styled_text(event.title), event.separator))
        elif isinstance(event, Notice):
            self._emit(_styled_text(event.message))
        elif isinstance(event, (WarningNotice, ValidationFeedback)):
            self._emit(Text(_render_text(event.message), style="profile.warning"))
        elif isinstance(event, ContextLine):
            line = Text(f"{event.label}: ")
            line.append_text(_styled_text(event.value))
            self._emit(line)
        elif isinstance(event, ScalarComparison):
            line = Text(f"\n{event.label}\nCurrent Salesforce value: ")
            line.append_text(_value(event.current))
            line.append("\nProposed value: ")
            line.append_text(_value(event.proposed))
            self._emit(line)
        elif isinstance(event, MappingComparison):
            heading = (
                "New Salesforce values:" if event.is_new else "Salesforce changes:"
            )
            lines = Text(f"\n{event.label}\n{heading}")
            for row in event.rows:
                lines.append(f"\n{row.label}: ")
                if event.is_new:
                    lines.append_text(_value(row.proposed))
                else:
                    lines.append_text(_value(row.current))
                    lines.append(" -> ")
                    lines.append_text(_value(row.proposed))
            self._emit(lines)
        elif isinstance(event, ContactCard):
            card = Text(f"{event.heading}:\nName: ")
            card.append_text(_value(event.name))
            card.append("\nTitle: ")
            card.append_text(_value(event.title))
            card.append("\nEmail: ")
            card.append_text(_value(event.email))
            card.append("\nPhone: ")
            card.append_text(_value(event.phone))
            self._emit(card)
        elif isinstance(event, ContactComparison):
            title = Text("Reconciled Contact: ")
            title.append_text(_raw_value(event.identity))
            self._emit(_section_heading(title, "-" * 72))
            self._emit(Text("Field | Current Salesforce | Reconciled | Sources"))
            for row in event.rows:
                line = Text(f"{row.label} | ")
                line.append_text(_value(row.current))
                line.append(" | ")
                line.append_text(_value(row.reconciled))
                line.append(" | ")
                line.append_text(_value(row.sources))
                self._emit(line)
        elif isinstance(event, ContactFieldConflict):
            self._emit(
                _section_heading(
                    Text(f"Contact field conflict: {event.label}"), "-" * 72
                )
            )
            for candidate in event.candidates:
                line = Text(f"{candidate.key}. ")
                line.append_text(_raw_value(candidate.value))
                line.append("\n   Sources: ")
                line.append_text(_raw_value(candidate.sources))
                self._emit(line)
            current = Text("current. ")
            current.append_text(_value(event.current))
            self._emit(current)
        elif isinstance(event, ParentAccountConflict):
            title = Text(
                "Parent Account needs manual follow-up: ", style="profile.warning"
            )
            title.append_text(_raw_value(event.parent))
            lines = _section_heading(title, "-" * 72)
            lines.append(
                "\nActive direct child values conflict, so this entire Case batch "
                "will remain open:",
                style="profile.warning",
            )
            for field in event.fields:
                lines.append(f"\n\n{field.label}\nRequested value: ")
                lines.append_text(_value(field.requested))
                for child in field.children:
                    lines.append("\n")
                    lines.append(child.account_name.value or "(unnamed)")
                    lines.append(f" ({child.account_id.value}): ")
                    lines.append_text(_value(child.current))
            self._emit(lines)
        elif isinstance(event, ParentAccountNoActiveChildren):
            title = Text(
                "Parent Account needs manual follow-up: ", style="profile.warning"
            )
            title.append_text(_raw_value(event.parent))
            lines = _section_heading(title, "-" * 72)
            lines.append(
                "\nThis Parent Account has no direct child with status Certified or "
                "Initials, so this entire Case batch will remain open.",
                style="profile.warning",
            )
            if event.children:
                lines.append("\nDirect children:")
                for child in event.children:
                    lines.append("\n")
                    lines.append(child.account_name.value or "(unnamed)")
                    lines.append(f" ({child.account_id.value}): ")
                    lines.append_text(_value(child.current))
            self._emit(lines)
        elif isinstance(event, StagedRowSummary):
            heading = Text("Staged row\nAccount: ")
            heading.append_text(_value(event.account))
            heading.append("\nSubmitter: ")
            heading.append_text(_raw_value(event.submitter_name))
            heading.append(" <")
            heading.append_text(_raw_value(event.submitter_email))
            heading.append(">\nProfile Updates: ")
            heading.append_text(_value(event.profile_updates))
            if event.contact_details_supplemented:
                heading.append_text(
                    _raw_value(
                        ValueFragment(
                            "\nNote: contact details were supplemented from available "
                            "contact information.",
                            ValueOrigin.SUPPLEMENTED,
                        )
                    )
                )
            if event.has_no_update_content:
                heading.append_text(
                    _raw_value(
                        ValueFragment(
                            "\nNote: this combined profile update has no submitted "
                            "update content.",
                            ValueOrigin.SUPPLEMENTED,
                        )
                    )
                )
            self._emit(_section_heading(heading, "-" * 72))
        elif isinstance(event, AccountHistory):
            line = Text("Account History: ")
            line.append_text(_raw_value(event.field))
            line.append(" changed from ")
            line.append_text(_value(event.old_value))
            line.append(" to ")
            line.append_text(_value(event.new_value))
            line.append(" at ")
            line.append_text(_raw_value(event.created_at))
            self._emit(line)
        elif isinstance(event, ResponseEmail):
            title = Text("Response email for ")
            title.append_text(_raw_value(event.recipient))
            response = _section_heading(title, "-" * 72)
            response.append("\n")
            response.append(event.body, style="profile.response")
            self._emit(response)
        elif isinstance(event, ReviewQueueSnapshot):
            changes = list(iter_changes(event.manifest))
            pending = sum(
                change.status in {QueueStatus.PENDING, QueueStatus.IN_PROGRESS}
                for change in changes
            )
            next_change = next(
                (
                    change
                    for change in changes
                    if change.id == event.manifest.default_next_item_id
                ),
                None,
            )
            next_label = next_change.label if next_change is not None else "none"
            self._emit(
                Text(
                    f"Review queue: {len(event.manifest.batches)} batch(es), "
                    f"{pending} pending change(s); next: {next_label}"
                )
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
                raw = self.input_fn(self._render(_styled_text(question.prompt)))
                normalized = raw.strip().casefold()
                if not normalized and question.default_key is not None:
                    normalized = question.default_key.casefold()
                choice = aliases.get(normalized)
                if choice is not None:
                    return ChoiceAnswer(choice)
                self._emit(
                    Text(
                        _render_text(question.invalid_feedback),
                        style="profile.warning",
                    )
                )
        if isinstance(question, FreeTextQuestion):
            return FreeTextAnswer(
                self.input_fn(self._render(_styled_text(question.prompt)))
            )
        if isinstance(question, AcknowledgementQuestion):
            self.input_fn(self._render(_styled_text(question.prompt)))
            return AcknowledgementAnswer()
        raise UnsupportedReviewInteractionError(
            f"CLIReviewUI cannot ask {type(question).__name__}."
        )

    def _emit(self, value: Text) -> None:
        self.output_fn(self._render(value))

    def _render(self, value: Text) -> str:
        buffer = StringIO()
        console = Console(
            file=buffer,
            color_system="standard" if self._colors_enabled else None,
            force_terminal=self._colors_enabled,
            legacy_windows=False,
            no_color=not self._colors_enabled,
            theme=PROFILE_UPDATE_THEME,
        )
        console.print(value, end="", soft_wrap=True)
        return buffer.getvalue()


def _normalize_color_mode(value: ColorMode | str | bool | None) -> ColorMode:
    if value is None:
        return ColorMode.AUTO
    if value is True:
        return ColorMode.ALWAYS
    if value is False:
        return ColorMode.NEVER
    return ColorMode(value)


def _detect_color_support(
    mode: ColorMode,
    *,
    custom_output: bool,
    stream: object,
) -> bool:
    if mode is ColorMode.ALWAYS:
        return True
    if mode is ColorMode.NEVER or custom_output or "NO_COLOR" in os.environ:
        return False
    detector = Console(file=stream, theme=PROFILE_UPDATE_THEME)
    return detector.is_terminal and detector.color_system is not None


def print_profile_error(
    message: str,
    *,
    color_mode: ColorMode | str | bool | None = None,
) -> None:
    """Print one Profile Update command failure to stderr in warning red."""
    mode = _normalize_color_mode(color_mode)
    enabled = _detect_color_support(
        mode,
        custom_output=False,
        stream=sys.stderr,
    )
    console = Console(
        file=sys.stderr,
        color_system="standard" if enabled else None,
        force_terminal=enabled,
        legacy_windows=False,
        no_color=not enabled,
        theme=PROFILE_UPDATE_THEME,
    )
    console.print(Text(message, style="profile.warning"), soft_wrap=True)


def _styled_text(value: StyledText) -> Text:
    rendered = Text()
    for fragment in value:
        if isinstance(fragment, ValueFragment):
            rendered.append(fragment.value, style=_ORIGIN_STYLES[fragment.origin])
        else:
            rendered.append(fragment.text)
    return rendered


def _render_text(value: StyledText) -> str:
    return "".join(
        fragment.value if isinstance(fragment, ValueFragment) else fragment.text
        for fragment in value
    )


def _value(fragment: ValueFragment) -> Text:
    return Text(fragment.value or "(blank)", style=_ORIGIN_STYLES[fragment.origin])


def _raw_value(fragment: ValueFragment) -> Text:
    return Text(fragment.value, style=_ORIGIN_STYLES[fragment.origin])


def _section_heading(title: Text, separator: str) -> Text:
    result = Text(f"\n{separator}\n")
    result.append_text(title)
    result.append(f"\n{separator}")
    return result
