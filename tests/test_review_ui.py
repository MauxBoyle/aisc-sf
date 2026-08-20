import re
from dataclasses import FrozenInstanceError
from io import StringIO

import pytest

from aisc_salesforce import app
from aisc_salesforce.cli_review_ui import CLIReviewUI
from aisc_salesforce.process_profile_updates import (
    InteractiveProfileUpdateProcessor,
    ReviewDecision,
)
from aisc_salesforce.review_queue import build_review_queue
from aisc_salesforce.review_ui import (
    AcknowledgementAnswer,
    ChoiceAnswer,
    ChoiceQuestion,
    Heading,
    MappingComparison,
    MappingComparisonRow,
    ParentAccountChildValue,
    ParentAccountConflict,
    ParentAccountFieldConflict,
    ParentAccountNoActiveChildren,
    ResponseEmail,
    ReviewChoice,
    ReviewQueueSnapshot,
    ScalarComparison,
    StagedRowSummary,
    UnsupportedReviewInteractionError,
    ValidationFeedback,
    ValueFragment,
    ValueOrigin,
    WarningNotice,
    styled,
)


class RecordingUI:
    def __init__(self, answer):
        self.answer = answer
        self.events = []
        self.questions = []

    def display(self, event):
        self.events.append(event)

    def ask(self, question):
        self.questions.append(question)
        return self.answer


def test_review_dataclasses_are_frozen_and_keep_values_structured():
    event = ScalarComparison(
        "Company Name",
        ValueFragment("Acme"),
        ValueFragment("Acme Steel"),
    )

    assert event.proposed.value == "Acme Steel"
    with pytest.raises(FrozenInstanceError):
        event.label = "Other"  # type: ignore[misc]


def test_value_fragment_origin_is_backward_compatible_and_renderer_neutral():
    assert ValueFragment("old caller").origin is ValueOrigin.NEUTRAL
    assert (
        ValueFragment("submitted", ValueOrigin.SUBMITTED).origin
        is ValueOrigin.SUBMITTED
    )


def test_cli_forced_color_uses_the_semantic_palette_and_literal_text():
    output = []
    ui = CLIReviewUI(output_fn=output.append, color_mode="always")

    ui.display(
        ScalarComparison(
            "Company [Name]",
            ValueFragment("Current [Salesforce]"),
            ValueFragment("Submitted [value]", ValueOrigin.SUBMITTED),
        )
    )
    ui.display(
        StagedRowSummary(
            ValueFragment("Acme"),
            ValueFragment("Alex", ValueOrigin.SUBMITTED),
            ValueFragment("alex@example.com", ValueOrigin.SUBMITTED),
            ValueFragment("PU-1", ValueOrigin.SUBMITTED),
            contact_details_supplemented=True,
        )
    )
    ui.display(WarningNotice(styled("Warning [literal]")))
    ui.display(ValidationFeedback(styled("Try [again]")))
    ui.display(ResponseEmail(ValueFragment("alex@example.com"), "Hello [Alex]"))

    rendered = "\n".join(output)
    assert "\x1b[92mSubmitted [value]\x1b[0m" in rendered
    assert "\x1b[93mNote: contact details were supplemented" in rendered
    assert "\x1b[91mWarning [literal]\x1b[0m" in rendered
    assert "\x1b[91mTry [again]\x1b[0m" in rendered
    assert "\x1b[94mHello [Alex]\x1b[0m" in rendered
    assert "Current [Salesforce]" in rendered


def test_cli_keeps_neutral_events_uncolored_even_when_color_is_forced():
    output = []
    ui = CLIReviewUI(output_fn=output.append, color_mode="always")

    ui.display(Heading(styled("Account Updates"), "=" * 4))
    ui.display(ScalarComparison("Name", ValueFragment("Old"), ValueFragment("New")))
    ui.display(ReviewQueueSnapshot(build_review_queue([])))

    assert all("\x1b[" not in value for value in output)


