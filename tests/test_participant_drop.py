import pytest

from aisc_salesforce.cli_participant_drop import CLIParticipantDropInteraction
from aisc_salesforce.participant_drop import (
    ALL_REASONS,
    ASSIGNED_REASONS,
    SELECTABLE_REASONS,
    AccountCandidate,
    ParticipantDropAction,
    ParticipantDropScenario,
    ParticipantDropService,
    WithdrawalReason,
)


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.queries = []
        self.messages = []

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        self.queries.append((object_name, fields, where))
        return next(self.responses)

    def post_feed_message(self, account_id, message):
        self.messages.append((account_id, message))


class Interaction:
    def __init__(
        self,
        scenario,
        reference="",
        certification_ids=(),
        company_names=(),
        choice=None,
        withdrawal_reasons=(),
    ):
        self.scenario = scenario
        self.reference = reference
        self.certification_ids = iter(certification_ids)
        self.company_names = iter(company_names)
        self.choice = choice
        self.withdrawal_reasons = iter(withdrawal_reasons)
        self.messages = []

    def choose_scenario(self):
        return self.scenario

    def request_reference(self, scenario):
        return self.reference

    def request_certification_id(self):
        return next(self.certification_ids, None)

    def request_company_name(self):
        return next(self.company_names, None)

    def select_account(self, candidates):
        return self.choice

    def choose_withdrawal_reason(self, default_reason):
        return next(self.withdrawal_reasons, default_reason)

    def show(self, message):
        self.messages.append(message)


def test_withdrawal_reason_collections_match_the_workflow_contract():
    assert SELECTABLE_REASONS == (
        WithdrawalReason.ECONOMY,
        WithdrawalReason.FACILITY_MAIN_OFFICE_CLOSURE,
        WithdrawalReason.ROI,
        WithdrawalReason.NEW_OWNERSHIP,
        WithdrawalReason.NEW_BUSINESS_MODEL,
    )
    assert ASSIGNED_REASONS == (WithdrawalReason.CRG, WithdrawalReason.NON_PAYMENT)
    assert ALL_REASONS == (*SELECTABLE_REASONS, *ASSIGNED_REASONS)


def test_cli_reason_menu_uses_the_unpaid_default_and_allows_replacement():
    answers = iter(["3"])
    output = []
    interaction = CLIParticipantDropInteraction(
        input_fn=lambda prompt: next(answers), output_fn=output.append
    )

    reason = interaction.choose_withdrawal_reason(WithdrawalReason.NON_PAYMENT)

    assert reason is WithdrawalReason.FACILITY_MAIN_OFFICE_CLOSURE
    assert output == [
        "Withdrawal reason:",
        "  1. #NonPayment (default)",
        "  2. Economy",
        "  3. Facility/Main office closure",
        "  4. ROI",
        "  5. New Ownership",
        "  6. New Business model",
    ]


def test_cli_reason_menu_requires_a_number_or_cancellation_without_a_default():
    answers = iter(["invalid", "cancel"])
    output = []
    interaction = CLIParticipantDropInteraction(
        input_fn=lambda prompt: next(answers), output_fn=output.append
    )

    assert interaction.choose_withdrawal_reason(None) is None
    assert output[-1] == "Enter one of the listed numbers, or 'cancel'."


def test_choose_action_displays_choices_in_enum_order():
    output = []
    interaction = CLIParticipantDropInteraction(
        input_fn=lambda prompt: "2", output_fn=output.append
    )

    assert interaction.choose_action() is ParticipantDropAction.COMPLETE
    assert output == [
        "Participant-drop action:",
        "  1. Start a new withdrawal",
        "  2. Complete an existing withdrawal",
    ]


def test_choose_action_explains_invalid_input_and_repeats_prompt():
    answers = iter(["not-a-choice", "1"])
    prompts = []
    output = []
    interaction = CLIParticipantDropInteraction(
        input_fn=lambda prompt: (prompts.append(prompt), next(answers))[1],
        output_fn=output.append,
    )

    assert interaction.choose_action() is ParticipantDropAction.START
    assert output[-1] == "Enter one of the listed numbers, or 'cancel'."
    assert prompts == [
        "Choose an action (or 'cancel'): ",
        "Choose an action (or 'cancel'): ",
    ]


