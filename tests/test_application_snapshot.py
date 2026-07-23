import csv
from datetime import UTC, date, datetime

import pytest

from aisc_salesforce.application_snapshot import (
    APPLICATION_RECORD_TYPE_IDS,
    APPLICATION_STAGES,
    CASE_CANCELLED_STAGE,
    RECORD_TYPE_ALIASES,
    ApplicationSnapshotError,
    ApplicationSnapshotService,
    aggregate_application_snapshot,
    application_stage,
    classify_application_type,
    is_qualifying_case,
    is_valid_audit,
    select_latest_valid_audits,
    write_application_snapshot,
)

TODAY = date(2026, 7, 23)
FABRICATOR = "0125w0000013inhAAA"
ERECTOR = "0125w0000013incAAA"
INTERNATIONAL = "0125w0000013inrAAA"


def case_record(**changes):
    record = {
        "Id": "case-1",
        "AccountId": "account-1",
        "CreatedDate": "2026-07-01T00:00:00Z",
        "RecordTypeId": FABRICATOR,
        "Cert_Stage__c": "Doc_Audit",
        "Cert_Is_this_a_scope_change__c": "No",
        "Cert_Expedited_Application__c": False,
        "Account": {
            "BillingCountry": "United States",
            "Cert_Certification_Status__c": "Initials",
        },
    }
    account_changes = changes.pop("Account", None)
    record.update(changes)
    if account_changes is not None:
        record["Account"].update(account_changes)
    return record


def audit_record(**changes):
    record = {
        "Id": "audit-1",
        "Cert_Account__c": "account-1",
        "Cert_Audit_Date__c": "2026-07-23",
        "CreatedDate": "2026-07-01T12:00:00.000+0000",
        "Cert_Audit_Status__c": "Scheduled",
        "Cert_Audit_Type__c": "Initial",
    }
    record.update(changes)
    return record


def test_record_type_aliases_include_all_supplied_values():
    assert RECORD_TYPE_ALIASES == {
        "0125w000000BaAdAAK": "Scope Change",
        "0125w000000BVTTAA4": "Web Question",
        "0125w000000BXdEAAW": "Audit Date",
        "0125w000000BZyIAAG": "Membership",
        "0125w0000013incAAA": "Erector Application",
        "0125w0000013inhAAA": "Fabricator Application",
        "0125w0000013inrAAA": "International Application",
        "012f20000012CCDAA2": "Certification",
        "012f20000012CCEAA2": "Solutions Center",
    }
    assert APPLICATION_RECORD_TYPE_IDS == frozenset(
        {FABRICATOR, ERECTOR, INTERNATIONAL}
    )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"Account": {"Cert_Certification_Status__c": "Certified"}}, False),
        ({"Account": {"Cert_Certification_Status__c": None}}, False),
        ({"Cert_Stage__c": CASE_CANCELLED_STAGE}, False),
        ({"Cert_Stage__c": None}, True),
        ({"Cert_Is_this_a_scope_change__c": "Yes"}, False),
        ({"Cert_Is_this_a_scope_change__c": None}, True),
        ({"RecordTypeId": "0125w000000BaAdAAK"}, False),
        ({"Account": None}, False),
    ],
)
def test_case_filter_includes_only_qualifying_applications(changes, expected):
    if changes.get("Account", ...) is None:
        record = case_record()
        record["Account"] = None
    else:
        record = case_record(**changes)
    assert is_qualifying_case(record) is expected


@pytest.mark.parametrize("record_type_id", [FABRICATOR, ERECTOR, INTERNATIONAL])
def test_each_application_record_type_is_allowed(record_type_id):
    assert is_qualifying_case(case_record(RecordTypeId=record_type_id))


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"Cert_Audit_Status__c": "Canceled"}, False),
        ({"Cert_Audit_Status__c": "Withdrawn"}, False),
        ({"Cert_Audit_Status__c": None}, True),
        ({"Cert_Audit_Type__c": "Additional"}, False),
        ({"Cert_Audit_Type__c": "Appeal"}, False),
        ({"Cert_Audit_Type__c": "SA-NYC"}, False),
        ({"Cert_Audit_Type__c": "Preassessment"}, False),
        ({"Cert_Audit_Type__c": None}, True),
    ],
)
def test_audit_filter_retains_null_status_and_type(changes, expected):
    assert is_valid_audit(audit_record(**changes)) is expected


def test_latest_valid_audit_uses_effective_date_created_date_and_id_tiebreakers():
    audits = [
        audit_record(
            Id="dated",
            Cert_Audit_Date__c="2026-07-10",
            CreatedDate="2026-07-20T12:00:00Z",
        ),
        audit_record(
            Id="undated-a",
            Cert_Audit_Date__c=None,
            CreatedDate="2026-07-21T12:00:00Z",
        ),
        audit_record(
            Id="undated-b",
            Cert_Audit_Date__c=None,
            CreatedDate="2026-07-21T12:00:00Z",
        ),
        audit_record(
            Id="ignored",
            Cert_Audit_Date__c="2026-08-01",
            Cert_Audit_Status__c="Canceled",
        ),
    ]
    assert select_latest_valid_audits(audits)["account-1"]["Id"] == "undated-b"


