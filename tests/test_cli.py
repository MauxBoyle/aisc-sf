from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace

import pytest

from aisc_salesforce import app
from aisc_salesforce.application_snapshot import (
    ApplicationSnapshotError,
    ApplicationSnapshotResult,
)
from aisc_salesforce.dictionary import ExportField
from aisc_salesforce.imis_contacts import ContactConsolidationError
from aisc_salesforce.picklist_audit import (
    PicklistAuditFinding,
    PicklistAuditResult,
)
from aisc_salesforce.profile_updates import AutomationCounts
from aisc_salesforce.rename_profile_update_cases import RenameCounts
from aisc_salesforce.user_reconciliation import (
    ReconciliationBlocker,
    UserReconciliationPlan,
)


def test_user_sync_config_cli_runs_the_read_only_check(monkeypatch, capsys):
    monkeypatch.setattr(app, "_run_check_user_sync_config", lambda: 0)

    assert app.main(["check-user-sync-config"]) == 0
    assert capsys.readouterr().err == ""


def test_participant_drop_cli_runs_interactive_workflow(monkeypatch):
    output = []
    received = {}

    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(
        app.os, "environ", {"SF_CLIENT_ID": "id", "SF_CLIENT_SECRET": "secret"}
    )
    monkeypatch.setattr(app, "get_credentials", lambda values: values)
    monkeypatch.setattr(app, "get_oauth_url", lambda values: "token-url")
    monkeypatch.setattr(
        app, "request_access_token", lambda credentials, oauth_url: "auth"
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: "client")

    class Service:
        def __init__(self, client):
            assert client == "client"

        def run(self, interaction):
            received["scenario"] = interaction.choose_scenario()
            received["reference"] = interaction.request_reference(received["scenario"])
            return SimpleNamespace(cancelled=False)

    monkeypatch.setattr(app, "ParticipantDropService", Service)

    answers = iter(["1", "INV-42"])
    assert app.main(
        ["participant-drop"], input_fn=lambda prompt: next(answers), output_fn=output.append
    ) == 0
    assert received["scenario"].value == "Unpaid Invoice"
    assert received["reference"] == "INV-42"


def test_user_sync_config_command_authenticates_and_validates(monkeypatch, capsys):
    environment = {
        "SF_CLIENT_ID": "id",
        "SF_CLIENT_SECRET": "secret",
        "PARTICIPANT_PROFILE_ID": "00e5w000000k7KfAAI",
        "PARTICIPANT_PRINCIPAL_PROFILE_ID": "00e5w000000kDqiAAE",
        "PARTICIPANT_AP_PROFILE_ID": "00e5w000000kDqdAAE",
        "PARTICIPANT_QC_PROFILE_ID": "00e5w000000kDqnAAE",
        "PARTICIPANT_RAS_PROFILE_ID": "00e5w000000kDqsAAE",
    }
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app.os, "environ", environment)
    monkeypatch.setattr(app, "get_credentials", lambda values: {"ok": "yes"})
    monkeypatch.setattr(app, "get_oauth_url", lambda values: "token-url")
    monkeypatch.setattr(
        app, "request_access_token", lambda credentials, oauth_url: "auth"
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: "client")

    class Validator:
        def __init__(self, client):
            assert client == "client"

        def validate(self, values):
            assert values == environment

    monkeypatch.setattr(app, "UserSyncConfigValidator", Validator)

    assert app.main(["check-user-sync-config"]) == 0
    assert capsys.readouterr().out.strip() == (
        "User sync configuration is valid; no Salesforce records were changed."
    )


def test_user_sync_config_cli_reports_expected_failures(monkeypatch, capsys):
    monkeypatch.setattr(
        app,
        "_run_check_user_sync_config",
        lambda: (_ for _ in ()).throw(app.SalesforceError("Profile query failed")),
    )

    assert app.main(["check-user-sync-config"]) == 1
    assert "User sync configuration check failed: Profile query failed" in (
        capsys.readouterr().err
    )


