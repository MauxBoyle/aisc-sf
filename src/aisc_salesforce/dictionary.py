"""Load the Salesforce schema dictionary into an export plan."""

from __future__ import annotations

import csv
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

REQUIRED_COLUMNS = {
    "Salesforce_Table",
    "Sensible_Python_Key",
    "Actual_Salesforce_API_Name",
    "ScriptsUsing",
}


class DictionaryError(ValueError):
    """The schema dictionary cannot be used to create an export plan."""


@dataclass(frozen=True)
class ExportField:
    """One Salesforce field and the CSV column name it maps to."""

    api_name: str
    python_key: str


def load_export_plan(path: Path) -> dict[str, list[ExportField]]:
    """Return selected fields grouped by object, keeping CSV row order."""
    try:
        with path.open(newline="", encoding="utf-8-sig") as dictionary_file:
            reader = csv.DictReader(dictionary_file)
            headers = set(reader.fieldnames or [])
            missing = REQUIRED_COLUMNS - headers
            if missing:
                raise DictionaryError(
                    "Schema dictionary is missing required columns: "
                    + ", ".join(sorted(missing))
                )

            plan: OrderedDict[str, list[ExportField]] = OrderedDict()
            keys_by_object: dict[str, set[str]] = {}
            for row_number, row in enumerate(reader, start=2):
                selected = (row.get("ScriptsUsing") or "").strip().upper() == "TRUE"
                if not selected:
                    continue
                object_name = (row.get("Salesforce_Table") or "").strip()
                api_name = (row.get("Actual_Salesforce_API_Name") or "").strip()
                python_key = (row.get("Sensible_Python_Key") or "").strip()
                if not all((object_name, api_name, python_key)):
                    raise DictionaryError(
                        f"Selected row {row_number} is incomplete; object, API name, "
                        "and Python key are required."
                    )
                object_keys = keys_by_object.setdefault(object_name, set())
                if python_key in object_keys:
                    raise DictionaryError(
                        f"Selected object {object_name!r} has duplicate output key "
                        f"{python_key!r}."
                    )
                object_keys.add(python_key)
                plan.setdefault(object_name, []).append(
                    ExportField(api_name, python_key)
                )
    except OSError as error:
        raise DictionaryError(
            f"Could not read schema dictionary {path}: {error}"
        ) from error

    if not plan:
        raise DictionaryError(
            "Schema dictionary contains no rows with ScriptsUsing=TRUE."
        )
    return dict(plan)
