"""Command-line interface for Salesforce snapshots and daily automation."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

from .dictionary import DictionaryError, load_export_plan
from .imis_contacts import ContactConsolidationError, consolidate_contactbasic
from .process_profile_updates import (
    InteractiveProfileUpdateProcessor,
    ProcessingError,
    ProfileUpdateProcessingWorkflow,
)
from .profile_updates import ProfileUpdateService
from .rename_profile_update_cases import RenameProfileUpdateCasesService
from .salesforce import (
    SalesforceClient,
    SalesforceError,
    get_credentials,
    get_oauth_url,
    request_access_token,
)
from .snapshot import write_snapshot
from .stage_profile_updates import (
    ProfileUpdateStagingService,
    write_staged_profile_updates,
)


def main(
    argv: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Run the CLI and return a shell-friendly status code."""
    parser = argparse.ArgumentParser(
        description="Run AISC Salesforce data and workflow commands."
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
    subparsers.add_parser(
        "profile-updates",
        help="Process recent audits and New profile update submissions.",
    )
    stage_parser = subparsers.add_parser(
        "stage-profile-updates",
        help="Stage New profile update submissions in a read-only CSV snapshot.",
    )
    stage_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("staged_profile_updates"),
        help="Directory to contain staged profile update folders.",
    )
    process_parser = subparsers.add_parser(
        "process-profile-updates",
        help="Create/reuse Cases, stage submissions, and review changes interactively.",
    )
    process_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("staged_profile_updates"),
        help="Directory to contain staging, audit, and response artifacts.",
    )
    rename_parser = subparsers.add_parser(
        "rename-profile-update-cases",
        help="Preview corrections to recent legacy Profile Update Case subjects.",
    )
    rename_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the previewed Subject-only Case updates.",
    )
    contacts_parser = subparsers.add_parser(
        "consolidate-imis-contacts",
        help="Merge the newest dated iMIS CSContactBasic export.",
    )
    contacts_parser.add_argument(
        "--directory",
        type=Path,
        default=Path("imis_contactbasic"),
        help="Directory containing dated CSContactBasic CSV files.",
    )
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        try:
            return _run_snapshot(args.output_dir)
        except (DictionaryError, SalesforceError, OSError) as error:
            print(f"Snapshot failed: {error}", file=sys.stderr)
            return 1
    if args.command == "profile-updates":
        try:
            return _run_profile_updates()
        except (SalesforceError, OSError) as error:
            print(f"Profile updates failed: {error}", file=sys.stderr)
            return 1
    if args.command == "stage-profile-updates":
        try:
            return _run_stage_profile_updates(args.output_dir)
        except (SalesforceError, OSError) as error:
            print(f"Stage profile updates failed: {error}", file=sys.stderr)
            return 1
    if args.command == "process-profile-updates":
        try:
            return _run_process_profile_updates(
                args.output_dir,
                input_fn=input_fn,
                output_fn=output_fn,
            )
        except (ProcessingError, SalesforceError, OSError) as error:
            print(f"Process profile updates failed: {error}", file=sys.stderr)
            return 1
    if args.command == "rename-profile-update-cases":
        try:
            return _run_rename_profile_update_cases(
                apply=args.apply,
                output_fn=output_fn,
            )
        except (SalesforceError, OSError) as error:
            print(f"Rename profile update Cases failed: {error}", file=sys.stderr)
            return 1
    if args.command == "consolidate-imis-contacts":
        try:
            return _run_consolidate_imis_contacts(
                args.directory,
                output_fn=output_fn,
            )
        except ContactConsolidationError as error:
            print(f"iMIS contact consolidation failed: {error}", file=sys.stderr)
            return 1
    return 1


def _run_snapshot(output_dir: Path) -> int:
    _load_dotenv(Path(".env"))
    dictionary_path = (
        Path(__file__).with_name("data") / "salesforce_schema_dictionary.csv"
    )
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


