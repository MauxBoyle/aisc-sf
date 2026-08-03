"""Typed, renderer-neutral interactions for Profile Update review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .review_queue import ReviewQueueManifest


class UnsupportedReviewInteractionError(RuntimeError):
    """A review UI received an interaction shape it does not support."""


@dataclass(frozen=True)
class TextFragment:
    """Literal explanatory text supplied by the application."""

    text: str


@dataclass(frozen=True)
class ValueFragment:
    """An interpolated domain value that a UI may style differently."""

    value: str


type StyledFragment = TextFragment | ValueFragment
type StyledText = tuple[StyledFragment, ...]


@dataclass(frozen=True)
class ReviewChoice:
    """One action that is structurally available for a question."""

    key: str
    label: str
    shortcuts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Heading:
    """A visually separated review section heading."""

    title: StyledText
    separator: str


@dataclass(frozen=True)
class Notice:
    """Informational reviewer-facing content."""

    message: StyledText


@dataclass(frozen=True)
class ValidationFeedback:
    """Feedback emitted when domain validation asks the reviewer to retry."""

    message: StyledText


@dataclass(frozen=True)
class ContextLine:
    """A labeled piece of Case or submission context."""

    label: str
    value: StyledText


@dataclass(frozen=True)
class ScalarComparison:
    """One current Salesforce value compared with one proposed value."""

    label: str
    current: ValueFragment
    proposed: ValueFragment


@dataclass(frozen=True)
class MappingComparisonRow:
    """One row within a dictionary-backed proposal."""

    label: str
    current: ValueFragment
    proposed: ValueFragment


@dataclass(frozen=True)
class MappingComparison:
    """A group of proposed fields for an existing or new record."""

    label: str
    rows: tuple[MappingComparisonRow, ...]
    is_new: bool = False


@dataclass(frozen=True)
class ContactCard:
    """Human-readable details for one Salesforce Contact candidate."""

    heading: str
    name: ValueFragment
    title: ValueFragment
    email: ValueFragment
    phone: ValueFragment


@dataclass(frozen=True)
class ContactComparisonRow:
    """One row in the aggregate reconciled-Contact comparison."""

    label: str
    current: ValueFragment
    reconciled: ValueFragment
    sources: ValueFragment


@dataclass(frozen=True)
class ContactComparison:
    """All submitted values reconciled for one Contact."""

    identity: ValueFragment
    rows: tuple[ContactComparisonRow, ...]


@dataclass(frozen=True)
class ConflictCandidate:
    """One submitted value and the submissions that supplied it."""

    key: str
    value: ValueFragment
    sources: ValueFragment


@dataclass(frozen=True)
class ContactFieldConflict:
    """Conflicting proposed values that require an explicit selection."""

    label: str
    current: ValueFragment
    candidates: tuple[ConflictCandidate, ...]


@dataclass(frozen=True)
class StagedRowSummary:
    """The safe-stop checkpoint shown before Salesforce writes."""

    account: ValueFragment
    submitter_name: ValueFragment
    submitter_email: ValueFragment
    profile_updates: ValueFragment
    contact_details_supplemented: bool = False
    has_no_update_content: bool = False


@dataclass(frozen=True)
class AccountHistory:
    """One Account field change near the submission date."""

    field: ValueFragment
    old_value: ValueFragment
    new_value: ValueFragment
    created_at: ValueFragment


@dataclass(frozen=True)
class ResponseEmail:
    """A generated response email presented for sending confirmation."""

    recipient: ValueFragment
    body: str


@dataclass(frozen=True)
class ReviewQueueSnapshot:
    """Complete navigation state for a CLI, TUI, or other review front end."""

    manifest: ReviewQueueManifest


type ReviewEvent = (
    Heading
    | Notice
    | ValidationFeedback
    | ContextLine
    | ScalarComparison
    | MappingComparison
    | ContactCard
    | ContactComparison
    | ContactFieldConflict
    | StagedRowSummary
    | AccountHistory
    | ResponseEmail
    | ReviewQueueSnapshot
)


@dataclass(frozen=True)
class ChoiceQuestion:
    """A question whose answer must be one of the included choices."""

    prompt: StyledText
    choices: tuple[ReviewChoice, ...]
    invalid_feedback: StyledText
    default_key: str | None = None


@dataclass(frozen=True)
class FreeTextQuestion:
    """A question that collects text for processor-owned validation."""

    prompt: StyledText


@dataclass(frozen=True)
class AcknowledgementQuestion:
    """A pause that continues when the reviewer acknowledges it."""

    prompt: StyledText


type ReviewQuestion = ChoiceQuestion | FreeTextQuestion | AcknowledgementQuestion


@dataclass(frozen=True)
class ChoiceAnswer:
    """The selected available choice."""

    choice: ReviewChoice


@dataclass(frozen=True)
class FreeTextAnswer:
    """Unvalidated text entered by the reviewer."""

    text: str


@dataclass(frozen=True)
class AcknowledgementAnswer:
    """Confirmation that the reviewer completed an external step."""


type ReviewAnswer = ChoiceAnswer | FreeTextAnswer | AcknowledgementAnswer


class ReviewUI(Protocol):
    """Boundary implemented by terminal, TUI, or other review front ends."""

    def display(self, event: ReviewEvent) -> None:
        """Render one typed review event."""
        ...

    def ask(self, question: ReviewQuestion) -> ReviewAnswer:
        """Collect one typed answer."""
        ...


def styled(*fragments: str | ValueFragment) -> StyledText:
    """Build styled text while keeping interpolated values identifiable."""
    return tuple(
        fragment if isinstance(fragment, ValueFragment) else TextFragment(fragment)
        for fragment in fragments
    )
