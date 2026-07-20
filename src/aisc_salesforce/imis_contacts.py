"""Safely consolidate dated iMIS ``CSContactBasic`` CSV exports."""

from __future__ import annotations

import csv
import os
import re
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

CONTACT_BASIC_COLUMNS = (
    "City",
    "Company",
    "Full Name",
    "iMIS Id",
    "Member Type",
    "State Province",
    "Company ID",
    "Company Member Type",
    "Date Added",
    "Email",
    "Is Company",
    "Is Member",
    "Join Date",
    "Major Key",
    "Member Status",
    "Status",
    "Category",
    "Last Updated",
    "Full Address",
    "Country",
    "Website",
)

_FULL_PREFIX = "Full_CSContactBasic_"
_COMBINED_PREFIX = "Combined_CSContactBasic_"
_FULL_PATTERN = re.compile(r"Full_CSContactBasic_(\d{6})\.csv\Z")
_COMBINED_PATTERN = re.compile(r"Combined_CSContactBasic_(\d{8})\.csv\Z")


class ContactConsolidationError(Exception):
    """Explain why iMIS contact consolidation could not safely finish."""


@dataclass(frozen=True)
class ConsolidationResult:
    """Files selected and row counts produced by one consolidation run."""

    fresh_export: Path
    prior_combined: Path | None
    combined_path: Path
    changed_path: Path | None
    new_path: Path | None
    combined_count: int
    changed_count: int
    new_count: int


@dataclass(frozen=True)
class _DatedFile:
    path: Path
    export_date: date


def consolidate_contactbasic(
    directory: Path | str = Path("imis_contactbasic"),
    output_fn: Callable[[str], None] = print,
) -> ConsolidationResult:
    """Merge the newest dated contact export with the newest combined table.

    CSV values remain strings throughout the workflow. Duplicate and blank IDs
    are skipped with warnings sent to ``output_fn``.

    Args:
        directory: Folder containing the iMIS input and output CSV files.
        output_fn: Function that receives readable warning messages.

    Returns:
        Paths and row counts for the completed consolidation.

    Raises:
        ContactConsolidationError: If discovery, validation, or publication fails.
    """
    directory = Path(directory)
    fresh, prior = _discover_inputs(directory)
    if prior is not None and fresh.export_date <= prior.export_date:
        raise ContactConsolidationError(
            f"Fresh export {fresh.path.name} must be newer than "
            f"combined table {prior.path.name}."
        )

    date_text = fresh.export_date.strftime("%Y%m%d")
    combined_path = directory / f"Combined_CSContactBasic_{date_text}.csv"
    changed_path = (
        directory / f"Changed_CSContactBasic_{date_text}.csv" if prior else None
    )
    new_path = directory / f"New_CSContactBasic_{date_text}.csv" if prior else None
    targets = [path for path in (combined_path, changed_path, new_path) if path]
    collisions = [path.name for path in targets if path.exists()]
    if collisions:
        raise ContactConsolidationError(
            "Refusing to overwrite output that already exists: " + ", ".join(collisions)
        )

    prior_rows = _read_contact_rows(prior.path, output_fn) if prior else []
    fresh_rows = _read_contact_rows(fresh.path, output_fn)
    combined_rows, changed_rows, new_rows = _merge_rows(prior_rows, fresh_rows)

    output_rows: list[tuple[Path, list[dict[str, str]]]] = [
        (combined_path, combined_rows)
    ]
    if changed_path is not None and new_path is not None:
        output_rows.extend(((changed_path, changed_rows), (new_path, new_rows)))
    _publish_csv_files(output_rows)

    return ConsolidationResult(
        fresh_export=fresh.path,
        prior_combined=prior.path if prior else None,
        combined_path=combined_path,
        changed_path=changed_path,
        new_path=new_path,
        combined_count=len(combined_rows),
        changed_count=len(changed_rows),
        new_count=len(new_rows),
    )


def _discover_inputs(directory: Path) -> tuple[_DatedFile, _DatedFile | None]:
    if not directory.exists():
        raise ContactConsolidationError(f"The directory does not exist: {directory}")
    if not directory.is_dir():
        raise ContactConsolidationError(f"The path is not a directory: {directory}")

    fresh_files: list[_DatedFile] = []
    combined_files: list[_DatedFile] = []
    try:
        paths = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise ContactConsolidationError(
            f"Could not read directory {directory}: {error}"
        ) from error

    for path in paths:
        if path.name.startswith(_FULL_PREFIX):
            fresh_files.append(_parse_dated_file(path, _FULL_PATTERN, "%y%m%d"))
        elif path.name.startswith(_COMBINED_PREFIX):
            combined_files.append(_parse_dated_file(path, _COMBINED_PATTERN, "%Y%m%d"))

    if not fresh_files:
        raise ContactConsolidationError(
            f"No Full_CSContactBasic_YYMMDD.csv export was found in {directory}."
        )
    fresh = max(fresh_files, key=lambda item: item.export_date)
    prior = max(combined_files, key=lambda item: item.export_date, default=None)
    return fresh, prior


