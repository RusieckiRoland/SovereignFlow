from __future__ import annotations

from types import SimpleNamespace

import pytest

from sovereignflow.domain import (
    DependencyUnavailableError,
    PipelineRun,
    PipelineStepAudit,
)
from sovereignflow.infrastructure import PostgreSQLExecutionAudit
from sovereignflow.infrastructure import audit as audit_module


class Cursor:
    def __init__(self, *, one=None, all_rows=None, error: Exception | None = None) -> None:
        self.one = list(one or [])
        self.all_rows = list(all_rows or [])
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

    def fetchall(self):
        return self.all_rows.pop(0) if self.all_rows else []


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


def audit() -> PostgreSQLExecutionAudit:
    return PostgreSQLExecutionAudit("postgresql://test", timeout_seconds=3)


def run() -> PipelineRun:
    return PipelineRun(
        "00000000-0000-0000-0000-000000000001",
        "request",
        "session",
        "domain",
        "tenant",
        "pipeline",
        "1.0",
        "a" * 64,
        "query",
    )


def test_audit_write_methods_use_parameterized_statements(monkeypatch) -> None:
    repository = audit()
    calls = []
    monkeypatch.setattr(
        repository,
        "_execute",
        lambda statement, parameters: calls.append(parameters),
    )

    repository.start(run())
    repository.record_step(PipelineStepAudit(run().run_id, 1, "step", "action", "1.0", 5, None))
    repository.succeed(run().run_id, answer="answer", citation_count=2)
    repository.fail(run().run_id, error_code="e" * 120, error_message="m" * 2100)

    assert calls[0][-1] == "query"
    assert calls[1][1:4] == (1, "step", "action")
    assert calls[2][0:3] == ("succeeded", "answer", 2)
    assert calls[3][0] == "failed"
    assert len(calls[3][3]) == 100
    assert len(calls[3][4]) == 2000


def test_audit_health_execute_and_failures(monkeypatch) -> None:
    cursor = Cursor(one=[(1,), None])
    connection = Connection(cursor)
    monkeypatch.setattr(audit_module, "psycopg_module", lambda: module_for(connection))
    repository = audit()

    assert repository.name == "execution_audit"
    repository.check()
    assert repository._execute_scalar("SELECT empty") is None
    repository._execute("UPDATE value SET x = %s", (1,))
    assert connection.commits == 1

    monkeypatch.setattr(
        audit_module,
        "psycopg_module",
        lambda: module_for(Connection(Cursor(error=RuntimeError("down")))),
    )
    with pytest.raises(DependencyUnavailableError, match="health"):
        repository.check()
    with pytest.raises(DependencyUnavailableError, match="write"):
        repository._execute("UPDATE", ())


def test_audit_fetch_returns_tenant_scoped_run_and_steps(monkeypatch) -> None:
    run_row = (
        run().run_id,
        "request",
        "session",
        "domain",
        "tenant",
        "pipeline",
        "1.0",
        "a" * 64,
        "succeeded",
        "query",
        "answer",
        1,
        None,
        None,
        "started",
        "completed",
    )
    step_row = (1, "step", "action", "1.0", 5, None, "completed")
    cursor = Cursor(one=[run_row], all_rows=[[step_row]])
    monkeypatch.setattr(audit_module, "psycopg_module", lambda: module_for(Connection(cursor)))

    result = audit().fetch("request", tenant_id="tenant")

    assert result is not None
    assert result["status"] == "succeeded"
    assert result["steps"][0]["action"] == "action"
    assert cursor.executed[0][1] == ("request", "tenant")


def test_audit_fetch_handles_missing_and_database_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        audit_module,
        "psycopg_module",
        lambda: module_for(Connection(Cursor(one=[None]))),
    )
    assert audit().fetch("missing", tenant_id="tenant") is None

    monkeypatch.setattr(
        audit_module,
        "psycopg_module",
        lambda: module_for(Connection(Cursor(error=RuntimeError("down")))),
    )
    with pytest.raises(DependencyUnavailableError, match="read"):
        audit().fetch("request", tenant_id="tenant")
