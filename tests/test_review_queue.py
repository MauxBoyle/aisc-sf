import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from aisc_salesforce.review_queue import (
    QueueStatus,
    ReviewQueueStore,
    build_review_queue,
    iter_changes,
    manifest_json,
    transition_item,
    write_review_queue,
)
from aisc_salesforce.stage_profile_updates import CSV_COLUMNS

NOW = datetime(2026, 7, 17, 18, 0, tzinfo=UTC)


def queue_row(**changes):
    row = {column: "" for column in CSV_COLUMNS}
    row.update(
        {
            "source_submission_ids": json.dumps(["submission-1"]),
            "source_submission_names": json.dumps(["PU-100"]),
            "earliest_submission_date": "2026-07-15T14:30:00.000+0000",
            "latest_submission_date": "2026-07-15T14:30:00.000+0000",
            "account_id": "account-1",
            "account_name": "Acme Steel",
            "case_id": "case-1",
            "case_number": "00010001",
            "case_status": "Pending",
            "case_match_status": "matched",
            "has_key_updates": "false",
        }
    )
    row.update(changes)
    return row


def test_manifest_is_frozen_versioned_and_empty_queue_has_no_default():
    manifest = build_review_queue([], now=NOW)

    assert manifest.schema_version == 1
    assert manifest.default_next_item_id is None
    assert manifest.batches == ()
    with pytest.raises(FrozenInstanceError):
        manifest.schema_version = 2


def test_ids_json_and_order_are_stable_when_input_is_shuffled():
    ordinary = queue_row(
        source_submission_ids=json.dumps(["submission-2"]),
        source_submission_names=json.dumps(["PU-101"]),
        account_id="account-2",
        case_id="case-2",
        case_number="2",
        earliest_submission_date="2026-07-10T12:00:00+00:00",
        revised_company_name="New Name",
    )
    overdue = queue_row(
        source_submission_ids=json.dumps(["submission-1"]),
        account_id="account-1",
        case_id="case-1",
        case_number="1",
        earliest_submission_date="2026-07-16T12:00:00+00:00",
        earliest_key_update_date="2026-07-01T12:00:00+00:00",
        has_key_updates="true",
        revised_company_owner="New Owner",
    )

    first = build_review_queue([ordinary, overdue], now=NOW)
    second = build_review_queue([overdue, ordinary], now=NOW)

    assert manifest_json(first) == manifest_json(second)
    assert [batch.account.record_id for batch in first.batches] == [
        "account-1",
        "account-2",
    ]
    account_changes = [
        change for change in iter_changes(first) if change.phase == "account"
    ]
    assert [change.phase for change in account_changes] == ["account", "account"]


def test_missing_setup_and_ambiguous_matches_are_explicit():
    missing = queue_row(
        account_id="",
        case_id="",
        case_number="",
        case_match_status="missing",
        warnings=(
            "Submission does not contain an Account ID.\n"
            "Blocking Case match: no Case contains the source submission names."
        ),
    )
    ambiguous = queue_row(
        source_submission_ids=json.dumps(["submission-2"]),
        account_id="account-2",
        case_id="",
        case_match_status="ambiguous",
        warnings="Blocking Case match: ambiguous.",
    )

    manifest = build_review_queue([ambiguous, missing], now=NOW)
    changes = list(iter_changes(manifest))

    assert any(change.field == "Account__c" for change in changes)
    assert any(change.field == "Case" for change in changes)
    assert any(
        blocker.code == "ambiguous_case"
        for change in changes
        for blocker in change.blockers
    )
    assert manifest.default_next_item_id == next(
        change.id for change in changes if change.reviewable
    )


def test_change_statuses_recompute_parents_and_advance_default_next():
    manifest = build_review_queue(
        [queue_row(revised_company_name="New Name", revised_company_owner="New Owner")],
        now=NOW,
    )
    changes = [change for change in iter_changes(manifest) if change.phase == "account"]
    first, second = changes
    setup = next(change for change in iter_changes(manifest) if change.phase == "setup")
    manifest = transition_item(
        manifest, setup.id, QueueStatus.COMPLETED, outcome="applied"
    )
    assert manifest.default_next_item_id == first.id

    manifest = transition_item(manifest, first.id, QueueStatus.IN_PROGRESS)
    assert manifest.batches[0].status is QueueStatus.IN_PROGRESS
    assert manifest.default_next_item_id == second.id

    manifest = transition_item(
        manifest, first.id, QueueStatus.COMPLETED, outcome="no-op"
    )
    manifest = transition_item(
        manifest, second.id, QueueStatus.COMPLETED, outcome="applied"
    )
    assert manifest.default_next_item_id is None
    assert manifest.batches[0].rows[0].status is QueueStatus.COMPLETED
    assert manifest.batches[0].status is QueueStatus.COMPLETED
    assert (
        next(
            change.outcome for change in iter_changes(manifest) if change.id == first.id
        )
        == "no-op"
    )


