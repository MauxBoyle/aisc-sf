import csv
from pathlib import Path

import pytest

from aisc_salesforce import imis_contacts
from aisc_salesforce.imis_contacts import (
    CONTACT_BASIC_COLUMNS,
    ContactConsolidationError,
    consolidate_contactbasic,
)


def make_row(imis_id: str, **changes: str) -> dict[str, str]:
    row = {column: "" for column in CONTACT_BASIC_COLUMNS}
    row.update(
        {
            "iMIS Id": imis_id,
            "Full Name": f"Contact {imis_id}",
            "Company ID": f"00{imis_id}",
            "Major Key": f"000{imis_id}",
        }
    )
    row.update(changes)
    return row


def write_csv(
    path: Path,
    rows: list[dict[str, str]],
    *,
    fieldnames: list[str] | tuple[str, ...] = CONTACT_BASIC_COLUMNS,
    encoding: str = "utf-8",
) -> None:
    with path.open("w", encoding=encoding, newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        return list(reader.fieldnames or []), list(reader)


def test_initial_export_creates_only_canonical_combined_file(tmp_path):
    reversed_columns = list(reversed(CONTACT_BASIC_COLUMNS))
    write_csv(
        tmp_path / "Full_CSContactBasic_260719.csv",
        [make_row("0007")],
        fieldnames=reversed_columns,
    )

    result = consolidate_contactbasic(tmp_path, output_fn=lambda message: None)

    expected = tmp_path / "Combined_CSContactBasic_20260719.csv"
    assert result.fresh_export.name == "Full_CSContactBasic_260719.csv"
    assert result.prior_combined is None
    assert result.combined_path == expected
    assert result.changed_path is None
    assert result.new_path is None
    assert result.combined_count == 1
    headers, rows = read_csv(expected)
    assert headers == list(CONTACT_BASIC_COLUMNS)
    assert rows == [make_row("0007")]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "Combined_CSContactBasic_20260719.csv",
        "Full_CSContactBasic_260719.csv",
    ]


def test_discovery_uses_dates_in_names_instead_of_modification_times(tmp_path):
    older = tmp_path / "Full_CSContactBasic_260701.csv"
    newer = tmp_path / "Full_CSContactBasic_260719.csv"
    write_csv(older, [make_row("old")])
    write_csv(newer, [make_row("new")])
    older.touch()

    result = consolidate_contactbasic(tmp_path, output_fn=lambda message: None)

    assert result.fresh_export == newer
    assert read_csv(result.combined_path)[1] == [make_row("new")]


def test_later_export_merges_in_stable_order_and_writes_reports(tmp_path):
    write_csv(
        tmp_path / "Combined_CSContactBasic_20260718.csv",
        [
            make_row("1", Company="Old company"),
            make_row("2", City="Retained"),
            make_row("3", **{"State Province": "unchanged"}),
        ],
    )
    write_csv(
        tmp_path / "Full_CSContactBasic_260719.csv",
        [
            make_row("3", **{"State Province": "unchanged"}),
            make_row("1", Company="New company"),
            make_row("4", City="Appended first"),
            make_row("5", City="Appended second"),
        ],
    )

    result = consolidate_contactbasic(tmp_path, output_fn=lambda message: None)

    assert [row["iMIS Id"] for row in read_csv(result.combined_path)[1]] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]
    combined = read_csv(result.combined_path)[1]
    assert combined[0]["Company"] == "New company"
    assert combined[1]["City"] == "Retained"
    assert read_csv(result.changed_path)[1] == [make_row("1", Company="New company")]
    assert read_csv(result.new_path)[1] == [
        make_row("4", City="Appended first"),
        make_row("5", City="Appended second"),
    ]
    assert (result.combined_count, result.changed_count, result.new_count) == (5, 1, 2)


def test_later_export_writes_header_only_empty_reports(tmp_path):
    row = make_row("1")
    write_csv(tmp_path / "Combined_CSContactBasic_20260718.csv", [row])
    write_csv(tmp_path / "Full_CSContactBasic_260719.csv", [row])

    result = consolidate_contactbasic(tmp_path, output_fn=lambda message: None)

    assert read_csv(result.changed_path) == (list(CONTACT_BASIC_COLUMNS), [])
    assert read_csv(result.new_path) == (list(CONTACT_BASIC_COLUMNS), [])


def test_comparison_is_exact_and_identifiers_keep_leading_zeroes(tmp_path):
    prior = make_row("0001", Company="Acme", Email="person@example.com")
    fresh = make_row("0001", Company=" Acme", Email="Person@example.com")
    write_csv(tmp_path / "Combined_CSContactBasic_20260718.csv", [prior])
    write_csv(tmp_path / "Full_CSContactBasic_260719.csv", [fresh])

    result = consolidate_contactbasic(tmp_path, output_fn=lambda message: None)

    changed = read_csv(result.changed_path)[1]
    assert changed == [fresh]
    assert changed[0]["iMIS Id"] == "0001"
    assert changed[0]["Company ID"] == "000001"
    assert changed[0]["Major Key"] == "0000001"


def test_bom_quoted_commas_and_embedded_newlines_are_preserved(tmp_path):
    row = make_row("1", Company='Steel, "Quoted"', **{"Full Address": "Line 1\nLine 2"})
    write_csv(
        tmp_path / "Full_CSContactBasic_260719.csv",
        [row],
        encoding="utf-8-sig",
    )

    result = consolidate_contactbasic(tmp_path, output_fn=lambda message: None)

    assert read_csv(result.combined_path)[1] == [row]


