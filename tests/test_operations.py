from __future__ import annotations

import pytest

from sovereignflow.application import OperationsService
from sovereignflow.domain import (
    DocumentChunk,
    DomainNotFoundError,
    IngestionCommand,
    IngestionJob,
    IngestionJobStatus,
    IngestionResult,
    ValidationError,
)


def job() -> IngestionJob:
    command = IngestionCommand(
        idempotency_key="key",
        domain="general",
        tenant_id="tenant-a",
        source_id="source",
        source_version="v1",
        chunks=(DocumentChunk("chunk", "general", "tenant-a", "source", "text"),),
    )
    return IngestionJob("job-1", "a" * 64, IngestionJobStatus.FAILED, command, attempts=2)


class Audit:
    def __init__(self) -> None:
        self.calls = []

    def fetch(self, request_id: str, *, tenant_id: str):
        self.calls.append(("fetch", request_id, tenant_id))
        return {"request_id": request_id, "tenant_id": tenant_id}

    def metrics(self, *, tenant_id: str, hours: int):
        self.calls.append(("metrics", tenant_id, hours))
        return {"tenant_id": tenant_id, "window_hours": hours}


class Repository:
    def __init__(self) -> None:
        self.calls = []

    def load_for_tenant(self, job_id: str, *, tenant_id: str) -> IngestionJob:
        self.calls.append((job_id, tenant_id))
        return job()


class Ingestion:
    def __init__(self) -> None:
        self.calls = []

    def retry(self, job_id: str) -> IngestionResult:
        self.calls.append(job_id)
        return IngestionResult(
            job_id,
            "general",
            "tenant-a",
            "source",
            "v1",
            IngestionJobStatus.INDEXED,
            1,
        )


def service(*, ingestion_services=None):
    audit = Audit()
    repository = Repository()
    operations = OperationsService(
        audit=audit,
        ingestion_repository=repository,
        ingestion_services=ingestion_services or {},
    )
    return operations, audit, repository


def test_operations_exposes_tenant_scoped_execution_and_metrics() -> None:
    operations, audit, _ = service()

    assert operations.execution("request-1", tenant_id="tenant-a") == {
        "request_id": "request-1",
        "tenant_id": "tenant-a",
    }
    assert operations.metrics(tenant_id="tenant-a", hours=24)["window_hours"] == 24
    assert audit.calls == [
        ("fetch", "request-1", "tenant-a"),
        ("metrics", "tenant-a", 24),
    ]


def test_operations_returns_safe_job_details_and_retries_known_domain() -> None:
    ingestion = Ingestion()
    operations, _, repository = service(ingestion_services={"general": ingestion})

    payload = operations.ingestion_job("job-1", tenant_id="tenant-a")
    retried = operations.retry_ingestion("job-1", tenant_id="tenant-a")

    assert payload == {
        "job_id": "job-1",
        "domain": "general",
        "tenant_id": "tenant-a",
        "source_id": "source",
        "source_version": "v1",
        "status": "failed",
        "attempts": 2,
        "chunk_count": 1,
        "relationship_count": 0,
    }
    assert retried["status"] == "indexed"
    assert repository.calls == [("job-1", "tenant-a"), ("job-1", "tenant-a")]
    assert ingestion.calls == ["job-1"]


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda value: value.execution("", tenant_id="tenant-a"), "request_id"),
        (lambda value: value.execution("request", tenant_id=""), "tenant_id"),
        (lambda value: value.metrics(tenant_id="tenant-a", hours=0), "hours"),
        (lambda value: value.metrics(tenant_id="tenant-a", hours=745), "hours"),
        (lambda value: value.ingestion_job("", tenant_id="tenant-a"), "job_id"),
    ],
)
def test_operations_rejects_invalid_inputs(operation, message: str) -> None:
    operations, _, _ = service()

    with pytest.raises(ValidationError, match=message):
        operation(operations)


def test_operations_rejects_retry_without_domain_service() -> None:
    operations, _, _ = service()

    with pytest.raises(DomainNotFoundError, match="general"):
        operations.retry_ingestion("job-1", tenant_id="tenant-a")