@pytest.mark.parametrize(
    "status",
    [QueueStatus.FAILED, QueueStatus.STOPPED_EARLY, QueueStatus.BLOCKED],
)
def test_terminal_and_blocking_statuses_remove_change_from_default(status):
    manifest = build_review_queue([queue_row(revised_company_name="New Name")], now=NOW)
    changed = manifest
    for change in iter_changes(manifest):
        changed = transition_item(changed, change.id, status)

    assert changed.default_next_item_id is None
    assert changed.batches[0].status is status


def test_contact_change_is_shared_across_source_rows():
    resolution = {
        "classification": "use_existing",
        "normalized_email": "sam@example.com",
        "comparison_key": "sam@example.com",
        "sources": [
            {"kind": "role", "role": "principal", "submission_id": "submission-1"},
            {"kind": "role", "role": "quality", "submission_id": "submission-2"},
        ],
        "submitted": {"email": "sam@example.com", "title": "President"},
        "selected_contact": {"Id": "contact-1", "Email": "old@example.com"},
        "warnings": [],
    }
    first = queue_row(contact_resolutions=json.dumps([resolution]))
    second = queue_row(
        source_submission_ids=json.dumps(["submission-2"]),
        source_submission_names=json.dumps(["PU-101"]),
        contact_resolutions=json.dumps([resolution]),
    )

    manifest = build_review_queue([first, second], now=NOW)
    contact_ids = [
        change.id
        for batch in manifest.batches
        for row in batch.rows
        for change in row.changes
        if change.salesforce.object_name == "Contact" and change.field == "Title"
    ]

    assert len(contact_ids) == 2
    assert len(set(contact_ids)) == 1


def test_contact_json_is_deterministic_and_prior_cases_remain_references():
    first_resolution = {
        "classification": "ambiguous",
        "normalized_email": "sam@example.com",
        "comparison_key": "sam@example.com",
        "sources": [
            {"kind": "role", "role": "principal", "submission_id": "submission-1"}
        ],
        "submitted": {"title": "President"},
        "warnings": ["Review Contact identity."],
    }
    second_resolution = {
        **first_resolution,
        "sources": [
            {"kind": "role", "role": "principal", "submission_id": "submission-2"}
        ],
        "submitted": {"title": "Owner"},
    }
    prior = json.dumps(
        [
            {
                "object_name": "Case",
                "record_id": "older-case",
                "label": "00009999",
                "status": "Closed",
                "relationship": "prior_activity",
            }
        ]
    )
    first = queue_row(
        contact_resolutions=json.dumps([first_resolution]),
        prior_activity_references=prior,
    )
    second = queue_row(
        source_submission_ids=json.dumps(["submission-2"]),
        source_submission_names=json.dumps(["PU-101"]),
        contact_resolutions=json.dumps([second_resolution]),
        prior_activity_references=prior,
    )

    one = build_review_queue([first, second], now=NOW)
    two = build_review_queue([second, first], now=NOW)

    assert manifest_json(one) == manifest_json(two)
    references = one.batches[0].references
    assert any(
        reference.record_id == "older-case"
        and reference.relationship == "prior_activity"
        and reference.status == "Closed"
        for reference in references
    )


def test_json_publish_replaces_atomically_and_store_preserves_queue_path(
    tmp_path, monkeypatch
):
    manifest = build_review_queue([queue_row(revised_company_name="New Name")], now=NOW)
    path = tmp_path / "review_queue.json"
    replacements = []
    real_replace = __import__("os").replace

    def recording_replace(source, target):
        replacements.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr("aisc_salesforce.review_queue.os.replace", recording_replace)

    assert write_review_queue(manifest, path) == path
    assert json.loads(path.read_text())["schema_version"] == 1
    assert replacements[-1][1] == path
    assert replacements[-1][0] != path

    store = ReviewQueueStore(path, manifest)
    assert store.path == path


def test_refresh_preserves_completed_setup_but_recomputes_resolved_blockers(tmp_path):
    missing = queue_row(
        case_id="",
        case_number="",
        case_match_status="missing",
    )
    store = ReviewQueueStore(
        tmp_path / "review_queue.json",
        build_review_queue([missing], now=NOW),
    )
    setup = next(
        change for change in iter_changes(store.manifest) if change.field == "Case"
    )
    store.transition(setup.id, QueueStatus.COMPLETED, outcome="applied")

    store.refresh(build_review_queue([queue_row()], now=NOW))

    refreshed = next(
        change for change in iter_changes(store.manifest) if change.field == "Case"
    )
    assert refreshed.id != setup.id
    assert refreshed.status is QueueStatus.COMPLETED
    assert refreshed.outcome == "applied"

    ambiguous = queue_row(
        case_id="",
        case_number="",
        case_match_status="ambiguous",
    )
    blocked_store = ReviewQueueStore(
        tmp_path / "blocked.json",
        build_review_queue([ambiguous], now=NOW),
    )
    assert next(iter_changes(blocked_store.manifest)).status is QueueStatus.BLOCKED

    blocked_store.refresh(build_review_queue([queue_row()], now=NOW))

    assert next(iter_changes(blocked_store.manifest)).status is QueueStatus.PENDING
