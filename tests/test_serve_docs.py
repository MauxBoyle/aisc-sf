"""Tests for the documentation preview script."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def serve_docs_module() -> ModuleType:
    """Load the standalone documentation preview script."""
    path = Path(__file__).parents[1] / "scripts" / "serve_docs.py"
    spec = importlib.util.spec_from_file_location("serve_docs", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_mirrors_mkdocs_output_to_terminal_and_log(
    serve_docs_module, monkeypatch, capsys
):
    """MkDocs output is written to both the terminal and the log file."""

    class Log:
        def __init__(self):
            self.written = []
            self.flush_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def write(self, line):
            self.written.append(line)

        def flush(self):
            self.flush_count += 1

    log = Log()
    opened = []

    def open_log(path, mode):
        opened.append((path, mode))
        return log

    process = SimpleNamespace(stdout=["Serving docs\n", "Watching files\n"])
    monkeypatch.setattr(
        serve_docs_module.subprocess, "Popen", lambda *args, **kwargs: process
    )
    monkeypatch.setattr(serve_docs_module.Path, "open", open_log)

    serve_docs_module.main()

    assert capsys.readouterr().out == "Serving docs\nWatching files\n"
    assert log.written == ["Serving docs\n", "Watching files\n"]
    assert log.flush_count == 2
    assert opened == [(Path("mkdocs.log"), "w")]


def test_main_has_return_annotation_and_docstring(serve_docs_module):
    """The public entry point follows the project's typing conventions."""
    assert serve_docs_module.main.__annotations__ == {"return": None}
    assert serve_docs_module.main.__doc__ == (
        "Serve MkDocs and mirror its output to the terminal and log file."
    )
