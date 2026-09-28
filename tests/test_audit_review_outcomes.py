import csv
from datetime import UTC, datetime

from aisc_salesforce.audit_review_outcomes import (
    AuditReviewOutcomeService,
    build_rows,
    classify_outcome,
    outcome_flags,
    two_year_cutoff,
    write_audit_review_outcomes,
)


def test_classifier_matches_drop_and_additional_terms_case_insensitively():
    assert classify_outcome("CRG Withdrawal", None) == "drop"
    assert classify_outcome(None, "Participant was DROPPED") == "drop"
    assert classify_outcome("ADDITIONAL audit", None) == "additional"
    assert classify_outcome(None, "An add'l audit is required") == "additional"
    assert classify_outcome(None, "An addt'l audit is required") == "additional"


def test_classifier_leaves_ordinary_favorable_outcomes_unflagged():
    for outcome in (
        "Renew certification",
        "Recommend renewal",
        "Grant certification",
    ):
        assert classify_outcome(outcome, None) == "unflagged"


def test_classifier_uses_crg_outcome_to_identify_standard_reviews():
    assert classify_outcome("Certification Recommended", None) == "standard"
    assert classify_outcome("Certificate Recommended", None) == "standard"
    assert classify_outcome("Certificate Processed", None) == "standard"
    assert classify_outcome("Certificate Processed", "Cautionary Letter") == (
        "cautionary_letter"
    )
    assert classify_outcome("Certificate Recommended", "Add BU certification") == (
        "scope"
    )


def test_classifier_requires_cautionary_and_excludes_conditional():
    assert classify_outcome("Cautionary Letter", None) == "cautionary_letter"
    assert classify_outcome("Cautionary Letter", "Conditional Certification") == (
        "needs_manual_review"
    )
    assert classify_outcome("Letter sent", None) == "needs_manual_review"


def test_classifier_matches_scope_only_with_a_certification_code_and_scope_language():
    assert classify_outcome("Add BU certification", None) == "scope"
    assert classify_outcome(None, "Remove SBR from scope") == "scope"
    assert classify_outcome("IBR upgrade", None) == "scope"
    assert classify_outcome("Grant BU only", None) == "scope"
    assert classify_outcome("Grant certification", None) == "unflagged"
    assert classify_outcome("Add certification", None) == "needs_manual_review"


def test_classifier_matches_all_certification_codes_and_explicit_scope_changes():
    for code in (
        "BU",
        "CSE",
        "ABR",
        "IBR",
        "SBR",
        "HYDA",
        "HYD",
        "CPT",
        "BEE",
        "SEE",
        "MEE",
        "CCC1",
        "CCE 2",
    ):
        assert classify_outcome(None, f"Add {code} certification") == "scope"
    assert classify_outcome(None, "Changing the scope of certification") == "scope"


def test_classifier_uses_crg_outcome_to_classify_withdrawal_and_follow_up():
    assert classify_outcome("CRG Withdrawal", "Remove BU certification") == "drop"
    assert classify_outcome("CRG Follow Up Needed", "Withdraw certification") == "drop"
    assert classify_outcome("CRG Follow Up Needed", "Withdraw BU certification") == (
        "scope"
    )


def test_classifier_handles_closed_without_certificate_rules():
    assert classify_outcome(
        "Closed Without Certificate", "Additional audit needed"
    ) == ("additional")
    assert classify_outcome(
        "Closed Without Certificate", "Conditional Certification"
    ) == ("standard")
    assert classify_outcome("Closed Without Certificate", "Part B job site audit") == (
        "standard"
    )
    assert classify_outcome("Closed Without Certificate", None) == "needs_manual_review"


def test_additional_audit_without_jobsite_overrides_a_certification_code():
    assert classify_outcome(
        "CRG Follow Up Needed", "BU full scope additional audit"
    ) == ("additional")
    assert classify_outcome("Certificate Recommended", "BU additional audit") == (
        "additional"
    )
    assert classify_outcome(None, "BU full scope additional audit") == "additional"


def test_classifier_marks_conflicting_outcomes_for_manual_review():
    assert classify_outcome("Drop; additional audit", None) == "needs_manual_review"
    assert classify_outcome("Cautionary Letter; grant BU only", None) == (
        "needs_manual_review"
    )


