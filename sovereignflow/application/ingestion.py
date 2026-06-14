from __future__ import annotations

import hashlib
import json

from sovereignflow.domain import (
    DomainProfile,
    IngestionCommand,
    IngestionJob,
    IngestionJobStatus,
    IngestionResult,
    PolicyViolationError,
    SovereignFlowError,
    ValidationError,
)

from .ports import IngestionRepositoryPort, VectorIndexPort


class DocumentIngestionService:
    def __init__(
        self,
        *,
        domain: DomainProfile,
        repository: IngestionRepositoryPort,
        vector_index: VectorIndexPort,
    ) -> None:
        self._domain = domain
        self._repository = repository
        self._vector_index = vector_index

    def ingest(self, command: IngestionCommand) -> IngestionResult:
        self._verify_boundary(command)
        job = self._repository.stage(command, payload_hash=_payload_hash(command))
        return self._process(job)

    def retry(self, job_id: str) -> IngestionResult:
        job = self._repository.load(job_id)
        self._verify_boundary(job.command)
        return self._process(job)

    def _process(self, job: IngestionJob) -> IngestionResult:
        if job.status == IngestionJobStatus.INDEXED:
            return self._result(job)
        self._repository.mark_indexing(job.job_id)
        try:
            self._vector_index.replace_source(
                job,
                collection_name=self._domain.collection,
            )
            self._repository.mark_indexed(job.job_id)
        except Exception as exc:
            error_code = exc.code if isinstance(exc, SovereignFlowError) else "internal_error"
            error_message = (
                exc.safe_message
                if isinstance(exc, SovereignFlowError)
                else "Unhandled vector indexing failure"
            )
            self._repository.mark_failed(
                job.job_id,
                error_code=error_code,
                error_message=error_message,
            )
            raise
        return self._result(job)

    def _verify_boundary(self, command: IngestionCommand) -> None:
        if command.domain != self._domain.name or command.tenant_id != self._domain.tenant_id:
            raise PolicyViolationError("Ingestion crossed a domain or tenant boundary")
        allowed_labels = set(self._domain.allowed_acl_labels)
        for chunk in command.chunks:
            if chunk.acl_labels and not set(chunk.acl_labels).issubset(allowed_labels):
                raise PolicyViolationError("Ingestion contains a forbidden ACL label")
            maximum = self._domain.max_classification_level
            if maximum is not None and chunk.classification_level > maximum:
                raise PolicyViolationError("Ingestion contains a forbidden classification level")

    @staticmethod
    def _result(job: IngestionJob) -> IngestionResult:
        command = job.command
        return IngestionResult(
            job_id=job.job_id,
            domain=command.domain,
            tenant_id=command.tenant_id,
            source_id=command.source_id,
            source_version=command.source_version,
            status=IngestionJobStatus.INDEXED,
            chunk_count=len(command.chunks),
        )


def _payload_hash(command: IngestionCommand) -> str:
    payload = {
        "domain": command.domain,
        "tenant_id": command.tenant_id,
        "source_id": command.source_id,
        "source_version": command.source_version,
        "source_uri": command.source_uri,
        "metadata": dict(command.metadata),
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "source_uri": chunk.source_uri,
                "metadata": dict(chunk.metadata),
                "acl_labels": list(chunk.acl_labels),
                "classification_level": chunk.classification_level,
            }
            for chunk in sorted(command.chunks, key=lambda item: item.chunk_id)
        ],
    }
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("Ingestion metadata must be valid JSON") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
