from __future__ import annotations

import builtins
import hashlib
from importlib.resources import files
from types import SimpleNamespace

import pytest

from sovereignflow.domain import DependencyUnavailableError
from sovereignflow.infrastructure import PostgreSQLMigrationRunner, postgres_support
from sovereignflow.infrastructure import migration_runner as migration_module


class Cursor:
    def __init__(self, *, one=None, error: Exception | None = None) -> None:
        self.one = list(one or [])
        self.error = error
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, statement, parameters=None) -> None:
        if self.error:
            raise self.error
        self.executed.append((str(statement), parameters))

    def fetchone(self):
        return self.one.pop(0) if self.one else None


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self._cursor = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def cursor(self) -> Cursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1


def module_for(connection: Connection):
    return SimpleNamespace(connect=lambda *args, **kwargs: connection)


def runner() -> PostgreSQLMigrationRunner:
    return PostgreSQLMigrationRunner("postgresql://test", timeout_seconds=3)


def test_migrations_are_applied_in_order(monkeypatch) -> None:
    cursor = Cursor(one=[None, None])
    connection = Connection(cursor)
    monkeypatch.setattr(migration_module, "psycopg_module", lambda: module_for(connection))

    runner().migrate()

    migration_statements = [
        statement for statement, _ in cursor.executed if "CREATE SCHEMA" in statement
    ]
    assert connection.commits == 1
    assert len(migration_statements) == 2


def test_existing_migrations_are_verified_and_skipped(monkeypatch) -> None:
    migrations = sorted(
        item
        for item in files("sovereignflow.infrastructure.migrations").iterdir()
        if item.name.endswith(".sql")
    )
    checksums = [
        hashlib.sha256(item.read_text(encoding="utf-8").encode()).hexdigest() for item in migrations
    ]
    cursor = Cursor(one=[(checksum,) for checksum in checksums])
    monkeypatch.setattr(
        migration_module,
        "psycopg_module",
        lambda: module_for(Connection(cursor)),
    )

    runner().migrate()

    assert not any("CREATE SCHEMA" in statement for statement, _ in cursor.executed)


def test_migration_checksum_and_database_failures_are_explicit(monkeypatch) -> None:
    cursor = Cursor(one=[("wrong",)])
    monkeypatch.setattr(
        migration_module,
        "psycopg_module",
        lambda: module_for(Connection(cursor)),
    )
    with pytest.raises(DependencyUnavailableError, match="checksum"):
        runner().migrate()

    monkeypatch.setattr(
        migration_module,
        "psycopg_module",
        lambda: module_for(Connection(Cursor(error=RuntimeError("down")))),
    )
    with pytest.raises(DependencyUnavailableError, match="migration failed"):
        runner().migrate()


def test_psycopg_import_failure_is_explicit(monkeypatch) -> None:
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(__import__("sys").modules, "psycopg", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(DependencyUnavailableError, match="not installed"):
        postgres_support.psycopg_module()


def test_psycopg_import_succeeds() -> None:
    assert postgres_support.psycopg_module().__name__ == "psycopg"