def test_reconcile_user_cli_supports_text_and_json(monkeypatch):
    output = []
    plan = UserReconciliationPlan(
        "contact-1", (), None, (), (), (), (), "none", None, (), ()
    )
    monkeypatch.setattr(
        app,
        "_run_reconcile_user",
        lambda contact_id, *, as_json, output_fn: (
            output_fn(plan.to_json() if as_json else "text plan"),
            0,
        )[1],
    )

    assert app.main(["reconcile-user", "contact-1"], output_fn=output.append) == 0
    assert output == ["text plan"]
    output.clear()
    assert (
        app.main(["reconcile-user", "contact-1", "--json"], output_fn=output.append)
        == 0
    )
    assert '"contact_id": "contact-1"' in output[0]


def test_reconcile_user_profile_configuration_blocker_returns_nonzero(monkeypatch):
    plan = UserReconciliationPlan(
        "contact-1",
        (),
        None,
        (),
        (),
        (),
        (),
        None,
        None,
        (),
        (ReconciliationBlocker("profile_configuration", "bad Profile"),),
    )
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(
        app.os, "environ", {"SF_CLIENT_ID": "id", "SF_CLIENT_SECRET": "secret"}
    )
    monkeypatch.setattr(app, "get_credentials", lambda values: values)
    monkeypatch.setattr(app, "get_oauth_url", lambda values: "token-url")
    monkeypatch.setattr(
        app, "request_access_token", lambda credentials, oauth_url: "auth"
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: "client")

    class Service:
        def __init__(self, client):
            pass

        def plan(self, contact_id, environment):
            return plan

    monkeypatch.setattr(app, "UserReconciliationService", Service)

    assert app.main(["reconcile-user", "contact-1"], output_fn=lambda line: None) == 1


def test_picklist_audit_cli_prints_grouped_informational_findings(monkeypatch):
    output = []
    result = PicklistAuditResult(
        cutoff=datetime(2024, 7, 24, tzinfo=UTC),
        findings=(
            PicklistAuditFinding("Case", "Status", ("Unexpected",), True),
            PicklistAuditFinding(
                "Case",
                "Uncataloged__c",
                ("Alpha", "Zebra"),
                False,
            ),
        ),
    )
    monkeypatch.setattr(
        app,
        "_run_audit_picklist_enums",
        lambda *, output_fn: (
            app._print_picklist_audit(result, output_fn=output_fn),
            0,
        )[1],
    )

    assert app.main(["audit-picklist-enums"], output_fn=output.append) == 0
    assert "Case:" in output
    assert "  Status:" in output
    assert "  Uncataloged__c [no enum catalog]:" in output
    assert "    - Alpha" in output
    assert "    - Zebra" in output
    assert any("informational" in line for line in output)


def test_picklist_audit_cli_prints_clear_success(monkeypatch):
    output = []
    result = PicklistAuditResult(
        cutoff=datetime(2024, 7, 24, tzinfo=UTC),
        findings=(),
    )
    monkeypatch.setattr(
        app,
        "_run_audit_picklist_enums",
        lambda *, output_fn: (
            app._print_picklist_audit(result, output_fn=output_fn),
            0,
        )[1],
    )

    assert app.main(["audit-picklist-enums"], output_fn=output.append) == 0
    assert output == [
        "Picklist enum audit complete: no missing values found in the audit "
        "window starting 2024-07-24T00:00:00Z."
    ]


def test_picklist_audit_cli_salesforce_failure_is_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        app,
        "_run_audit_picklist_enums",
        lambda **kwargs: (_ for _ in ()).throw(
            app.SalesforceError("describe Case failed")
        ),
    )

    assert app.main(["audit-picklist-enums"]) == 1
    assert "Picklist enum audit failed: describe Case failed" in capsys.readouterr().err