def test_latest_valid_audit_breaks_effective_date_tie_with_created_date():
    audits = [
        audit_record(
            Id="older-created",
            CreatedDate="2026-07-01T12:00:00Z",
        ),
        audit_record(
            Id="newer-created",
            CreatedDate="2026-07-02T12:00:00Z",
        ),
    ]
    assert select_latest_valid_audits(audits)["account-1"]["Id"] == "newer-created"


@pytest.mark.parametrize(
    ("case_stage", "audit_status", "audit_date", "expected"),
    [
        (
            "Eligibility_Review",
            "Pending Acceptance",
            "2026-08-01",
            "Awaiting Audit Assignment",
        ),
        (None, None, None, "Initial Review"),
        ("New_Application", None, None, "Initial Review"),
        ("Eligibility_Review", "Scheduled", "2026-08-01", "Eligibility Review"),
        ("Doc_Audit", "Scheduled", None, "Doc Audit"),
        ("Doc_Audit", "Scheduled", "2026-07-22", "Awaiting CRG Decision"),
        ("Doc_Audit", "Scheduled", "2026-08-01", "Awaiting Audit"),
        (
            "Doc_Audit",
            "Reschedule in Progress",
            "2026-08-01",
            "Awaiting Audit",
        ),
        ("Pending_AuditAssignment", None, None, "Awaiting Audit Assignment"),
        (
            "Pending_AuditAssignment",
            "Reschedule in Progress",
            "2026-08-01",
            "Awaiting Audit Assignment",
        ),
        (
            "Pending_AuditAssignment",
            "Scheduled",
            None,
            "Awaiting Audit Assignment",
        ),
        ("Pending_AuditAssignment", "Scheduled", "2026-07-23", "Awaiting Audit"),
        (
            "Pending_AuditAssignment",
            "Scheduled",
            "2026-07-22",
            "Awaiting CRG Decision",
        ),
        ("Pending_AuditAssignment", "Scheduled", "2026-07-24", "Awaiting Audit"),
        ("Awaiting_Custom_Step", "Scheduled", "2026-08-01", "Awaiting Custom Step"),
    ],
)
def test_application_stage_reproduces_tableau_branches(
    case_stage, audit_status, audit_date, expected
):
    record = case_record(Cert_Stage__c=case_stage)
    audit = (
        audit_record(
            Cert_Audit_Status__c=audit_status,
            Cert_Audit_Date__c=audit_date,
        )
        if audit_status is not None or audit_date is not None
        else None
    )
    assert application_stage(record, audit, today=TODAY) == expected


def test_malformed_salesforce_dates_fail_clearly():
    with pytest.raises(ApplicationSnapshotError, match="Cert_Audit_Date__c"):
        application_stage(
            case_record(Cert_Stage__c="Pending_AuditAssignment"),
            audit_record(Cert_Audit_Date__c="not-a-date"),
            today=TODAY,
        )
    with pytest.raises(ApplicationSnapshotError, match="CreatedDate"):
        select_latest_valid_audits(
            [audit_record(Cert_Audit_Date__c=None, CreatedDate="not-a-date")]
        )


@pytest.mark.parametrize(
    ("country", "expedited", "expected"),
    [
        ("United States", False, "domestic_regular"),
        ("United States", None, "domestic_regular"),
        ("United States", "true", "domestic_regular"),
        ("United States", True, "domestic_expedited"),
        ("Canada", True, "international_regular"),
        (None, True, "international_regular"),
    ],
)
def test_origin_and_speed_use_exact_country_and_boolean_rules(
    country, expedited, expected
):
    record = case_record(
        Cert_Expedited_Application__c=expedited,
        Account={"BillingCountry": country},
    )
    assert classify_application_type(record) == expected


def test_invalid_audit_never_influences_application_stage():
    cases = [case_record(Cert_Stage__c="Pending_AuditAssignment")]
    audits = [
        audit_record(
            Id="valid-past",
            Cert_Audit_Date__c="2026-07-20",
            Cert_Audit_Status__c="Complete",
        ),
        audit_record(
            Id="invalid-future",
            Cert_Audit_Date__c="2026-08-20",
            Cert_Audit_Status__c="Canceled",
        ),
    ]

    result = aggregate_application_snapshot(cases, audits, today=TODAY)

    crg_row = next(
        row
        for row in result.rows
        if row["application_stage"] == "Awaiting CRG Decision"
    )
    assert crg_row["domestic_regular"] == 1


def test_audit_must_be_created_on_or_after_its_application_case():
    cases = [
        case_record(
            Id="earlier-case",
            CreatedDate="2026-07-01T00:00:00Z",
            Cert_Stage__c="Pending_AuditAssignment",
        ),
        case_record(
            Id="later-case",
            CreatedDate="2026-07-02T00:00:00Z",
            Cert_Stage__c="Pending_AuditAssignment",
        ),
    ]
    audits = [
        audit_record(
            Id="between-cases",
            CreatedDate="2026-07-01T12:00:00Z",
            Cert_Audit_Date__c="2026-07-20",
        ),
        audit_record(
            Id="same-time-as-later-case",
            CreatedDate="2026-07-02T00:00:00Z",
            Cert_Audit_Date__c="2026-07-24",
        ),
    ]

    result = aggregate_application_snapshot(cases, audits, today=TODAY)

    awaiting_audit = next(
        row for row in result.rows if row["application_stage"] == "Awaiting Audit"
    )
    assert awaiting_audit["domestic_regular"] == 2