@pytest.mark.parametrize("alias", ["cancel", "c", "q", "quit"])
def test_choose_action_accepts_all_cancellation_aliases(alias):
    interaction = CLIParticipantDropInteraction(input_fn=lambda prompt: alias)

    assert interaction.choose_action() is None


def test_unpaid_invoice_uses_cert_invoice_name_and_posts_exact_message():
    client = Client(
        [
            [{"Cert_Account__c": "001invoice"}],
            [
                {
                    "Id": "001invoice",
                    "Name": "Invoice Steel",
                    "Certification_ID__c": "C-1",
                }
            ],
        ]
    )
    interaction = Interaction(ParticipantDropScenario.UNPAID_INVOICE, "INV-42")

    result = ParticipantDropService(client).run(interaction)

    assert result.account == AccountCandidate("001invoice", "Invoice Steel", "C-1")
    assert result.withdrawal_reason is WithdrawalReason.NON_PAYMENT
    assert client.queries[0] == (
        "Cert_Invoice__c",
        ["Name", "Cert_Account__c"],
        "Name = 'INV-42'",
    )
    assert client.messages == [
        ("001invoice", "Withdrawal in progress: Unpaid Invoice.")
    ]


def test_withdrawal_request_uses_name_and_crg_drop_uses_audit_name():
    for scenario, object_name, field, account_id in (
        (
            ParticipantDropScenario.WITHDRAWAL_REQUEST,
            "Withdrawal_Request__c",
            "Account__c",
            "001withdrawal",
        ),
        (
            ParticipantDropScenario.CRG_DROP,
            "Cert_Audit__c",
            "Cert_Account__c",
            "001crg",
        ),
    ):
        client = Client(
            [
                [{field: account_id}],
                [{"Id": account_id, "Name": "Steel", "Certification_ID__c": None}],
            ]
        )
        withdrawal_reasons = (
            [WithdrawalReason.ECONOMY]
            if scenario is ParticipantDropScenario.WITHDRAWAL_REQUEST
            else []
        )
        result = ParticipantDropService(client).run(
            Interaction(scenario, "REF-1", withdrawal_reasons=withdrawal_reasons)
        )

        assert result.account.id == account_id
        assert client.queries[0] == (object_name, ["Name", field], "Name = 'REF-1'")


def test_unpaid_invoice_default_can_be_replaced_with_a_selectable_reason():
    client = Client(
        [
            [{"Cert_Account__c": "001invoice"}],
            [
                {
                    "Id": "001invoice",
                    "Name": "Invoice Steel",
                    "Certification_ID__c": "C-1",
                }
            ],
        ]
    )
    interaction = Interaction(
        ParticipantDropScenario.UNPAID_INVOICE,
        "INV-42",
        withdrawal_reasons=[WithdrawalReason.ROI],
    )

    result = ParticipantDropService(client).run(interaction)

    assert result.withdrawal_reason is WithdrawalReason.ROI
    assert client.messages == [
        ("001invoice", "Withdrawal in progress: Unpaid Invoice.")
    ]


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        (ParticipantDropScenario.WITHDRAWAL_REQUEST, WithdrawalReason.ECONOMY),
        (ParticipantDropScenario.OTHER, WithdrawalReason.NEW_BUSINESS_MODEL),
    ],
)
def test_selectable_reason_is_required_for_manual_drop_scenarios(scenario, reason):
    client = Client([[{"Id": "001a", "Name": "Steel", "Certification_ID__c": "C-1"}]])
    interaction = Interaction(
        scenario, certification_ids=["C-1"], withdrawal_reasons=[reason]
    )

    result = ParticipantDropService(client).run(interaction)

    assert result.withdrawal_reason is reason


def test_crg_drop_assigns_its_reason_without_prompting_for_a_reason():
    client = Client(
        [
            [{"Cert_Account__c": "001crg"}],
            [{"Id": "001crg", "Name": "Steel", "Certification_ID__c": None}],
        ]
    )

    result = ParticipantDropService(client).run(
        Interaction(ParticipantDropScenario.CRG_DROP, "AUD-1")
    )

    assert result.withdrawal_reason is WithdrawalReason.CRG