def test_application_snapshot_cli_uses_custom_output_and_prints_summary(
    monkeypatch, tmp_path
):
    output = []
    result = ApplicationSnapshotResult(
        rows=(),
        qualifying_case_count=3,
        unexpected_stages={"Surprise Stage": 2},
    )
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app, "get_credentials", lambda environment: {"ok": "yes"})
    monkeypatch.setattr(app, "get_oauth_url", lambda environment: "token-url")
    monkeypatch.setattr(
        app, "request_access_token", lambda credentials, oauth_url: "auth"
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: "client")

    class Service:
        def __init__(self, client):
            assert client == "client"

        def build(self):
            return result

    monkeypatch.setattr(app, "ApplicationSnapshotService", Service)
    monkeypatch.setattr(
        app,
        "write_application_snapshot",
        lambda report, output_dir: output_dir / "run",
    )

    assert (
        app.main(
            ["application-snapshot", "--output-dir", str(tmp_path)],
            output_fn=output.append,
        )
        == 0
    )
    assert output[0] == "Warning: unexpected application stages: Surprise Stage (2)"
    assert str(tmp_path / "run" / "application_snapshot.csv") in output[1]
    assert output[2] == "qualifying Cases: 3"


@pytest.mark.parametrize(
    "error",
    [
        ApplicationSnapshotError("bad date"),
        app.SalesforceError("unavailable"),
        OSError("disk full"),
    ],
)
def test_application_snapshot_cli_reports_expected_failures(monkeypatch, capsys, error):
    monkeypatch.setattr(
        app,
        "_run_application_snapshot",
        lambda output_dir, **kwargs: (_ for _ in ()).throw(error),
    )

    assert app.main(["application-snapshot"]) == 1
    assert f"Application snapshot failed: {error}" in capsys.readouterr().err


def test_cli_success_uses_custom_output_dir(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        app, "load_export_plan", lambda path: {"Account": [ExportField("Name", "name")]}
    )
    monkeypatch.setattr(app, "get_credentials", lambda env: {"ok": "yes"})
    monkeypatch.setattr(app, "get_oauth_url", lambda env: "https://example/token")
    monkeypatch.setattr(
        app, "request_access_token", lambda credentials, oauth_url: object()
    )

    class Client:
        def __init__(self, auth):
            pass

        def query_all(self, object_name, fields):
            return [{"Name": "Acme"}]

    monkeypatch.setattr(app, "SalesforceClient", Client)
    monkeypatch.setattr(
        app,
        "write_snapshot",
        lambda plan, records, destination: destination / "finished",
    )

    assert app.main(["snapshot", "--output-dir", str(tmp_path)]) == 0
    assert "Snapshot complete" in capsys.readouterr().out


def test_cli_configuration_failure_is_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(app, "load_export_plan", lambda path: {"Account": []})
    monkeypatch.setattr(
        app,
        "get_credentials",
        lambda env: (_ for _ in ()).throw(
            app.SalesforceError("Missing Salesforce configuration: SF_CLIENT_SECRET")
        ),
    )
    assert app.main(["snapshot"]) == 1
    assert "SF_CLIENT_SECRET" in capsys.readouterr().err


def test_consolidate_contacts_cli_uses_default_directory_and_prints_summary(
    monkeypatch,
):
    output = []
    expected = SimpleNamespace(
        fresh_export=app.Path("imis_contactbasic/Full_CSContactBasic_260719.csv"),
        prior_combined=None,
        combined_path=app.Path(
            "imis_contactbasic/Combined_CSContactBasic_20260719.csv"
        ),
        changed_path=None,
        new_path=None,
        combined_count=4,
        changed_count=0,
        new_count=0,
    )

    def consolidate(directory, *, output_fn):
        assert directory == app.Path("imis_contactbasic")
        output_fn("example warning")
        return expected

    monkeypatch.setattr(app, "consolidate_contactbasic", consolidate)

    assert app.main(["consolidate-imis-contacts"], output_fn=output.append) == 0
    assert "example warning" in output
    assert any("Selected fresh export:" in line for line in output)
    assert any("Combined contacts: 4" in line for line in output)


def test_consolidate_contacts_cli_uses_custom_directory(monkeypatch, tmp_path):
    calls = []

    def run(directory, *, output_fn):
        calls.append((directory, output_fn))
        return 0

    monkeypatch.setattr(app, "_run_consolidate_imis_contacts", run)

    assert (
        app.main(
            ["consolidate-imis-contacts", "--directory", str(tmp_path)],
            output_fn=lambda message: None,
        )
        == 0
    )
    assert calls[0][0] == tmp_path


