import csv
import json
from datetime import UTC, datetime

import pytest

from aisc_salesforce.dictionary import ExportField
from aisc_salesforce.snapshot import write_snapshot


def test_write_snapshot_writes_csv_manifest_and_empty_headers(tmp_path):
    plan = {
        "Account": [ExportField("Name", "name"), ExportField("Phone", "phone")],
        "Contact": [ExportField("Email", "email")],
    }
    path = write_snapshot(
        plan,
        {"Account": [{"Name": "Acme", "Phone": None}], "Contact": []},
        tmp_path,
        datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
    )
    assert path.name == "2026-07-14T12-00-00Z"
    with (path / "account.csv").open(newline="") as file:
        assert list(csv.reader(file)) == [["name", "phone"], ["Acme", ""]]
    assert (path / "contact.csv").read_text() == "email\n"
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["created_at"] == "2026-07-14T12:00:00Z"
    assert manifest["objects"][0]["fields"][0] == {
        "salesforce_api_name": "Name",
        "python_key": "name",
    }


def test_write_snapshot_cleans_up_after_failure(tmp_path, monkeypatch):
    plan = {"Account": [ExportField("Name", "name")]}
    monkeypatch.setattr(
        "aisc_salesforce.snapshot._write_csv",
        lambda *args: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError, match="disk full"):
        write_snapshot(plan, {"Account": []}, tmp_path)
    assert not list(tmp_path.iterdir())