def test_audit_created_before_its_only_application_case_is_not_matched():
    result = aggregate_application_snapshot(
        [
            case_record(
                CreatedDate="2026-07-02T00:00:00Z",
                Cert_Stage__c="Pending_AuditAssignment",
            )
        ],
        [
            audit_record(
                CreatedDate="2026-07-01T23:59:59Z",
                Cert_Audit_Date__c="2026-07-24",
            )
        ],
        today=TODAY,
    )

    awaiting_audit_assignment = next(
        row
        for row in result.rows
        if row["application_stage"] == "Awaiting Audit Assignment"
    )
    assert awaiting_audit_assignment["domestic_regular"] == 1


def test_aggregation_counts_each_case_once_and_keeps_all_official_rows():
    cases = [
        case_record(
            Id="domestic-regular",
            AccountId="regular",
            Cert_Stage__c=None,
        ),
        case_record(
            Id="domestic-fast",
            AccountId="fast",
            Cert_Stage__c=None,
            Cert_Expedited_Application__c=True,
        ),
        case_record(
            Id="international",
            AccountId="international-account",
            Cert_Stage__c=None,
            Cert_Expedited_Application__c=True,
            Account={"BillingCountry": "Canada"},
        ),
        case_record(
            Id="unexpected",
            AccountId="unexpected-account",
            Cert_Stage__c="Zebra_Review",
        ),
        case_record(
            Id="unexpected",
            AccountId="unexpected-account",
            Cert_Stage__c="Zebra_Review",
        ),
        case_record(Id="filtered", Cert_Stage__c=CASE_CANCELLED_STAGE),
    ]
    result = aggregate_application_snapshot(cases, [], today=TODAY)

    assert result.qualifying_case_count == 4
    assert [row["application_stage"] for row in result.rows[:6]] == list(
        APPLICATION_STAGES
    )
    assert result.rows[0] == {
        "application_stage": "Initial Review",
        "domestic_regular": 1,
        "domestic_expedited": 1,
        "international_regular": 1,
    }
    assert all(
        row["domestic_regular"]
        + row["domestic_expedited"]
        + row["international_regular"]
        == 0
        for row in result.rows[1:6]
    )
    assert result.rows[-1]["application_stage"] == "Zebra Review"
    assert result.rows[-1]["domestic_regular"] == 1
    assert result.unexpected_stages == {"Zebra Review": 1}


def test_unexpected_application_stages_are_sorted():
    cases = [
        case_record(Id="z", AccountId="z", Cert_Stage__c="Zeta"),
        case_record(Id="a", AccountId="a", Cert_Stage__c="Alpha"),
    ]
    result = aggregate_application_snapshot(cases, [], today=TODAY)
    assert [row["application_stage"] for row in result.rows[-2:]] == [
        "Alpha",
        "Zeta",
    ]


def test_service_queries_relationship_case_fields_and_minimal_audit_fields():
    calls = []

    class Client:
        def query_records(self, object_name, fields, *, where=None, order_by=None):
            calls.append((object_name, fields, where, order_by))
            return []

    result = ApplicationSnapshotService(Client(), today=TODAY).build()

    assert result.qualifying_case_count == 0
    case_call, audit_call = calls
    assert case_call[0] == "Case"
    assert "Account.BillingCountry" in case_call[1]
    assert "Account.Cert_Certification_Status__c" in case_call[1]
    assert "RecordTypeId" in case_call[1]
    assert "Cert_Account__c" in audit_call[1]
    assert "Cert_Audit_Date__c" in audit_call[1]
    assert len(calls) == 2


def test_writer_publishes_csv_atomically_and_suffixes_same_second(tmp_path):
    result = aggregate_application_snapshot([], [], today=TODAY)
    now = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)

    first = write_application_snapshot(result, tmp_path, now=now)
    second = write_application_snapshot(result, tmp_path, now=now)

    assert first.name == "2026-07-23T15-30-00Z"
    assert second.name == "2026-07-23T15-30-00Z-01"
    with (first / "application_snapshot.csv").open(newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert len(rows) == 6
    assert rows[0] == {
        "application_stage": "Initial Review",
        "domestic_regular": "0",
        "domestic_expedited": "0",
        "international_regular": "0",
    }


def test_writer_failure_leaves_no_partial_snapshot(tmp_path, monkeypatch):
    result = aggregate_application_snapshot([], [], today=TODAY)
    monkeypatch.setattr(
        "aisc_salesforce.application_snapshot._write_application_csv",
        lambda *args: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        write_application_snapshot(result, tmp_path)

    assert not list(tmp_path.iterdir())
