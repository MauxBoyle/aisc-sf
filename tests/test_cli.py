from aisc_salesforce import app
from aisc_salesforce.dictionary import ExportField


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