def test_conflicting_outcomes_have_no_outcome_flag_and_require_manual_review():
    assert outcome_flags("Drop; additional audit", None) == (
        False,
        False,
        False,
        False,
        False,
        True,
    )
    assert outcome_flags("Cautionary Letter; grant BU only", None) == (
        False,
        False,
        False,
        False,
        False,
        True,
    )


def test_classifier_prioritizes_drop_or_additional_over_cautionary_letter():
    assert classify_outcome("Drop with Cautionary Letter", None) == "drop"
    assert classify_outcome("Additional audit; Cautionary Letter", None) == "additional"


def test_classifier_marks_meaningful_unmatched_text_for_manual_review():
    assert classify_outcome("On hold", None) == "needs_manual_review"
    assert classify_outcome(None, "Awaiting decision") == "needs_manual_review"
    assert classify_outcome(None, None) == "unflagged"
    assert classify_outcome("  ", "\t") == "unflagged"


def test_outcome_flags_are_mutually_exclusive():
    assert outcome_flags("Certificate Recommended", None) == (
        True,
        False,
        False,
        False,
        False,
        False,
    )
    assert outcome_flags("CRG Withdrawal", None) == (
        False,
        True,
        False,
        False,
        False,
        False,
    )
    assert outcome_flags(None, "Participant was dropped") == (
        False,
        True,
        False,
        False,
        False,
        False,
    )
    assert outcome_flags("On hold", None) == (False, False, False, False, False, True)


def test_build_rows_flattens_account_and_uses_real_booleans():
    rows = build_rows(
        [
            {
                "Name": "AR-000123",
                "CreatedDate": "2026-09-01T14:30:00.000+0000",
                "Cert_Account__c": "001abc",
                "Cert_Account__r": {"Name": "Example Steel"},
                "Cert_CRG_Outcome__c": "On Hold",
                "CRG_Comments__c": "Additional audit needed",
            }
        ]
    )

    assert rows == [
        {
            "audit_review": "AR-000123",
            "audit_review_created_date": "2026-09-01T14:30:00.000+0000",
            "account_name": "Example Steel",
            "account_id": "001abc",
            "crg_outcome": "On Hold",
            "crg_comments": "Additional audit needed",
            "standard": False,
            "drop": False,
            "additional": True,
            "cautionary_letter": False,
            "scope": False,
            "needs_manual_review": False,
        }
    ]


def test_service_queries_a_rolling_two_year_window():
    class Client:
        def query_records(self, object_name, fields, *, where, order_by):
            self.call = (object_name, fields, where, order_by)
            return []

    client = Client()
    rows = AuditReviewOutcomeService(
        client, now=datetime(2026, 9, 22, 15, 30, tzinfo=UTC)
    ).build()

    assert rows == []
    assert client.call[0] == "Cert_Audit_Review__c"
    assert client.call[1][:2] == ["Name", "CreatedDate"]
    assert client.call[2] == "CreatedDate >= 2024-09-22T15:30:00Z"
    assert client.call[3] == "CreatedDate DESC"


def test_two_year_cutoff_handles_leap_day():
    assert two_year_cutoff(datetime(2024, 2, 29, tzinfo=UTC)) == datetime(
        2022, 2, 28, tzinfo=UTC
    )


def test_write_csv_uses_boolean_values(tmp_path):
    output = write_audit_review_outcomes(
        build_rows(
            [
                {
                    "Name": "AR-000123",
                    "CreatedDate": "2026-09-01T14:30:00.000+0000",
                    "Cert_Account__c": "001abc",
                    "Cert_Account__r": {"Name": "Example Steel"},
                    "Cert_CRG_Outcome__c": "CRG Withdrawal",
                    "CRG_Comments__c": "",
                }
            ]
        ),
        tmp_path / "out.csv",
    )

    with output.open(newline="", encoding="utf-8") as csv_file:
        assert next(csv.reader(csv_file)) == [
            "audit_review",
            "audit_review_created_date",
            "account_name",
            "account_id",
            "crg_outcome",
            "crg_comments",
            "standard",
            "drop",
            "additional",
            "cautionary_letter",
            "scope",
            "needs_manual_review",
        ]
        csv_file.seek(0)
        row = next(csv.DictReader(csv_file))
    assert row["audit_review"] == "AR-000123"
    assert row["audit_review_created_date"] == "2026-09-01T14:30:00.000+0000"
    assert row["standard"] == "False"
    assert row["drop"] == "True"
    assert row["additional"] == "False"
    assert row["cautionary_letter"] == "False"
    assert row["scope"] == "False"
    assert row["needs_manual_review"] == "False"
