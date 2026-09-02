"""Command-line interface for Salesforce snapshots and daily automation."""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from collections.abc import Callable
from pathlib import Path

from .application_snapshot import (
    ApplicationSnapshotError,
    ApplicationSnapshotService,
    write_application_snapshot,
)
from .cli_participant_drop import CLIParticipantDropInteraction
from .cli_review_ui import CLIReviewUI, ColorMode, print_profile_error
from .dictionary import DictionaryError, load_export_plan
from .imis_contacts import ContactConsolidationError, consolidate_contactbasic
from .participant_drop import ParticipantDropAction, ParticipantDropService
from .picklist_audit import (
    PicklistAuditError,
    PicklistAuditResult,
    PicklistEnumAuditService,
)
from .process_profile_updates import (
    InteractiveProfileUpdateProcessor,
    ProcessingError,
    ProfileUpdateProcessingWorkflow,
    publish_staging_session,
)
from .profile_updates import ProfileUpdateService
from .queried_fields import FieldInventoryError, build_queried_field_inventory
from .rename_profile_update_cases import RenameProfileUpdateCasesService
from .review_ui import UnsupportedReviewInteractionError
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
from .user_reconciliation import (
    ReconciliationPlanError,
    UserReconciliationService,
    load_user_reconciliation_plan,
    render_user_reconciliation_plan,
)
from .user_sync_config import UserSyncConfigError, UserSyncConfigValidator


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
    application_parser = subparsers.add_parser(
        "application-snapshot",
        help="Create a read-only CSV count of qualifying certification applications.",
    )
    application_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("application_snapshots"),
        help="Directory to contain application snapshot folders.",
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
        help="Stage a session, prepare Cases, and review changes interactively.",
    )
    process_parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("staged_profile_updates"),
        help="Directory to contain staging, audit, and response artifacts.",
    )
    process_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored terminal output.",
    )
    process_operations = process_parser.add_subparsers(dest="process_operation")
    for operation in ("stage", "prepare", "review"):
        operation_parser = process_operations.add_parser(
            operation,
            help=f"Run only the {operation} phase of a staging session.",
        )
        operation_parser.add_argument(
            "--no-color",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Disable colored terminal output.",
        )
        if operation != "stage":
            operation_parser.add_argument(
                "session_id",
                help="Exact staging-session ID printed by the stage command.",
            )
        operation_parser.add_argument(
            "--output-dir",
            type=Path,
            default=argparse.SUPPRESS,
            help="Directory containing Profile Update staging sessions.",
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
    subparsers.add_parser(
        "audit-picklist-enums",
        help="Report recently stored picklist values missing from Python enums.",
    )
    subparsers.add_parser(
        "check-user-sync-config",
        help="Validate configured participant Profiles without changing Salesforce.",
    )
    reconcile_user_parser = subparsers.add_parser(
        "reconcile-user",
        help="Build a read-only participant User reconciliation plan for one Contact.",
    )
    reconcile_user_parser.add_argument("contact_id", help="Salesforce Contact ID.")
    reconcile_user_parser.add_argument(
        "--json", action="store_true", help="Print the stable JSON plan contract."
    )
    reconcile_user_parser.add_argument(
        "--plan", type=Path, help="Reviewed JSON plan file to verify before applying."
    )
    reconcile_user_parser.add_argument(
        "--apply", action="store_true", help="Apply a still-current reviewed update plan."
    )
    subparsers.add_parser(
        "participant-drop",
        help="Interactively record that a participant withdrawal is in progress.",
    )
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        try:
            return _run_snapshot(args.output_dir)
        except (DictionaryError, SalesforceError, OSError) as error:
            print(f"Snapshot failed: {error}", file=sys.stderr)
            return 1
    if args.command == "application-snapshot":
        try:
            return _run_application_snapshot(
                args.output_dir,
                output_fn=output_fn,
            )
        except (ApplicationSnapshotError, SalesforceError, OSError) as error:
            print(f"Application snapshot failed: {error}", file=sys.stderr)
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
        color_mode = ColorMode.NEVER if args.no_color else ColorMode.AUTO
        color_kwargs = (
            {"color_mode": color_mode} if color_mode is ColorMode.NEVER else {}
        )
        try:
            if args.process_operation == "stage":
                return _run_stage_profile_update_session(
                    args.output_dir,
                    output_fn=output_fn,
                    **color_kwargs,
                )
            if args.process_operation == "prepare":
                return _run_prepare_profile_update_session(
                    args.session_id,
                    args.output_dir,
                    output_fn=output_fn,
                    **color_kwargs,
                )
            if args.process_operation == "review":
                return _run_review_profile_update_session(
                    args.session_id,
                    args.output_dir,
                    input_fn=input_fn,
                    output_fn=output_fn,
                    **color_kwargs,
                )
            return _run_process_profile_updates(
                args.output_dir,
                input_fn=input_fn,
                output_fn=output_fn,
                **color_kwargs,
            )
        except (
            ProcessingError,
            SalesforceError,
            UnsupportedReviewInteractionError,
            OSError,
        ) as error:
            print_profile_error(
                f"Process profile updates failed: {error}",
                color_mode=color_mode,
            )
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
    if args.command == "audit-picklist-enums":
        try:
            return _run_audit_picklist_enums(output_fn=output_fn)
        except (
            DictionaryError,
            FieldInventoryError,
            PicklistAuditError,
            SalesforceError,
            OSError,
        ) as error:
            print(f"Picklist enum audit failed: {error}", file=sys.stderr)
            return 1
    if args.command == "check-user-sync-config":
        try:
            return _run_check_user_sync_config()
        except (UserSyncConfigError, SalesforceError) as error:
            print(f"User sync configuration check failed: {error}", file=sys.stderr)
            return 1
    if args.command == "reconcile-user":
        if args.plan is not None and not args.apply:
            print("User reconciliation apply requires --apply; no Salesforce records were changed.", file=sys.stderr)
            return 1
        if args.apply and args.plan is None:
            print("User reconciliation apply requires --plan PATH; no Salesforce records were changed.", file=sys.stderr)
            return 1
        try:
            if args.apply:
                return _run_apply_reconcile_user(
                    args.contact_id, args.plan, as_json=args.json, output_fn=output_fn
                )
            return _run_reconcile_user(
                args.contact_id, as_json=args.json, output_fn=output_fn
            )
        except (SalesforceError, UserSyncConfigError, ReconciliationPlanError, OSError) as error:
            print(f"User reconciliation failed: {error}", file=sys.stderr)
            return 1
    if args.command == "participant-drop":
        try:
            return _run_participant_drop(input_fn=input_fn, output_fn=output_fn)
        except (SalesforceError, ValueError) as error:
            print(f"Participant drop failed: {error}", file=sys.stderr)
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


def _run_audit_picklist_enums(*, output_fn: Callable[[str], None] = print) -> int:
    """Connect to Salesforce and run the read-only picklist enum audit."""
    _load_dotenv(Path(".env"))
    dictionary_path = (
        Path(__file__).with_name("data") / "salesforce_schema_dictionary.csv"
    )
    export_plan = load_export_plan(dictionary_path)
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    result = PicklistEnumAuditService(SalesforceClient(auth)).audit(
        build_queried_field_inventory(export_plan)
    )
    _print_picklist_audit(result, output_fn=output_fn)
    return 0


def _run_check_user_sync_config() -> int:
    """Validate the configured participant Profiles with read-only Salesforce calls."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    UserSyncConfigValidator(SalesforceClient(auth)).validate(environment)
    print("User sync configuration is valid; no Salesforce records were changed.")
    return 0


def _run_reconcile_user(
    contact_id: str,
    *,
    as_json: bool,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Authenticate and print a User proposal using Salesforce reads only."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    plan = UserReconciliationService(SalesforceClient(auth)).plan(
        contact_id, environment
    )
    output_fn(plan.to_json() if as_json else render_user_reconciliation_plan(plan))
    return (
        1 if any(item.code == "profile_configuration" for item in plan.blockers) else 0
    )


def _run_apply_reconcile_user(
    contact_id: str,
    plan_path: Path,
    *,
    as_json: bool,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Apply one reviewed plan only after a fresh Salesforce safety check."""
    reviewed = load_user_reconciliation_plan(plan_path)
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    result = UserReconciliationService(SalesforceClient(auth)).apply(
        reviewed, contact_id, environment
    )
    if as_json:
        output_fn(result.to_json())
    else:
        output_fn(f"Updated User {result.user_id} for Contact {result.contact_id}.")
        for item in result.fields:
            output_fn(f"  - {item['field']}: {item['status']}")
        for event in result.events:
            output_fn(
                "login_identity_changed: "
                f"User {event['user_id']} changed {', '.join(event['fields'])}."
            )
    return 0


def _run_participant_drop(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Choose an action, then authenticate only when starting a withdrawal."""
    interaction = CLIParticipantDropInteraction(
        input_fn=input_fn, output_fn=output_fn
    )
    action = interaction.choose_action()
    if action is None:
        interaction.show(
            "Participant drop cancelled; no Salesforce changes were made."
        )
        return 0
    if action is ParticipantDropAction.COMPLETE:
        interaction.show("Complete an existing withdrawal is not implemented yet.")
        return 0

    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    ParticipantDropService(SalesforceClient(auth)).run(interaction)
    return 0


def _print_picklist_audit(
    result: PicklistAuditResult,
    *,
    output_fn: Callable[[str], None] = print,
) -> None:
    """Print stable, grouped audit findings without creating a report file."""
    cutoff = result.cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not result.findings:
        output_fn(
            "Picklist enum audit complete: no missing values found "
            f"in the audit window starting {cutoff}."
        )
        return

    output_fn(f"Picklist enum audit (audit window starts {cutoff}):")
    current_object = ""
    for finding in result.findings:
        if finding.object_name != current_object:
            current_object = finding.object_name
            output_fn(f"{current_object}:")
        marker = "" if finding.has_enum else " [no enum catalog]"
        output_fn(f"  {finding.field_name}{marker}:")
        for value in finding.values:
            output_fn(f"    - {value}")
    output_fn(
        "Audit complete. Missing and uncataloged values are informational; "
        "no Salesforce data was changed."
    )


def _run_application_snapshot(
    output_dir: Path,
    *,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Connect to Salesforce and publish one application snapshot."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    result = ApplicationSnapshotService(SalesforceClient(auth)).build()
    snapshot_path = write_application_snapshot(result, output_dir)
    if result.unexpected_stages:
        details = ", ".join(
            f"{stage} ({count})" for stage, count in result.unexpected_stages.items()
        )
        output_fn(f"Warning: unexpected application stages: {details}")
    output_fn(
        f"Application snapshot complete: {snapshot_path / 'application_snapshot.csv'}"
    )
    output_fn(f"qualifying Cases: {result.qualifying_case_count}")
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
    color_mode: ColorMode | str | bool | None = None,
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
    processor_parameters = inspect.signature(
        InteractiveProfileUpdateProcessor
    ).parameters
    if "ui" in processor_parameters:
        processor = InteractiveProfileUpdateProcessor(
            client,
            CLIReviewUI(
                input_fn=input_fn,
                output_fn=output_fn,
                color_mode=color_mode,
            ),
        )
    else:  # Compatibility for injected processors using the former constructor.
        processor = InteractiveProfileUpdateProcessor(
            client,
            input_fn=input_fn,
            output_fn=output_fn,
        )
    workflow = ProfileUpdateProcessingWorkflow(
        ProfileUpdateService(client, queue_id, responder_id),
        ProfileUpdateStagingService(client),
        processor,
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
    queue_path = getattr(result, "queue_path", None)
    if queue_path is not None:
        output_fn(f"Review queue: {queue_path}")
    output_fn(f"completed Case batches: {result.completed_batches}")
    output_fn(f"pending Case batches: {result.pending_batches}")
    return 0


def _run_stage_profile_update_session(
    output_dir: Path,
    *,
    output_fn: Callable[[str], None] = print,
    color_mode: ColorMode | str | bool | None = None,
) -> int:
    """Authenticate, capture New submissions, and publish one stable session."""
    client = _profile_update_client(output_fn)
    staged = ProfileUpdateStagingService(client).stage()
    session = publish_staging_session(
        staged.rows,
        output_dir,
        warning_count=staged.warning_count,
    )
    output_fn(f"Staging session published: {session.session_id}")
    output_fn(f"Staging CSV: {session.csv_path}")
    output_fn(f"Review queue: {session.queue_path}")
    output_fn(f"staged rows: {session.row_count}")
    output_fn(f"warnings: {session.warning_count}")
    return 0


def _run_prepare_profile_update_session(
    session_id: str,
    output_dir: Path,
    *,
    output_fn: Callable[[str], None] = print,
    color_mode: ColorMode | str | bool | None = None,
) -> int:
    """Authenticate and prepare Cases for one published session."""
    client, queue_id, responder_id = _profile_update_client_with_configuration(
        output_fn
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        CLIReviewUI(
            input_fn=input,
            output_fn=output_fn,
            color_mode=color_mode,
        ),
    )
    workflow = ProfileUpdateProcessingWorkflow(
        ProfileUpdateService(client, queue_id, responder_id),
        ProfileUpdateStagingService(client),
        processor,
        output_fn=output_fn,
    )
    workflow.prepare(session_id, output_dir)
    output_fn(f"Prepared staging session: {session_id}")
    return 0


def _run_review_profile_update_session(
    session_id: str,
    output_dir: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    color_mode: ColorMode | str | bool | None = None,
) -> int:
    """Authenticate and resume interactive review for one published session."""
    client, queue_id, responder_id = _profile_update_client_with_configuration(
        output_fn
    )
    processor = InteractiveProfileUpdateProcessor(
        client,
        CLIReviewUI(
            input_fn=input_fn,
            output_fn=output_fn,
            color_mode=color_mode,
        ),
    )
    workflow = ProfileUpdateProcessingWorkflow(
        ProfileUpdateService(client, queue_id, responder_id),
        ProfileUpdateStagingService(client),
        processor,
        output_fn=output_fn,
    )
    result = workflow.review(session_id, output_dir)
    if result.stopped_early:
        output_fn("Review stopped early at your request.")
    else:
        output_fn("Interactive review complete.")
    output_fn(f"Reviewed staging session: {session_id}")
    output_fn(f"Audit trail: {result.audit_path}")
    output_fn(f"Response emails: {result.response_path}")
    output_fn(f"Review queue: {result.queue_path}")
    output_fn(f"completed Case batches: {result.completed_batches}")
    output_fn(f"pending Case batches: {result.pending_batches}")
    return 0


def _profile_update_client(output_fn: Callable[[str], None]) -> SalesforceClient:
    """Load configuration and authenticate one Profile Update operation."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    output_fn("Authenticating with Salesforce...")
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    client = SalesforceClient(auth)
    output_fn("Salesforce authentication complete.")
    return client


def _profile_update_client_with_configuration(
    output_fn: Callable[[str], None],
) -> tuple[SalesforceClient, str, str]:
    """Authenticate and return the Case configuration used by write phases."""
    _load_dotenv(Path(".env"))
    environment = dict(os.environ)
    queue_id, responder_id = get_profile_update_configuration(environment)
    output_fn("Authenticating with Salesforce...")
    credentials = get_credentials(environment)
    auth = request_access_token(credentials, oauth_url=get_oauth_url(environment))
    client = SalesforceClient(auth)
    output_fn("Salesforce authentication complete.")
    return client, queue_id, responder_id


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