@pytest.mark.parametrize(
    ("fieldnames", "message"),
    [
        (CONTACT_BASIC_COLUMNS[:-1], "missing headers: Website"),
        ((*CONTACT_BASIC_COLUMNS, "Unexpected"), "unexpected headers: Unexpected"),
    ],
)
def test_bad_headers_fail_before_writing(tmp_path, fieldnames, message):
    write_csv(
        tmp_path / "Full_CSContactBasic_260719.csv",
        [make_row("1")],
        fieldnames=fieldnames,
    )

    with pytest.raises(ContactConsolidationError, match=message):
        consolidate_contactbasic(tmp_path, output_fn=lambda output: None)

    assert not list(tmp_path.glob("Combined_*.csv"))


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("Full_CSContactBasic_bad.csv", "malformed CSContactBasic filename"),
        ("Full_CSContactBasic_261332.csv", "invalid date"),
        ("Combined_CSContactBasic_2026-07-18.csv", "malformed CSContactBasic filename"),
        ("Combined_CSContactBasic_20260230.csv", "invalid date"),
    ],
)
def test_malformed_relevant_filenames_fail(tmp_path, filename, message):
    write_csv(tmp_path / filename, [make_row("1")])

    with pytest.raises(ContactConsolidationError, match=message):
        consolidate_contactbasic(tmp_path, output_fn=lambda output: None)


def test_missing_directory_or_fresh_export_fails(tmp_path):
    with pytest.raises(ContactConsolidationError, match="directory does not exist"):
        consolidate_contactbasic(tmp_path / "missing")
    write_csv(tmp_path / "Combined_CSContactBasic_20260718.csv", [make_row("1")])
    with pytest.raises(ContactConsolidationError, match="No Full_CSContactBasic"):
        consolidate_contactbasic(tmp_path)


def test_fresh_export_must_be_newer_than_combined_table(tmp_path):
    write_csv(tmp_path / "Combined_CSContactBasic_20260719.csv", [make_row("1")])
    write_csv(tmp_path / "Full_CSContactBasic_260719.csv", [make_row("1")])

    with pytest.raises(ContactConsolidationError, match="must be newer"):
        consolidate_contactbasic(tmp_path)


def test_duplicate_ids_are_all_omitted_and_warned_by_filename(tmp_path):
    prior_name = "Combined_CSContactBasic_20260718.csv"
    fresh_name = "Full_CSContactBasic_260719.csv"
    write_csv(
        tmp_path / prior_name,
        [make_row("prior-duplicate"), make_row("keep"), make_row("prior-duplicate")],
    )
    write_csv(
        tmp_path / fresh_name,
        [
            make_row("keep", City="should not replace"),
            make_row("fresh-duplicate"),
            make_row("fresh-duplicate"),
            make_row("new"),
        ],
    )
    messages = []

    result = consolidate_contactbasic(tmp_path, output_fn=messages.append)

    assert [row["iMIS Id"] for row in read_csv(result.combined_path)[1]] == [
        "keep",
        "new",
    ]
    assert read_csv(result.combined_path)[1][0]["City"] == "should not replace"
    assert any(
        prior_name in message and "prior-duplicate" in message for message in messages
    )
    assert any(
        fresh_name in message and "fresh-duplicate" in message for message in messages
    )


def test_duplicated_fresh_id_leaves_valid_prior_row_unchanged(tmp_path):
    old = make_row("1", City="Old")
    write_csv(tmp_path / "Combined_CSContactBasic_20260718.csv", [old])
    write_csv(
        tmp_path / "Full_CSContactBasic_260719.csv",
        [make_row("1", City="First"), make_row("1", City="Second")],
    )

    result = consolidate_contactbasic(tmp_path, output_fn=lambda output: None)

    assert read_csv(result.combined_path)[1] == [old]
    assert read_csv(result.changed_path)[1] == []
    assert read_csv(result.new_path)[1] == []


def test_blank_ids_are_skipped_and_csv_row_numbers_are_reported(tmp_path):
    name = "Full_CSContactBasic_260719.csv"
    write_csv(tmp_path / name, [make_row(""), make_row("   "), make_row("valid")])
    messages = []

    result = consolidate_contactbasic(tmp_path, output_fn=messages.append)

    assert read_csv(result.combined_path)[1] == [make_row("valid")]
    assert any(name in message and "2, 3" in message for message in messages)


def test_existing_output_collision_is_refused_without_changes(tmp_path):
    write_csv(tmp_path / "Combined_CSContactBasic_20260718.csv", [make_row("old")])
    write_csv(tmp_path / "Full_CSContactBasic_260719.csv", [make_row("new")])
    collision = tmp_path / "Changed_CSContactBasic_20260719.csv"
    collision.write_text("do not replace", encoding="utf-8")

    with pytest.raises(ContactConsolidationError, match="already exists"):
        consolidate_contactbasic(tmp_path)

    assert collision.read_text(encoding="utf-8") == "do not replace"
    assert not (tmp_path / "Combined_CSContactBasic_20260719.csv").exists()


def test_write_failure_removes_temporary_and_new_output_files(tmp_path, monkeypatch):
    write_csv(tmp_path / "Combined_CSContactBasic_20260718.csv", [make_row("old")])
    write_csv(tmp_path / "Full_CSContactBasic_260719.csv", [make_row("new")])
    real_write = imis_contacts._write_csv_file
    calls = 0

    def fail_second_write(path, rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return real_write(path, rows)

    monkeypatch.setattr(imis_contacts, "_write_csv_file", fail_second_write)

    with pytest.raises(ContactConsolidationError, match="Could not publish.*disk full"):
        consolidate_contactbasic(tmp_path)

    assert not list(tmp_path.glob(".*.tmp-*"))
    assert not (tmp_path / "Combined_CSContactBasic_20260719.csv").exists()
    assert not (tmp_path / "Changed_CSContactBasic_20260719.csv").exists()
    assert not (tmp_path / "New_CSContactBasic_20260719.csv").exists()