def test_consolidate_contacts_cli_reports_validation_error(monkeypatch, capsys):
    monkeypatch.setattr(
        app,
        "_run_consolidate_imis_contacts",
        lambda directory, **kwargs: (_ for _ in ()).throw(
            ContactConsolidationError("No Full_CSContactBasic export was found")
        ),
    )

    assert app.main(["consolidate-imis-contacts"]) == 1
    assert (
        "iMIS contact consolidation failed: No Full_CSContactBasic export was found"
        in capsys.readouterr().err
    )


def test_dotenv_removes_inline_comments_but_preserves_hashes(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "QUEUE_ID = 00G123  # Queue ID\n"
        "SECRET=abc#part\n"
        'LOGIN_URL="https://example.com/#fragment" # Login URL\n',
        encoding="utf-8",
    )
    for name in ("QUEUE_ID", "SECRET", "LOGIN_URL"):
        monkeypatch.delenv(name, raising=False)

    app._load_dotenv(dotenv)

    assert app.os.environ["QUEUE_ID"] == "00G123"
    assert app.os.environ["SECRET"] == "abc#part"
    assert app.os.environ["LOGIN_URL"] == "https://example.com/#fragment"


def test_profile_updates_cli_prints_counts_and_succeeds(monkeypatch, capsys):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app, "get_credentials", lambda env: {"ok": "yes"})
    monkeypatch.setattr(app, "get_oauth_url", lambda env: "https://example/token")
    monkeypatch.setattr(
        app, "request_access_token", lambda credentials, oauth_url: object()
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: object())
    monkeypatch.setenv("CERTIFICATION_QUEUE_ID", "queue")
    monkeypatch.setenv("PRIMARY_RESPONDER_ID", "responder")

    class Service:
        def __init__(self, client, queue_id, responder_id):
            assert queue_id == "queue"
            assert responder_id == "responder"

        def run(self):
            return AutomationCounts(created=2, reused=3, skipped=4)

    monkeypatch.setattr(app, "ProfileUpdateService", Service)

    assert app.main(["profile-updates"]) == 0
    output = capsys.readouterr().out
    assert "created: 2" in output
    assert "reused: 3" in output
    assert "skipped: 4" in output
    assert "failed: 0" in output


def test_profile_updates_cli_requires_both_ids(monkeypatch, capsys):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.delenv("CERTIFICATION_QUEUE_ID", raising=False)
    monkeypatch.delenv("PRIMARY_RESPONDER_ID", raising=False)

    assert app.main(["profile-updates"]) == 1
    error = capsys.readouterr().err
    assert "CERTIFICATION_QUEUE_ID" in error
    assert "PRIMARY_RESPONDER_ID" in error


def test_profile_updates_cli_is_nonzero_when_records_fail(monkeypatch, capsys):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app, "get_credentials", lambda env: {"ok": "yes"})
    monkeypatch.setattr(app, "get_oauth_url", lambda env: "https://example/token")
    monkeypatch.setattr(
        app, "request_access_token", lambda credentials, oauth_url: object()
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: object())
    monkeypatch.setenv("CERTIFICATION_QUEUE_ID", "queue")
    monkeypatch.setenv("PRIMARY_RESPONDER_ID", "responder")

    class Service:
        errors = ["audit-1: feed unavailable"]

        def __init__(self, client, queue_id, responder_id):
            pass

        def run(self):
            return AutomationCounts(failed=1)

    monkeypatch.setattr(app, "ProfileUpdateService", Service)

    assert app.main(["profile-updates"]) == 1
    assert "audit-1: feed unavailable" in capsys.readouterr().err


