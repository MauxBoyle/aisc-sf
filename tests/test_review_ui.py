from dataclasses import FrozenInstanceError

import pytest

from aisc_salesforce import app
from aisc_salesforce.cli_review_ui import CLIReviewUI
from aisc_salesforce.process_profile_updates import (
    InteractiveProfileUpdateProcessor,
    ReviewDecision,
)
from aisc_salesforce.review_ui import (
    AcknowledgementAnswer,
    ChoiceAnswer,
    ChoiceQuestion,
    MappingComparison,
    MappingComparisonRow,
    ReviewChoice,
    ScalarComparison,
    UnsupportedReviewInteractionError,
    ValueFragment,
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
        "\nCompany Name\nCurrent Salesforce value: Acme\n"
        "Proposed value: Acme Steel",
        "\nContact: Alex Smith\nSalesforce changes:\n"
        "Email: old@example.com -> new@example.com",
    ]


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

    with pytest.raises(UnsupportedReviewInteractionError, match="requires ChoiceAnswer"):
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
