from __future__ import annotations

import runpy
import sys
from types import SimpleNamespace

import pytest

from sovereignflow import import_cli
from sovereignflow.domain import (
    DatasetConsistencyReport,
    DatasetImportRun,
    DatasetImportStatus,
    ValidationError,
)


def run() -> DatasetImportRun:
    return DatasetImportRun(
        "import-1",
        "general",
        "tenant-a",
        "a" * 64,
        DatasetImportStatus.COMPLETED,
        2,
        3,
        1,
        1,
        2,
        1,
        1,
    )


class Service:
    def __init__(self, *, consistent=True) -> None:
        self.calls = []
        self.consistent = consistent

    def execute(self, reader):
        self.calls.append(("execute", reader))
        return run()

    def status(self, import_id):
        self.calls.append(("status", import_id))
        return run()

    def consistency(self):
        self.calls.append(("consistency",))
        return DatasetConsistencyReport(
            "general",
            "tenant-a",
            2,
            3,
            3 if self.consistent else 2,
            1,
        )


class Application:
    def __init__(self, service) -> None:
        self.service = service
        self.closed = 0

    def close(self):
        self.closed += 1


def common(command):
    return [command, "--config", "config.yaml", "--domain", "general"]


def test_import_cli_executes_import_status_and_verify(monkeypatch, capsys) -> None:
    service = Service()
    applications = []

    def bootstrap(settings, *, domain_name):
        application = Application(service)
        applications.append(application)
        return application

    monkeypatch.setattr(import_cli, "load_settings", lambda path: object())
    monkeypatch.setattr(import_cli, "bootstrap_import", bootstrap)
    monkeypatch.setattr(import_cli, "JsonlDatasetReader", lambda **kwargs: kwargs)

    assert (
        import_cli.main(
            [
                *common("import"),
                "--nodes",
                "nodes.jsonl",
                "--edges",
                "edges.jsonl",
                "--operations",
                "operations.jsonl",
                "--workspace",
                "workspace.sqlite",
                "--import-id",
                "import-1",
                "--relationship-scope",
                "complete",
            ]
        )
        == 0
    )
    assert '"status": "completed"' in capsys.readouterr().out

    assert import_cli.main([*common("status"), "--import-id", "import-1"]) == 0
    assert '"import_id": "import-1"' in capsys.readouterr().out

    assert import_cli.main(common("verify")) == 0
    assert '"consistent": true' in capsys.readouterr().out
    assert all(application.closed == 1 for application in applications)


def test_import_cli_returns_inconsistent_and_controlled_error_codes(
    monkeypatch,
    capsys,
) -> None:
    inconsistent = Application(Service(consistent=False))
    monkeypatch.setattr(import_cli, "load_settings", lambda path: object())
    monkeypatch.setattr(
        import_cli,
        "bootstrap_import",
        lambda settings, domain_name: inconsistent,
    )
    assert import_cli.main(common("verify")) == 3
    assert '"consistent": false' in capsys.readouterr().out

    monkeypatch.setattr(
        import_cli,
        "load_settings",
        lambda path: (_ for _ in ()).throw(ValidationError("bad config")),
    )
    assert import_cli.main(common("verify")) == 2
    assert '"validation_error"' in capsys.readouterr().err

    monkeypatch.setattr(
        import_cli,
        "load_settings",
        lambda path: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    assert import_cli.main(common("verify")) == 2
    error = capsys.readouterr().err
    assert "internal_error" in error
    assert "secret" not in error


def test_import_cli_parser_requires_subcommand() -> None:
    with pytest.raises(SystemExit) as error:
        import_cli.build_parser().parse_args([])
    assert error.value.code == 2


def test_import_cli_closes_application_when_service_raises(monkeypatch) -> None:
    application = Application(
        SimpleNamespace(consistency=lambda: (_ for _ in ()).throw(ValidationError("failed")))
    )
    monkeypatch.setattr(import_cli, "load_settings", lambda path: object())
    monkeypatch.setattr(
        import_cli,
        "bootstrap_import",
        lambda settings, domain_name: application,
    )

    assert import_cli.main(common("verify")) == 2
    assert application.closed == 1


def test_import_cli_module_entrypoint(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["sovereignflow.import_cli", "--help"])
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as error,
    ):
        runpy.run_module("sovereignflow.import_cli", run_name="__main__")
    assert error.value.code == 0
