"""Write completed Salesforce exports as atomic snapshot folders."""

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .dictionary import ExportField

FILENAMES = {
    "Account": "account.csv",
    "Contact": "contact.csv",
    "Case": "case.csv",
    "Cert_Audit__c": "cert_audit.csv",
    "Company_Profile_Change__c": "company_profile_change.csv",
}


def write_snapshot(
    export_plan: dict[str, list[ExportField]],
    records_by_object: dict[str, list[dict]],
    output_dir: Path,
    now: datetime | None = None,
) -> Path:
    """Write all files in a temporary directory before publishing the snapshot."""
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    folder_name = timestamp.strftime("%Y-%m-%dT%H-%M-%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / folder_name
    if final_path.exists():
        raise FileExistsError(f"Snapshot destination already exists: {final_path}")
    temporary_path = output_dir / f".{folder_name}-{uuid4().hex}.tmp"
    try:
        temporary_path.mkdir()
        objects: list[dict] = []
        for object_name, fields in export_plan.items():
            filename = FILENAMES.get(object_name, _safe_filename(object_name))
            records = records_by_object.get(object_name, [])
            _write_csv(temporary_path / filename, fields, records)
            objects.append(
                {
                    "salesforce_object": object_name,
                    "file": filename,
                    "row_count": len(records),
                    "fields": [
                        {
                            "salesforce_api_name": field.api_name,
                            "python_key": field.python_key,
                        }
                        for field in fields
                    ],
                }
            )
        manifest = {
            "created_at": timestamp.isoformat().replace("+00:00", "Z"),
            "objects": objects,
        }
        (temporary_path / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary_path, final_path)
    except Exception:
        shutil.rmtree(temporary_path, ignore_errors=True)
        raise
    return final_path


def _write_csv(path: Path, fields: list[ExportField], records: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output, fieldnames=[field.python_key for field in fields]
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    field.python_key: _csv_value(record.get(field.api_name))
                    for field in fields
                }
            )


def _csv_value(value: object) -> object:
    return "" if value is None else value


def _safe_filename(object_name: str) -> str:
    return object_name.lower().replace("__c", "").replace("_", "_") + ".csv"
