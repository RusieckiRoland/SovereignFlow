from __future__ import annotations

import builtins
import runpy
import sys
from types import SimpleNamespace

import pytest

import sovereignflow.bootstrap as bootstrap_package
from sovereignflow import __main__


class Application:
    def __init__(self) -> None:
        self.app = object()
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def test_main_runs_waitress_with_validated_settings(monkeypatch) -> None:
    application = Application()
    settings = SimpleNamespace(server=SimpleNamespace(host="127.0.0.1", port=8000, threads=4))
    calls = []
    monkeypatch.setattr(sys, "argv", ["sovereignflow", "--config", "config.yaml"])
    monkeypatch.setattr(__main__, "load_settings", lambda path: settings)
    monkeypatch.setattr(__main__, "bootstrap", lambda value: application)
    monkeypatch.setitem(
        sys.modules,
        "waitress",
        SimpleNamespace(serve=lambda app, **kwargs: calls.append((app, kwargs))),
    )

    assert __main__.main() == 0
    assert calls == [
        (
            application.app,
            {"host": "127.0.0.1", "port": 8000, "threads": 4},
        )
    ]


def test_main_returns_failure_when_startup_fails(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["sovereignflow", "--config", "bad.yaml"])
    monkeypatch.setattr(
        __main__,
        "load_settings",
        lambda path: (_ for _ in ()).throw(RuntimeError("bad config")),
    )

    assert __main__.main() == 1
    assert "startup failed" in capsys.readouterr().err


def test_main_closes_application_when_waitress_is_missing(
    monkeypatch,
    capsys,
) -> None:
    application = Application()
    settings = SimpleNamespace(server=SimpleNamespace())
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "waitress":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "argv", ["sovereignflow", "--config", "config.yaml"])
    monkeypatch.setattr(__main__, "load_settings", lambda path: settings)
    monkeypatch.setattr(__main__, "bootstrap", lambda value: application)
    monkeypatch.delitem(sys.modules, "waitress", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)

    assert __main__.main() == 1
    assert application.closed == 1
    assert "waitress is not installed" in capsys.readouterr().err


def test_module_entrypoint_raises_main_result(monkeypatch) -> None:
    application = Application()
    settings = SimpleNamespace(server=SimpleNamespace(host="127.0.0.1", port=8000, threads=2))
    monkeypatch.setattr(sys, "argv", ["sovereignflow", "--config", "config.yaml"])
    monkeypatch.setattr(bootstrap_package, "load_settings", lambda path: settings)
    monkeypatch.setattr(bootstrap_package, "bootstrap", lambda value: application)
    monkeypatch.setitem(
        sys.modules,
        "waitress",
        SimpleNamespace(serve=lambda *args, **kwargs: None),
    )
    monkeypatch.delitem(sys.modules, "sovereignflow.__main__", raising=False)

    with pytest.raises(SystemExit, match="0"):
        runpy.run_module("sovereignflow.__main__", run_name="__main__")