@pytest.mark.parametrize("color_mode", [None, "never", False])
def test_cli_custom_output_is_plain_unless_color_is_forced(color_mode):
    output = []
    ui = CLIReviewUI(output_fn=output.append, color_mode=color_mode)

    ui.display(
        ScalarComparison(
            "Name",
            ValueFragment("Old"),
            ValueFragment("New", ValueOrigin.SUBMITTED),
        )
    )

    assert "\x1b[" not in output[0]


def test_cli_no_color_environment_disables_ansi_on_a_supported_terminal(
    monkeypatch,
):
    class TerminalBuffer(StringIO):
        def isatty(self):
            return True

    stream = TerminalBuffer()
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setenv("NO_COLOR", "1")
    ui = CLIReviewUI()

    ui.display(
        ScalarComparison(
            "Name",
            ValueFragment("Old"),
            ValueFragment("New", ValueOrigin.SUBMITTED),
        )
    )

    assert "\x1b[" not in stream.getvalue()


def test_cli_auto_color_uses_ansi_on_a_supported_terminal(monkeypatch):
    class TerminalBuffer(StringIO):
        def isatty(self):
            return True

    stream = TerminalBuffer()
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    ui = CLIReviewUI()

    ui.display(
        ScalarComparison(
            "Name",
            ValueFragment("Old"),
            ValueFragment("New", ValueOrigin.SUBMITTED),
        )
    )

    assert "\x1b[92mNew\x1b[0m" in stream.getvalue()


@pytest.mark.parametrize("terminal", [False, True])
def test_cli_auto_color_falls_back_for_redirection_and_dumb_terminals(
    monkeypatch, terminal
):
    class TerminalBuffer(StringIO):
        def isatty(self):
            return terminal

    stream = TerminalBuffer()
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.delenv("NO_COLOR", raising=False)
    if terminal:
        monkeypatch.setenv("TERM", "dumb")
    ui = CLIReviewUI()

    ui.display(WarningNotice(styled("Needs attention")))

    assert "\x1b[" not in stream.getvalue()


def test_forced_color_and_plain_rendering_have_identical_readable_text():
    event = ScalarComparison(
        "Company Name",
        ValueFragment("Acme"),
        ValueFragment("Acme [Steel]", ValueOrigin.SUBMITTED),
    )
    colored = []
    plain = []
    CLIReviewUI(output_fn=colored.append, color_mode="always").display(event)
    CLIReviewUI(output_fn=plain.append, color_mode="never").display(event)

    assert re.sub(r"\x1b\[[0-9;]*m", "", colored[0]) == plain[0]


def test_cli_renders_scalar_and_mapping_comparisons_compatibly():
    output = []
    ui = CLIReviewUI(output_fn=output.append)

    ui.display(
        ScalarComparison(
            "Company Name",
            ValueFragment("Acme"),
            ValueFragment("Acme Steel"),
        )
    )
    ui.display(
        MappingComparison(
            "Contact: Alex Smith",
            (
                MappingComparisonRow(
                    "Email",
                    ValueFragment("old@example.com"),
                    ValueFragment("new@example.com"),
                ),
            ),
        )
    )

    assert output == [
        "\nCompany Name\nCurrent Salesforce value: Acme\nProposed value: Acme Steel",
        "\nContact: Alex Smith\nSalesforce changes:\n"
        "Email: old@example.com -> new@example.com",
    ]


def test_cli_renders_a_concise_summary_from_the_complete_queue_event():
    output = []
    ui = CLIReviewUI(output_fn=output.append)

    ui.display(ReviewQueueSnapshot(build_review_queue([])))

    assert output == ["Review queue: 0 batch(es), 0 pending change(s); next: none"]


