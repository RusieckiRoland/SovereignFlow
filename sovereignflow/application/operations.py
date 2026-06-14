from __future__ import annotations

from collections.abc import Mapping

from sovereignflow.domain import (
    DomainNotFoundError,
    IngestionJob,
    ValidationError,
)

from .ingestion import DocumentIngestionService
from .ports import ExecutionAuditPort, IngestionRepositoryPort


class OperationsService:
    def __init__(
        self,
        *,
        audit: ExecutionAuditPort,
        ingestion_repository: IngestionRepositoryPort,
        ingestion_services: Mapping[str, DocumentIngestionService],
    ) -> None:
        self._audit = audit
        self._ingestion_repository = ingestion_repository
        self._ingestion_services = dict(ingestion_services)

    def execution(self, request_id: str, *, tenant_id: str) -> dict | None:
        return self._audit.fetch(
            _required(request_id, "request_id"),
            tenant_id=_required(tenant_id, "tenant_id"),
        )

    def metrics(self, *, tenant_id: str, hours: int) -> dict:
        normalized_tenant = _required(tenant_id, "tenant_id")
        if hours < 1 or hours > 744:
            raise ValidationError("hours must be between 1 and 744")
        return self._audit.metrics(tenant_id=normalized_tenant, hours=hours)

    def ingestion_job(self, job_id: str, *, tenant_id: str) -> dict:
        job = self._ingestion_repository.load_for_tenant(
            _required(job_id, "job_id"),
            tenant_id=_required(tenant_id, "tenant_id"),
        )
        return _job_payload(job)

    def retry_ingestion(self, job_id: str, *, tenant_id: str) -> dict:
        job = self._ingestion_repository.load_for_tenant(
            _required(job_id, "job_id"),
            tenant_id=_required(tenant_id, "tenant_id"),
        )
        service = self._ingestion_services.get(job.command.domain)
        if service is None:
            raise DomainNotFoundError(
                f"No ingestion service exists for domain: {job.command.domain}"
            )
        result = service.retry(job.job_id)
        return {
            "job_id": result.job_id,
            "domain": result.domain,
            "tenant_id": result.tenant_id,
            "source_id": result.source_id,
            "source_version": result.source_version,
            "status": result.status.value,
            "chunk_count": result.chunk_count,
        }


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValidationError(f"{field_name} is required")
    return normalized


def _job_payload(job: IngestionJob) -> dict:
    command = job.command
    return {
        "job_id": job.job_id,
        "domain": command.domain,
        "tenant_id": command.tenant_id,
        "source_id": command.source_id,
        "source_version": command.source_version,
        "status": job.status.value,
        "attempts": job.attempts,
        "chunk_count": len(command.chunks),
        "relationship_count": len(command.relationships),
    }
