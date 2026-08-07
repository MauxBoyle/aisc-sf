import os

import pytest

from aisc_salesforce.filesystem import sync_directory


def test_sync_directory_skips_directory_handles_on_windows(monkeypatch):
    calls = []

    with monkeypatch.context() as patch:
        patch.setattr("aisc_salesforce.filesystem.os.name", "nt")
        patch.setattr(
            "aisc_salesforce.filesystem.os.open",
            lambda *args: calls.append(("open", args)),
        )
        patch.setattr(
            "aisc_salesforce.filesystem.os.fsync",
            lambda *args: calls.append(("fsync", args)),
        )
        patch.setattr(
            "aisc_salesforce.filesystem.os.close",
            lambda *args: calls.append(("close", args)),
        )

        sync_directory("staging")

    assert calls == []


def test_sync_directory_opens_syncs_and_closes_on_posix(monkeypatch):
    calls = []

    with monkeypatch.context() as patch:
        patch.setattr("aisc_salesforce.filesystem.os.name", "posix")
        patch.setattr(
            "aisc_salesforce.filesystem.os.open",
            lambda path, flags: calls.append(("open", path, flags)) or 42,
        )
        patch.setattr(
            "aisc_salesforce.filesystem.os.fsync",
            lambda descriptor: calls.append(("fsync", descriptor)),
        )
        patch.setattr(
            "aisc_salesforce.filesystem.os.close",
            lambda descriptor: calls.append(("close", descriptor)),
        )

        sync_directory("staging")

    assert calls == [
        ("open", "staging", os.O_RDONLY),
        ("fsync", 42),
        ("close", 42),
    ]


def test_sync_directory_propagates_posix_errors_and_still_closes(monkeypatch):
    calls = []

    def fail_sync(descriptor):
        calls.append(("fsync", descriptor))
        raise OSError("sync failed")

    with monkeypatch.context() as patch:
        patch.setattr("aisc_salesforce.filesystem.os.name", "posix")
        patch.setattr("aisc_salesforce.filesystem.os.open", lambda *args: 42)
        patch.setattr("aisc_salesforce.filesystem.os.fsync", fail_sync)
        patch.setattr(
            "aisc_salesforce.filesystem.os.close",
            lambda descriptor: calls.append(("close", descriptor)),
        )

        with pytest.raises(OSError, match="sync failed"):
            sync_directory("staging")

    assert calls == [("fsync", 42), ("close", 42)]
