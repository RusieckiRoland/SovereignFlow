from __future__ import annotations

from dataclasses import replace

import pytest

from sovereignflow.application.ingestion import DocumentIngestionService, _payload_hash
from sovereignflow.domain import (
    DependencyUnavailableError,
    DocumentChunk,
    DomainProfile,
    IngestionCommand,
    IngestionJob,
    IngestionJobStatus,
    IngestionResult,
    PolicyViolationError,
    RetrievalProfile,
    SearchMode,
    ValidationError,
)


def profile() -> DomainProfile:
    return DomainProfile(
        name="general",
        description="",
        collection="General",
        tenant_id="tenant-a",
        prompt_name="answer",
        allow_external_model=False,
        retrieval=RetrievalProfile(SearchMode.HYBRID, 5, 1000),
        allowed_acl_labels=("public", "staff"),
        max_classification_level=2,
    )


def command(
    *,
    domain: str = "general",
    tenant_id: str = "tenant-a",
    acl_labels: tuple[str, ...] = ("public",),
    classification_level: int = 1,
) -> IngestionCommand:
    return IngestionCommand(
        idempotency_key="import-1",
        domain=domain,
        tenant_id=tenant_id,
        source_id="source-1",
        source_version="v1",
        source_uri="https://example.test/source-1",
        metadata={"language": "en"},
        chunks=(
            DocumentChunk(
                chunk_id="chunk-2",
                domain=domain,
                tenant_id=tenant_id,
                source_id="source-1",
                source_uri="https://example.test/source-1#2",
                text="second",
                metadata={"page": 2},
                acl_labels=acl_labels,
                classification_level=classification_level,
            ),
            DocumentChunk(
                chunk_id="chunk-1",
                domain=domain,
                tenant_id=tenant_id,
                source_id="source-1",
                source_uri="https://example.test/source-1#1",
                text="first",
                metadata={"page": 1},
                acl_labels=acl_labels,
                classification_level=classification_level,
            ),
        ),
    )


class Repository:
    def __init__(self, *, initial_status: IngestionJobStatus = IngestionJobStatus.STAGED) -> None:
        self.initial_status = initial_status
        self.job: IngestionJob | None = None
        self.calls = []
        self.failure = None

    def stage(self, ingestion_command, *, payload_hash):
        self.calls.append(("stage", payload_hash))
        self.job = IngestionJob(
            job_id="job-1",
            payload_hash=payload_hash,
            status=self.initial_status,
            command=ingestion_command,
        )
        return self.job

    def load(self, job_id):
        self.calls.append(("load", job_id))
        assert self.job is not None
        return self.job

    def mark_indexing(self, job_id):
        self.calls.append(("indexing", job_id))

    def mark_indexed(self, job_id):
        self.calls.append(("indexed", job_id))

    def mark_failed(self, job_id, *, error_code, error_message):
        self.failure = (job_id, error_code, error_message)
        self.calls.append(("failed", job_id))


class VectorIndex:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def replace_source(self, job, *, collection_name):
        self.calls.append((job, collection_name))
        if self.error:
            raise self.error


def service(repository: Repository, index: VectorIndex) -> DocumentIngestionService:
    return DocumentIngestionService(
        domain=profile(),
        repository=repository,
        vector_index=index,
    )


def test_ingestion_stages_indexes_and_completes_source() -> None:
    repository = Repository()
    index = VectorIndex()

    result = service(repository, index).ingest(command())

    assert result.status == IngestionJobStatus.INDEXED
    assert result.chunk_count == 2
    assert [call[0] for call in repository.calls] == ["stage", "indexing", "indexed"]
    assert index.calls[0][1] == "General"


def test_indexed_idempotent_job_is_returned_without_reindexing() -> None:
    repository = Repository(initial_status=IngestionJobStatus.INDEXED)
    index = VectorIndex()

    result = service(repository, index).ingest(command())

    assert result.job_id == "job-1"
    assert [call[0] for call in repository.calls] == ["stage"]
    assert index.calls == []


def test_failed_job_can_be_retried_explicitly() -> None:
    repository = Repository(initial_status=IngestionJobStatus.FAILED)
    repository.stage(command(), payload_hash=_payload_hash(command()))
    index = VectorIndex()

    result = service(repository, index).retry("job-1")

    assert result.status == IngestionJobStatus.INDEXED
    assert [call[0] for call in repository.calls[-3:]] == ["load", "indexing", "indexed"]


@pytest.mark.parametrize(
    "invalid_command",
    [
        command(domain="other"),
        command(tenant_id="tenant-b"),
        command(acl_labels=("forbidden",)),
        command(classification_level=3),
    ],
)
def test_ingestion_enforces_domain_tenant_acl_and_classification(invalid_command) -> None:
    repository = Repository()

    with pytest.raises(PolicyViolationError):
        service(repository, VectorIndex()).ingest(invalid_command)

    assert repository.calls == []


def test_provider_failure_is_persisted_and_rethrown() -> None:
    repository = Repository()
    error = DependencyUnavailableError("vector database unavailable")

    with pytest.raises(DependencyUnavailableError, match="vector database unavailable"):
        service(repository, VectorIndex(error)).ingest(command())

    assert repository.failure == (
        "job-1",
        "dependency_unavailable",
        "vector database unavailable",
    )


def test_unhandled_failure_is_recorded_without_leaking_details() -> None:
    repository = Repository()

    with pytest.raises(RuntimeError, match="secret"):
        service(repository, VectorIndex(RuntimeError("secret"))).ingest(command())

    assert repository.failure == (
        "job-1",
        "internal_error",
        "Unhandled vector indexing failure",
    )


def test_payload_hash_is_deterministic_and_validates_json_metadata() -> None:
    original = command()
    reordered = replace(original, chunks=tuple(reversed(original.chunks)))

    assert _payload_hash(original) == _payload_hash(reordered)

    invalid = replace(original, metadata={"invalid": object()})
    with pytest.raises(ValidationError, match="valid JSON"):
        _payload_hash(invalid)


def test_ingestion_domain_models_reject_invalid_state() -> None:
    base = command()
    with pytest.raises(ValidationError, match="cannot be empty"):
        replace(base, chunks=())
    with pytest.raises(ValidationError, match="must match"):
        replace(
            base,
            chunks=(replace(base.chunks[0], source_id="other"),),
        )
    with pytest.raises(ValidationError, match="Duplicate"):
        replace(base, chunks=(base.chunks[0], base.chunks[0]))
    with pytest.raises(ValidationError, match="negative"):
        IngestionJob(
            job_id="job",
            payload_hash="hash",
            status=IngestionJobStatus.STAGED,
            command=base,
            attempts=-1,
        )
    with pytest.raises(ValidationError, match="greater than zero"):
        IngestionResult(
            job_id="job",
            domain="general",
            tenant_id="tenant-a",
            source_id="source",
            source_version="v1",
            status=IngestionJobStatus.INDEXED,
            chunk_count=0,
        )
