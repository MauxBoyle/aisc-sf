from types import SimpleNamespace

from aisc_salesforce import app
from aisc_salesforce.dictionary import ExportField
from aisc_salesforce.profile_updates import AutomationCounts


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
