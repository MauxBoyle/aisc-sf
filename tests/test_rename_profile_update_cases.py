from datetime import UTC, datetime

from aisc_salesforce.rename_profile_update_cases import (
    RenameProfileUpdateCasesService,
    correction_window,
)
from aisc_salesforce.salesforce import SalesforceError

NOW = datetime(2026, 7, 20, 18, 30, tzinfo=UTC)


def legacy_case(case_id, subject):
    return {"Id": case_id, "CaseNumber": case_id, "Subject": subject}


class FakeClient:
    def __init__(self, cases):
        self.cases = cases
        self.queries = []
        self.updated = []
        self.fail_ids = set()

    def query_records(self, object_name, fields, *, where=None, order_by=None):
        self.queries.append((object_name, fields, where, order_by))
        return list(self.cases)

    def update_record(self, object_name, record_id, payload):
        if record_id in self.fail_ids:
            raise SalesforceError("write failed")
        self.updated.append((object_name, record_id, payload))


def test_correction_window_is_seven_chicago_dates_converted_to_utc():
    start, end = correction_window(NOW)

    assert start == datetime(2026, 7, 14, 5, tzinfo=UTC)
    assert end == datetime(2026, 7, 21, 5, tzinfo=UTC)


def test_correction_window_uses_the_winter_chicago_utc_offset():
    start, end = correction_window(datetime(2026, 1, 20, 18, 30, tzinfo=UTC))

    assert start == datetime(2026, 1, 14, 6, tzinfo=UTC)
    assert end == datetime(2026, 1, 21, 6, tzinfo=UTC)


def test_preview_is_default_and_never_writes():
    client = FakeClient(
        [
            legacy_case(
                "5001",
                "2026-07-15: Profile Update Received for Acme Steel - PU-100",
            )
        ]
    )
    output = []

    counts = RenameProfileUpdateCasesService(
        client, now=NOW, output_fn=output.append
    ).run()

    assert counts.matched == 1
    assert counts.would_update == 1
    assert counts.updated == 0
    assert client.updated == []
    assert any(
        "2026-07-15: Profile Update Received for Acme Steel - PU-100"
        " -> AISC Profile Update for Acme Steel - PU-100 26-07-15" in line
        for line in output
    )
    where = client.queries[0][2]
    assert "CreatedDate >= 2026-07-14T05:00:00Z" in where
    assert "CreatedDate < 2026-07-21T05:00:00Z" in where


def test_apply_updates_only_subject_and_multiple_names_share_embedded_date():
    client = FakeClient(
        [
            legacy_case(
                "5001",
                "26-07-15 - Profile Update Received for Acme Steel - PU-100 / PU-101",
            )
        ]
    )

    counts = RenameProfileUpdateCasesService(client, now=NOW).run(apply=True)

    assert counts.updated == 1
    assert client.updated == [
        (
            "Case",
            "5001",
            {
                "Subject": (
                    "AISC Profile Update for Acme Steel - "
                    "PU-100 26-07-15 / PU-101 26-07-15"
                )
            },
        )
    ]


def test_apply_continues_after_partial_failure_and_reports_nonzero_failure_count():
    client = FakeClient(
        [
            legacy_case(
                "fails",
                "2026-07-15 - Profile Update Received for Acme - PU-100",
            ),
            legacy_case(
                "works",
                "2026-07-16: Profile Update Received for Beta - PU-200",
            ),
        ]
    )
    client.fail_ids.add("fails")

    counts = RenameProfileUpdateCasesService(client, now=NOW).run(apply=True)

    assert counts.matched == 2
    assert counts.updated == 1
    assert counts.failed == 1
    assert client.updated == [
        (
            "Case",
            "works",
            {"Subject": "AISC Profile Update for Beta - PU-200 26-07-16"},
        )
    ]


def test_unparseable_and_overlong_subjects_are_skipped_and_reruns_are_safe():
    client = FakeClient(
        [
            legacy_case(
                "no-date",
                "Profile Update Received for Acme Steel - PU-100",
            ),
            legacy_case(
                "too-long",
                "2026-07-15: Profile Update Received for " + "A" * 240 + " - PU-100",
            ),
            legacy_case(
                "already-new",
                "AISC Profile Update for Acme Steel - PU-100 26-07-15",
            ),
        ]
    )
    output = []

    counts = RenameProfileUpdateCasesService(
        client, now=NOW, output_fn=output.append
    ).run(apply=True)

    assert counts.matched == 1
    assert counts.updated == 0
    assert counts.skipped == 3
    assert counts.failed == 0
    assert client.updated == []
    assert any("trustworthy embedded received date" in line for line in output)
    assert any("255" in line for line in output)