def test_stage_profile_updates_cli_uses_custom_output_and_prints_counts(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app, "get_credentials", lambda env: {"ok": "yes"})
    monkeypatch.setattr(app, "get_oauth_url", lambda env: "https://example/token")
    monkeypatch.setattr(
        app, "request_access_token", lambda credentials, oauth_url: object()
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: object())

    class Result:
        rows = [{"has_warnings": "true"}, {"has_warnings": "false"}]
        warning_count = 1

    class Service:
        def __init__(self, client):
            pass

        def stage(self):
            return Result()

    monkeypatch.setattr(app, "ProfileUpdateStagingService", Service)
    monkeypatch.setattr(
        app,
        "write_staged_profile_updates",
        lambda rows, output_dir: output_dir / "finished",
    )

    assert app.main(["stage-profile-updates", "--output-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert str(tmp_path / "finished" / "profile_updates.csv") in output
    assert "staged rows: 2" in output
    assert "warnings: 1" in output


def test_stage_profile_updates_cli_reports_salesforce_failure(monkeypatch, capsys):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(
        app,
        "get_credentials",
        lambda env: (_ for _ in ()).throw(
            app.SalesforceError("Salesforce unavailable")
        ),
    )

    assert app.main(["stage-profile-updates"]) == 1
    assert (
        "Stage profile updates failed: Salesforce unavailable"
        in capsys.readouterr().err
    )


def test_stage_profile_updates_cli_reports_file_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        app,
        "_run_stage_profile_updates",
        lambda output_dir: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert app.main(["stage-profile-updates"]) == 1
    assert "Stage profile updates failed: disk full" in capsys.readouterr().err


def test_process_profile_updates_cli_injects_interactive_io_and_output_dir(
    monkeypatch, tmp_path
):
    prompts = []
    output = []
    calls = []

    def input_fn(prompt):
        prompts.append(prompt)
        return "will not be made"

    def run(output_dir, *, input_fn, output_fn):
        calls.append((output_dir, input_fn, output_fn))
        output_fn("Processing complete")
        return 0

    monkeypatch.setattr(app, "_run_process_profile_updates", run)

    assert (
        app.main(
            ["process-profile-updates", "--output-dir", str(tmp_path)],
            input_fn=input_fn,
            output_fn=output.append,
        )
        == 0
    )
    assert calls == [(tmp_path, input_fn, output.append)]
    assert output == ["Processing complete"]
    assert prompts == []


def test_process_profile_update_nested_operations_route_session_and_output_dir(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        app,
        "_run_stage_profile_update_session",
        lambda output_dir, **kwargs: calls.append(("stage", None, output_dir)) or 0,
    )
    monkeypatch.setattr(
        app,
        "_run_prepare_profile_update_session",
        lambda session_id, output_dir, **kwargs: (
            calls.append(("prepare", session_id, output_dir)) or 0
        ),
    )
    monkeypatch.setattr(
        app,
        "_run_review_profile_update_session",
        lambda session_id, output_dir, **kwargs: (
            calls.append(("review", session_id, output_dir)) or 0
        ),
    )
    session_id = "2026-08-04T15-30-00Z"

    assert (
        app.main(["process-profile-updates", "stage", "--output-dir", str(tmp_path)])
        == 0
    )
    assert (
        app.main(
            [
                "process-profile-updates",
                "prepare",
                session_id,
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert (
        app.main(
            [
                "process-profile-updates",
                "review",
                session_id,
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert calls == [
        ("stage", None, tmp_path),
        ("prepare", session_id, tmp_path),
        ("review", session_id, tmp_path),
    ]


def test_process_profile_update_nested_operation_preserves_parent_output_dir(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        app,
        "_run_stage_profile_update_session",
        lambda output_dir, **kwargs: calls.append(output_dir) or 0,
    )

    assert (
        app.main(["process-profile-updates", "--output-dir", str(tmp_path), "stage"])
        == 0
    )
    assert calls == [tmp_path]


@pytest.mark.parametrize("operation", ["stage", "prepare", "review"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_process_profile_update_no_color_works_around_nested_operations(
    monkeypatch, operation, position
):
    calls = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return 0

    monkeypatch.setattr(app, f"_run_{operation}_profile_update_session", record)
    session = [] if operation == "stage" else ["2026-08-04T15-30-00Z"]
    argv = ["process-profile-updates"]
    if position == "before":
        argv.append("--no-color")
    argv.extend([operation, *session])
    if position == "after":
        argv.append("--no-color")

    assert app.main(argv) == 0
    assert calls[0][1]["color_mode"] == app.ColorMode.NEVER


def test_process_profile_updates_reports_authentication_and_safe_stop(
    monkeypatch, tmp_path
):
    output = []
    captured = {}
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(
        app,
        "get_profile_update_configuration",
        lambda environment: ("queue", "responder"),
    )
    monkeypatch.setattr(app, "get_credentials", lambda environment: {"ok": "yes"})
    monkeypatch.setattr(app, "get_oauth_url", lambda environment: "token-url")
    monkeypatch.setattr(
        app,
        "request_access_token",
        lambda credentials, oauth_url: "auth",
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: "client")
    monkeypatch.setattr(app, "ProfileUpdateService", lambda *args: "cases")
    monkeypatch.setattr(app, "ProfileUpdateStagingService", lambda client: "staging")

    class Processor:
        def __init__(self, client, *, input_fn, output_fn):
            captured["processor_output"] = output_fn

    class Workflow:
        def __init__(
            self,
            case_service,
            staging_service,
            processor,
            *,
            output_fn,
        ):
            captured["workflow_output"] = output_fn

        def run(self, output_dir):
            return SimpleNamespace(
                staging_path=output_dir / "run",
                audit_path=output_dir / "run" / "review_audit.jsonl",
                response_path=output_dir / "run" / "response_emails.txt",
                completed_batches=1,
                pending_batches=1,
                stopped_early=True,
            )

    monkeypatch.setattr(app, "InteractiveProfileUpdateProcessor", Processor)
    monkeypatch.setattr(app, "ProfileUpdateProcessingWorkflow", Workflow)

    result = app._run_process_profile_updates(
        tmp_path,
        input_fn=lambda prompt: "",
        output_fn=output.append,
    )

    assert result == 0
    assert captured["processor_output"] == output.append
    assert captured["workflow_output"] == output.append
    assert "Authenticating with Salesforce" in output[0]
    assert "Salesforce authentication complete" in output[1]
    assert "Review stopped early at your request." in output


def test_process_profile_updates_cli_reports_processing_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        app,
        "_run_process_profile_updates",
        lambda output_dir, **kwargs: (_ for _ in ()).throw(
            app.ProcessingError("manual verification failed")
        ),
    )

    assert app.main(["process-profile-updates"]) == 1
    assert (
        "Process profile updates failed: manual verification failed"
        in capsys.readouterr().err
    )


def test_process_profile_update_failure_is_red_on_a_supported_terminal(monkeypatch):
    class TerminalBuffer(StringIO):
        def isatty(self):
            return True

    stream = TerminalBuffer()
    monkeypatch.setattr("sys.stderr", stream)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    app.print_profile_error("Process profile updates failed: example")

    assert "\x1b[91mProcess profile updates failed: example\x1b[0m" in stream.getvalue()


def test_rename_cli_previews_by_default_and_apply_is_explicit(monkeypatch):
    calls = []

    def run(*, apply, output_fn):
        calls.append((apply, output_fn))
        return 0

    monkeypatch.setattr(app, "_run_rename_profile_update_cases", run)

    assert app.main(["rename-profile-update-cases"]) == 0
    assert app.main(["rename-profile-update-cases", "--apply"]) == 0
    assert [apply for apply, _ in calls] == [False, True]


def test_rename_cli_prints_totals_and_fails_only_for_update_failures(
    monkeypatch, capsys
):
    monkeypatch.setattr(app, "_load_dotenv", lambda path: None)
    monkeypatch.setattr(app, "get_credentials", lambda environment: {"ok": "yes"})
    monkeypatch.setattr(app, "get_oauth_url", lambda environment: "token-url")
    monkeypatch.setattr(
        app,
        "request_access_token",
        lambda credentials, oauth_url: "auth",
    )
    monkeypatch.setattr(app, "SalesforceClient", lambda auth: "client")

    class Service:
        def __init__(self, client, *, output_fn):
            pass

        def run(self, *, apply):
            assert apply is True
            return RenameCounts(matched=3, updated=1, skipped=1, failed=1)

    monkeypatch.setattr(app, "RenameProfileUpdateCasesService", Service)

    assert app._run_rename_profile_update_cases(apply=True) == 1
    output = capsys.readouterr().out
    assert "matched: 3" in output
    assert "updated: 1" in output
    assert "skipped: 1" in output
    assert "failed: 1" in output