def test_cancel_at_reason_never_posts_to_salesforce():
    client = Client(
        [
            [{"Cert_Account__c": "001invoice"}],
            [
                {
                    "Id": "001invoice",
                    "Name": "Invoice Steel",
                    "Certification_ID__c": "C-1",
                }
            ],
        ]
    )
    interaction = Interaction(
        ParticipantDropScenario.UNPAID_INVOICE, "INV-42", withdrawal_reasons=[None]
    )

    result = ParticipantDropService(client).run(interaction)

    assert result.cancelled is True
    assert client.messages == []


def test_falls_back_to_normalized_certification_id_then_company_name():
    client = Client(
        [
            [
                {
                    "Id": "001b",
                    "Name": "AISC Steel, Inc.",
                    "Certification_ID__c": "abc 123",
                },
                {"Id": "001c", "Name": "Unrelated", "Certification_ID__c": "other"},
            ],
        ]
    )
    interaction = Interaction(
        ParticipantDropScenario.OTHER,
        certification_ids=["ABC-123"],
        withdrawal_reasons=[WithdrawalReason.ECONOMY],
    )

    result = ParticipantDropService(client).run(interaction)

    assert result.account.id == "001b"
    assert client.messages == [
        ("001b", "Withdrawal in progress: Other participant drop.")
    ]


def test_company_no_match_retries_and_multiple_candidates_require_selection():
    first = AccountCandidate("001a", "Acme Steel", "A-1")
    selected = AccountCandidate("001b", "Acme Steel West", "A-2")
    client = Client(
        [
            [],
            [],
            [
                {
                    "Id": first.id,
                    "Name": first.name,
                    "Certification_ID__c": first.certification_id,
                },
                {
                    "Id": selected.id,
                    "Name": selected.name,
                    "Certification_ID__c": selected.certification_id,
                },
            ],
        ]
    )
    interaction = Interaction(
        ParticipantDropScenario.OTHER,
        certification_ids=["missing"],
        company_names=["No company", "Acme"],
        choice=selected,
        withdrawal_reasons=[WithdrawalReason.ECONOMY],
    )

    result = ParticipantDropService(client).run(interaction)

    assert result.account == selected
    assert (
        "No Accounts matched that company name; try again or cancel."
        in interaction.messages
    )
    assert client.messages == [
        ("001b", "Withdrawal in progress: Other participant drop.")
    ]


def test_cancel_at_scenario_never_posts_to_salesforce():
    client = Client([])
    interaction = Interaction(None)

    result = ParticipantDropService(client).run(interaction)

    assert result.cancelled is True
    assert client.messages == []


def test_cancel_at_reference_never_posts_to_salesforce():
    client = Client([])
    interaction = Interaction(ParticipantDropScenario.OTHER, reference=None)

    result = ParticipantDropService(client).run(interaction)

    assert result.cancelled is True
    assert client.messages == []


def test_cancel_at_certification_id_never_posts_to_salesforce():
    client = Client([])
    interaction = Interaction(ParticipantDropScenario.OTHER, certification_ids=[None])

    result = ParticipantDropService(client).run(interaction)

    assert result.cancelled is True
    assert client.messages == []


def test_cancel_at_company_name_never_posts_to_salesforce():
    client = Client([[]])
    interaction = Interaction(
        ParticipantDropScenario.OTHER,
        certification_ids=["missing"],
        company_names=[None],
    )

    result = ParticipantDropService(client).run(interaction)

    assert result.cancelled is True
    assert client.messages == []


def test_cancel_at_multiple_account_selection_never_posts_to_salesforce():
    client = Client(
        [
            [],
            [
                {"Id": "001a", "Name": "Acme East", "Certification_ID__c": "A-1"},
                {"Id": "001b", "Name": "Acme West", "Certification_ID__c": "A-2"},
            ],
        ]
    )
    interaction = Interaction(
        ParticipantDropScenario.OTHER,
        certification_ids=["missing"],
        company_names=["Acme"],
        choice=None,
    )

    result = ParticipantDropService(client).run(interaction)

    assert result.cancelled is True
    assert client.messages == []
