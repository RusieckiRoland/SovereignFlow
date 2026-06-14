from __future__ import annotations

from types import SimpleNamespace

import pytest

from sovereignflow.domain import (
    ConflictError,
    DependencyUnavailableError,
    DocumentChunk,
    IngestionCommand,
    IngestionError,
    IngestionJobStatus,
)
from sovereignflow.infrastructure import PostgreSQLIngestionRepository
from sovereignflow.infrastructure import ingestion as ingestion_module


def command() -> IngestionCommand:
    return IngestionCommand(
        idempotency_key="key-1",
        domain="general",
        tenant_id="tenant-a",
        source_id="source-1",
        source_version="v1",
        source_uri="https://example.test",
        metadata={"kind": "document"},
        chunks=(
            DocumentChunk(
                chunk_id="chunk-1",
                domain="general",
                tenant_id="tenant-a",
                source_id="source-1",
                text="content",
                metadata={"page": 1},
                acl_labels=("public",),
                classification_level=1,
            ),
        ),
    )


def job_row(*, status: str = "staged", metadata='{"kind":"document"}'):
    return (
        "job-1",
        "a" * 64,
        status,
        1,
        "key-1",
        "general",
        "tenant-a",
        "source-1",
        "v1",
        "https://example.test",
        metadata,
    )


def chunk_rows():
    return [
        (
            "chunk-1",
            None,
            "content",
            '{"page":1}',
            ["public"],
            1,
        )
    ]


class Cursor:
    def __init__(
        self,
        *,
        one=(),
        all_rows=(),
        rowcount: int = 1,
        error: Exception | None = None,
    ) -> None:
        self.one = list(one)
        self.all_rows = list(all_rows)
        self.rowcount = rowcount
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
        self.cursor_value = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def cursor(self):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1


class Database:
    def __init__(self, *connections: Connection) -> None:
        self.connections = list(connections)

    def connect(self, *args, **kwargs):
        return self.connections.pop(0)


def repository() -> PostgreSQLIngestionRepository:
    return PostgreSQLIngestionRepository(
        "postgresql://test",
        timeout_seconds=3,
        job_id_factory=lambda: "job-1",
    )


def install(monkeypatch, *cursors: Cursor) -> list[Connection]:
    connections = [Connection(cursor) for cursor in cursors]
    database = Database(*connections)
    monkeypatch.setattr(
        ingestion_module,
        "psycopg_module",
        lambda: SimpleNamespace(connect=database.connect),
    )
    return connections


def test_stage_persists_new_source_chunks_and_job(monkeypatch) -> None:
    cursor = Cursor(one=[None, None])
    connections = install(monkeypatch, cursor)

    job = repository().stage(command(), payload_hash="a" * 64)

    assert job.status == IngestionJobStatus.STAGED
    assert connections[0].commits == 1
    assert any("INSERT INTO ingestion.source_versions" in call[0] for call in cursor.executed)
    assert any("INSERT INTO ingestion.chunks" in call[0] for call in cursor.executed)
    assert any("INSERT INTO ingestion.jobs" in call[0] for call in cursor.executed)


def test_stage_returns_existing_idempotent_job(monkeypatch) -> None:
    cursor = Cursor(
        one=[("job-1", "a" * 64), job_row()],
        all_rows=[chunk_rows()],
    )
    install(monkeypatch, cursor)

    job = repository().stage(command(), payload_hash="a" * 64)

    assert job.job_id == "job-1"
    assert job.command.metadata["kind"] == "document"


def test_stage_rejects_idempotency_and_source_version_conflicts(monkeypatch) -> None:
    install(monkeypatch, Cursor(one=[("job-1", "b" * 64)]))
    with pytest.raises(ConflictError, match="Idempotency"):
        repository().stage(command(), payload_hash="a" * 64)

    install(monkeypatch, Cursor(one=[None, ("b" * 64,)]))
    with pytest.raises(ConflictError, match="Source version"):
        repository().stage(command(), payload_hash="a" * 64)


def test_stage_reuses_identical_source_version_and_maps_database_failure(monkeypatch) -> None:
    cursor = Cursor(one=[None, ("a" * 64,)])
    install(monkeypatch, cursor)

    repository().stage(command(), payload_hash="a" * 64)

    assert not any("INSERT INTO ingestion.chunks" in call[0] for call in cursor.executed)

    install(monkeypatch, Cursor(error=RuntimeError("down")))
    with pytest.raises(DependencyUnavailableError, match="stage failed"):
        repository().stage(command(), payload_hash="a" * 64)


def test_load_reconstructs_job_and_rejects_missing_or_corrupt_data(monkeypatch) -> None:
    install(monkeypatch, Cursor(one=[job_row(status="failed")], all_rows=[chunk_rows()]))
    job = repository().load("job-1")
    assert job.status == IngestionJobStatus.FAILED
    assert job.attempts == 1
    assert job.command.chunks[0].acl_labels == ("public",)

    install(monkeypatch, Cursor(one=[None]))
    with pytest.raises(IngestionError, match="does not exist"):
        repository().load("missing")

    install(
        monkeypatch,
        Cursor(one=[job_row(metadata="[]")], all_rows=[chunk_rows()]),
    )
    with pytest.raises(IngestionError, match="metadata"):
        repository().load("job-1")

    install(monkeypatch, Cursor(error=RuntimeError("down")))
    with pytest.raises(DependencyUnavailableError, match="read failed"):
        repository().load("job-1")


def test_job_state_transitions_are_atomic_and_validated(monkeypatch) -> None:
    indexing = Cursor(rowcount=1)
    indexed = Cursor(one=[("tenant-a", "general", "source-1", "v1")])
    failed = Cursor(rowcount=1)
    connections = install(monkeypatch, indexing, indexed, failed)
    repo = repository()

    repo.mark_indexing("job-1")
    repo.mark_indexed("job-1")
    repo.mark_failed(
        "job-1",
        error_code="e" * 120,
        error_message="m" * 2200,
    )

    assert all(connection.commits == 1 for connection in connections)
    assert len(failed.executed[0][1][0]) == 100
    assert len(failed.executed[0][1][1]) == 2000

    install(monkeypatch, Cursor(rowcount=0))
    with pytest.raises(IngestionError, match="indexing state"):
        repo.mark_indexing("job-1")

    install(monkeypatch, Cursor(one=[None]))
    with pytest.raises(IngestionError, match="indexed state"):
        repo.mark_indexed("job-1")

    install(monkeypatch, Cursor(error=RuntimeError("down")))
    with pytest.raises(DependencyUnavailableError, match="transition failed"):
        repo.mark_failed("job-1", error_code="x", error_message="x")

    install(monkeypatch, Cursor(error=RuntimeError("down")))
    with pytest.raises(DependencyUnavailableError, match="completion failed"):
        repo.mark_indexed("job-1")


def test_health_check_and_metadata_helpers(monkeypatch) -> None:
    install(monkeypatch, Cursor(one=[(1,)]), Cursor(one=[None]))
    repo = repository()

    assert repo.name == "ingestion_repository"
    repo.check()
    assert repo._read_scalar("SELECT nothing") is None
    assert '"kind": "document"' in ingestion_module._json(command().metadata)

    with pytest.raises(IngestionError, match="metadata"):
        ingestion_module._mapping("{")
    with pytest.raises(IngestionError, match="metadata"):
        ingestion_module._mapping([])

    install(monkeypatch, Cursor(error=RuntimeError("down")))
    with pytest.raises(DependencyUnavailableError, match="health check"):
        repo.check()
