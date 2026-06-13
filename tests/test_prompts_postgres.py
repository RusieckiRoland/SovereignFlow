from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sovereignflow.domain import ConfigurationError, DependencyUnavailableError
from sovereignflow.infrastructure.postgres import PostgreSQLHealthProbe
from sovereignflow.infrastructure.prompts import FilePromptRepository


def test_file_prompt_repository_reads_only_non_empty_files(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").write_text("  prompt  ", encoding="utf-8")
    repository = FilePromptRepository(tmp_path)

    assert repository.load("answer") == "prompt"
    with pytest.raises(ConfigurationError, match="does not exist"):
        repository.load("missing")
    (tmp_path / "empty.txt").write_text(" ", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="empty"):
        repository.load("empty")
    with pytest.raises(ConfigurationError, match="escapes"):
        repository.load("../secret")


class Cursor:
    def __init__(self, row=(1,), *, fail=False) -> None:
        self.row = row
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query: str) -> None:
        if self.fail:
            raise RuntimeError("database error")

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def cursor(self):
        return self._cursor


def test_postgresql_health_probe_checks_select_one(monkeypatch) -> None:
    calls = []
    module = SimpleNamespace(
        connect=lambda url, connect_timeout: (
            calls.append((url, connect_timeout)) or Connection(Cursor())
        )
    )
    monkeypatch.setitem(sys.modules, "psycopg", module)

    PostgreSQLHealthProbe("postgresql://test", timeout_seconds=3).check()

    assert calls == [("postgresql://test", 3)]


def test_postgresql_health_probe_rejects_failure_and_bad_result(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=lambda *args, **kwargs: Connection(Cursor(fail=True))),
    )
    with pytest.raises(DependencyUnavailableError, match="unavailable"):
        PostgreSQLHealthProbe("postgresql://test").check()

    monkeypatch.setitem(
        sys.modules,
        "psycopg",
        SimpleNamespace(connect=lambda *args, **kwargs: Connection(Cursor((2,)))),
    )
    with pytest.raises(DependencyUnavailableError, match="invalid data"):
        PostgreSQLHealthProbe("postgresql://test").check()


def test_postgresql_health_probe_reports_missing_driver(monkeypatch) -> None:
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)

    with pytest.raises(DependencyUnavailableError, match="not installed"):
        PostgreSQLHealthProbe("postgresql://test").check()