def test_cli_renders_parent_conflict_and_no_active_child_events():
    output = []
    ui = CLIReviewUI(output_fn=output.append)
    children = (
        ParentAccountChildValue(
            ValueFragment("child-1"),
            ValueFragment("First Child"),
            ValueFragment("Old Name"),
        ),
        ParentAccountChildValue(
            ValueFragment("child-2"),
            ValueFragment("Second Child"),
            ValueFragment("Other Name"),
        ),
    )

    ui.display(
        ParentAccountConflict(
            ValueFragment("Parent Account (parent-1)"),
            (
                ParentAccountFieldConflict(
                    "Company Name", ValueFragment("Requested Name"), children
                ),
            ),
        )
    )
    ui.display(
        ParentAccountNoActiveChildren(
            ValueFragment("Parent Account (parent-1)"),
            (
                ParentAccountChildValue(
                    ValueFragment("child-dropped"),
                    ValueFragment("Dropped Child"),
                    ValueFragment("Dropped"),
                ),
            ),
        )
    )

    rendered = "\n".join(output)
    assert "Company Name" in rendered
    assert "Requested value: Requested Name" in rendered
    assert "First Child (child-1): Old Name" in rendered
    assert "Second Child (child-2): Other Name" in rendered
    assert "no direct child with status Certified or Initials" in rendered
    assert "Dropped Child (child-dropped): Dropped" in rendered


def test_cli_retries_invalid_choice_with_question_feedback():
    answers = iter(["wrong", "M"])
    output = []
    ui = CLIReviewUI(input_fn=lambda prompt: next(answers), output_fn=output.append)
    question = ChoiceQuestion(
        styled("Decision: "),
        (ReviewChoice("manual", "make manually", ("m",)),),
        styled("Choose make manually."),
    )

    assert ui.ask(question) == ChoiceAnswer(question.choices[0])
    assert output == ["Choose make manually."]


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, EOFError])
def test_cli_allows_terminal_interruptions_to_propagate(error_type):
    def interrupt(prompt):
        raise error_type

    ui = CLIReviewUI(input_fn=interrupt)

    with pytest.raises(error_type):
        ui.ask(
            ChoiceQuestion(
                styled("Continue? "),
                (ReviewChoice("yes", "yes"),),
                styled("Enter yes."),
            )
        )


def test_cli_rejects_unknown_events_and_questions():
    ui = CLIReviewUI()

    with pytest.raises(UnsupportedReviewInteractionError, match="cannot display"):
        ui.display(object())  # type: ignore[arg-type]
    with pytest.raises(UnsupportedReviewInteractionError, match="cannot ask"):
        ui.ask(object())  # type: ignore[arg-type]


def test_incomplete_contact_decision_omits_automatic_choice_structurally():
    manual = ReviewChoice(
        ReviewDecision.MAKE_MANUALLY.value,
        ReviewDecision.MAKE_MANUALLY.value,
    )
    ui = RecordingUI(ChoiceAnswer(manual))
    processor = InteractiveProfileUpdateProcessor(object(), ui)

    decision = processor._prompt_decision(automatic_allowed=False)

    assert decision is ReviewDecision.MAKE_MANUALLY
    assert isinstance(ui.questions[0], ChoiceQuestion)
    assert {choice.key for choice in ui.questions[0].choices} == {
        ReviewDecision.MAKE_MANUALLY.value,
        ReviewDecision.WILL_NOT_BE_MADE.value,
    }


def test_processor_rejects_mismatched_answer_type():
    ui = RecordingUI(AcknowledgementAnswer())
    processor = InteractiveProfileUpdateProcessor(object(), ui)

    with pytest.raises(
        UnsupportedReviewInteractionError, match="requires ChoiceAnswer"
    ):
        processor._prompt_yes_no(styled("Continue? "))


def test_cli_reports_unsupported_review_interaction_as_command_failure(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        app,
        "_run_process_profile_updates",
        lambda output_dir, **kwargs: (_ for _ in ()).throw(
            UnsupportedReviewInteractionError("unknown review event")
        ),
    )

    assert app.main(["process-profile-updates"]) == 1
    assert (
        "Process profile updates failed: unknown review event"
        in capsys.readouterr().err
    )
