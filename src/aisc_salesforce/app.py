"""Command-line interface for manual Salesforce snapshots."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .dictionary import DictionaryError, load_export_plan
from .salesforce import (
    SalesforceClient,
    SalesforceError,
    get_credentials,
    get_oauth_url,
    request_access_token,
)
from .snapshot import write_snapshot


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a shell-friendly status code."""
    parser = argparse.ArgumentParser(
        description="Create a read-only Salesforce data snapshot."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser(
        "snapshot", help="Export selected Salesforce records."
    )
    snapshot_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("snapshots"),
        help="Directory to contain snapshot folders.",
    )
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        try:
            return _run_snapshot(args.output_dir)
        except (DictionaryError, SalesforceError, OSError) as error:
            print(f"Snapshot failed: {error}", file=sys.stderr)
            return 1
    return 1


def _run_snapshot(output_dir: Path) -> int:
    _load_dotenv(Path(".env"))
    dictionary_path = Path(__file__).with_name("data") / "salesforce_schema_dictionary.csv"
    plan = load_export_plan(dictionary_path)
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    client = SalesforceClient(auth)
    records = {
        object_name: client.query_all(object_name, fields)
        for object_name, fields in plan.items()
    }
    snapshot_path = write_snapshot(plan, records, output_dir)
    print(f"Snapshot complete: {snapshot_path}")
    for object_name, object_records in records.items():
        print(f"{object_name}: {len(object_records)} rows")
    return 0


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting real environment variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)