def _parse_dated_file(
    path: Path, pattern: re.Pattern[str], format_text: str
) -> _DatedFile:
    match = pattern.fullmatch(path.name)
    if match is None:
        raise ContactConsolidationError(
            f"Found malformed CSContactBasic filename: {path.name}"
        )
    try:
        export_date = datetime.strptime(match.group(1), format_text).date()
    except ValueError as error:
        raise ContactConsolidationError(
            f"Filename {path.name} contains an invalid date."
        ) from error
    if format_text == "%y%m%d":
        export_date = export_date.replace(year=2000 + int(match.group(1)[:2]))
    if not path.is_file():
        raise ContactConsolidationError(f"Expected a CSV file: {path}")
    return _DatedFile(path, export_date)


def _read_contact_rows(
    path: Path,
    output_fn: Callable[[str], None],
) -> list[dict[str, str]]:
    numbered_rows: list[tuple[int, dict[str, str]]] = []
    blank_row_numbers: list[int] = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file, strict=True)
            _validate_headers(path, reader.fieldnames)
            for row_number, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise ContactConsolidationError(
                        f"{path.name} has a malformed CSV record at row {row_number}."
                    )
                canonical_row = {
                    column: row[column] for column in CONTACT_BASIC_COLUMNS
                }
                if not canonical_row["iMIS Id"].strip():
                    blank_row_numbers.append(row_number)
                else:
                    numbered_rows.append((row_number, canonical_row))
    except ContactConsolidationError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise ContactConsolidationError(
            f"Could not read {path.name}: {error}"
        ) from error

    if blank_row_numbers:
        row_text = ", ".join(str(number) for number in blank_row_numbers)
        output_fn(
            f"Warning: {path.name} skipped blank or whitespace-only iMIS Id "
            f"at CSV row(s): {row_text}."
        )

    id_counts = Counter(row["iMIS Id"] for _, row in numbered_rows)
    duplicate_ids = [imis_id for imis_id, count in id_counts.items() if count > 1]
    if duplicate_ids:
        output_fn(
            f"Warning: {path.name} omitted every row for duplicate iMIS Id "
            f"value(s): {', '.join(duplicate_ids)}."
        )
    duplicate_set = set(duplicate_ids)
    return [row for _, row in numbered_rows if row["iMIS Id"] not in duplicate_set]


def _validate_headers(path: Path, fieldnames: list[str] | None) -> None:
    headers = fieldnames or []
    expected = set(CONTACT_BASIC_COLUMNS)
    missing = [column for column in CONTACT_BASIC_COLUMNS if column not in headers]
    unexpected = [column for column in headers if column not in expected]
    duplicates = [column for column, count in Counter(headers).items() if count > 1]
    problems = []
    if missing:
        problems.append("missing headers: " + ", ".join(missing))
    if unexpected:
        problems.append("unexpected headers: " + ", ".join(unexpected))
    if duplicates:
        problems.append("duplicate headers: " + ", ".join(duplicates))
    if problems:
        raise ContactConsolidationError(
            f"{path.name} has invalid CSV headers ({'; '.join(problems)})."
        )


def _merge_rows(
    prior_rows: Sequence[dict[str, str]],
    fresh_rows: Sequence[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    combined = [row.copy() for row in prior_rows]
    indexes = {row["iMIS Id"]: index for index, row in enumerate(combined)}
    changed: list[dict[str, str]] = []
    new: list[dict[str, str]] = []
    comparison_columns = [
        column for column in CONTACT_BASIC_COLUMNS if column != "iMIS Id"
    ]

    for fresh_row in fresh_rows:
        imis_id = fresh_row["iMIS Id"]
        index = indexes.get(imis_id)
        if index is None:
            indexes[imis_id] = len(combined)
            combined.append(fresh_row.copy())
            new.append(fresh_row.copy())
            continue
        old_row = combined[index]
        if any(old_row[column] != fresh_row[column] for column in comparison_columns):
            changed.append(fresh_row.copy())
        combined[index] = fresh_row.copy()
    return combined, changed, new


def _publish_csv_files(
    output_rows: Sequence[tuple[Path, list[dict[str, str]]]],
) -> None:
    temporary_paths: list[Path] = []
    published_paths: list[Path] = []
    try:
        for target, rows in output_rows:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{target.name}.tmp-",
            )
            temporary_path = Path(temporary_name)
            temporary_paths.append(temporary_path)
            os.close(descriptor)
            _write_csv_file(temporary_path, rows)

        for (target, _), temporary_path in zip(
            output_rows, temporary_paths, strict=True
        ):
            os.link(temporary_path, target)
            published_paths.append(target)
            temporary_path.unlink()
    except (OSError, csv.Error) as error:
        for path in published_paths:
            _unlink_if_present(path)
        for path in temporary_paths:
            _unlink_if_present(path)
        raise ContactConsolidationError(
            f"Could not publish iMIS contact CSV files: {error}"
        ) from error


def _write_csv_file(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CONTACT_BASIC_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