def _run_profile_updates() -> int:
    """Connect to Salesforce and run the profile update service once."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    queue_id, responder_id = get_profile_update_configuration(environment)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    service = ProfileUpdateService(SalesforceClient(auth), queue_id, responder_id)
    counts = service.run()
    print("Profile updates complete:")
    print(f"created: {counts.created}")
    print(f"reused: {counts.reused}")
    print(f"skipped: {counts.skipped}")
    print(f"failed: {counts.failed}")
    for error in getattr(service, "errors", []):
        print(error, file=sys.stderr)
    return 1 if counts.failed else 0


def _run_stage_profile_updates(output_dir: Path) -> int:
    """Connect to Salesforce and publish one read-only staging snapshot."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    result = ProfileUpdateStagingService(SalesforceClient(auth)).stage()
    snapshot_path = write_staged_profile_updates(result.rows, output_dir)
    print(f"Staged profile updates complete: {snapshot_path / 'profile_updates.csv'}")
    print(f"staged rows: {len(result.rows)}")
    print(f"warnings: {result.warning_count}")
    return 0


def _run_process_profile_updates(
    output_dir: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Connect to Salesforce and run the interactive Profile Update workflow."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    queue_id, responder_id = get_profile_update_configuration(environment)
    output_fn("Authenticating with Salesforce...")
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    client = SalesforceClient(auth)
    output_fn("Salesforce authentication complete.")
    workflow = ProfileUpdateProcessingWorkflow(
        ProfileUpdateService(client, queue_id, responder_id),
        ProfileUpdateStagingService(client),
        InteractiveProfileUpdateProcessor(
            client,
            input_fn=input_fn,
            output_fn=output_fn,
        ),
        output_fn=output_fn,
    )
    result = workflow.run(output_dir)
    if result.stopped_early:
        output_fn("Review stopped early at your request.")
    else:
        output_fn("Interactive review complete.")
    output_fn(f"Processed Profile Updates from: {result.staging_path}")
    output_fn(f"Audit trail: {result.audit_path}")
    output_fn(f"Response emails: {result.response_path}")
    output_fn(f"completed Case batches: {result.completed_batches}")
    output_fn(f"pending Case batches: {result.pending_batches}")
    return 0


def _run_rename_profile_update_cases(
    *,
    apply: bool,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Connect to Salesforce and preview or apply recent subject corrections."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    counts = RenameProfileUpdateCasesService(
        SalesforceClient(auth), output_fn=output_fn
    ).run(apply=apply)
    output_fn("Rename profile update Cases complete:")
    output_fn(f"matched: {counts.matched}")
    label = "updated" if apply else "would update"
    value = counts.updated if apply else counts.would_update
    output_fn(f"{label}: {value}")
    output_fn(f"skipped: {counts.skipped}")
    output_fn(f"failed: {counts.failed}")
    return 1 if counts.failed else 0


def _run_consolidate_imis_contacts(
    directory: Path,
    *,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Consolidate local iMIS contact exports and print a short summary."""
    result = consolidate_contactbasic(directory, output_fn=output_fn)
    output_fn(f"Selected fresh export: {result.fresh_export}")
    if result.prior_combined is None:
        output_fn("Selected prior combined table: none (initial run)")
    else:
        output_fn(f"Selected prior combined table: {result.prior_combined}")
    output_fn(f"Published combined file: {result.combined_path}")
    if result.changed_path is not None and result.new_path is not None:
        output_fn(f"Published changed file: {result.changed_path}")
        output_fn(f"Published new file: {result.new_path}")
    output_fn(f"Combined contacts: {result.combined_count}")
    output_fn(f"Changed contacts: {result.changed_count}")
    output_fn(f"New contacts: {result.new_count}")
    return 0


def get_profile_update_configuration(
    environment: dict[str, str],
) -> tuple[str, str]:
    """Read the two Salesforce IDs required by profile update automation."""
    names = ("CERTIFICATION_QUEUE_ID", "PRIMARY_RESPONDER_ID")
    missing = [name for name in names if not environment.get(name, "").strip()]
    if missing:
        raise SalesforceError(
            "Missing Profile Update configuration: " + ", ".join(missing)
        )
    return tuple(environment[name].strip() for name in names)


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting real environment variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), _dotenv_value(value)
        if key:
            os.environ.setdefault(key, value)


def _dotenv_value(value: str) -> str:
    """Remove quotes and comments from one value read from a ``.env`` file."""
    value = value.strip()
    if not value:
        return ""
    if value[0] in {'"', "'"}:
        quote = value[0]
        for index, character in enumerate(value[1:], start=1):
            if character == quote and value[index - 1] != "\\":
                return value[1:index]
        return value[1:]
    for index, character in enumerate(value):
        if character == "#" and index > 0 and value[index - 1].isspace():
            return value[:index].rstrip()
    return value
