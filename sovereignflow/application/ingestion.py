from __future__ import annotations

import hashlib
import json

from sovereignflow.domain import (
    DocumentSecurity,
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
            _validate_document_security(
                model=self._domain.security_model,
                security=chunk.security,
            )

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
                "security": _security_payload(chunk.security),
            }
            for chunk in sorted(command.chunks, key=lambda item: item.chunk_id)
        ],
        "relationships": [
            {
                "from_source_id": relationship.from_node.source_id,
                "from_chunk_id": relationship.from_node.chunk_id,
                "to_source_id": relationship.to_node.source_id,
                "to_chunk_id": relationship.to_node.chunk_id,
                "relationship_type": relationship.relationship_type,
                "metadata": dict(relationship.metadata),
            }
            for relationship in sorted(
                command.relationships,
                key=lambda item: (
                    item.from_node.source_id,
                    item.from_node.chunk_id,
                    item.to_node.source_id,
                    item.to_node.chunk_id,
                    item.relationship_type,
                ),
            )
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


def _validate_document_security(*, model, security: DocumentSecurity) -> None:
    kind = getattr(model.kind, "value", None)
    if kind == "none":
        if security.clearance_label is not None or security.classification_labels:
            raise PolicyViolationError("Ingestion contains security metadata for disabled model")
        return
    if kind == "clearance_level":
        if model.clearance_level is None or security.clearance_label is None:
            raise PolicyViolationError("Ingestion contains incomplete clearance metadata")
        try:
            model.clearance_level.value(
                security.clearance_label,
                "chunk.security.clearance_label",
            )
        except ValidationError as exc:
            raise PolicyViolationError("Ingestion contains a forbidden clearance label") from exc
        if security.classification_labels:
            raise PolicyViolationError("Ingestion mixed security model metadata")
        return
    if kind == "classification_labels":
        if model.classification_labels is None:
            raise PolicyViolationError("Ingestion contains incomplete classification label model")
        try:
            model.classification_labels.validate_labels(
                security.classification_labels,
                "chunk.security.classification_labels",
            )
        except ValidationError as exc:
            raise PolicyViolationError(
                "Ingestion contains a forbidden classification label"
            ) from exc
        if security.clearance_label is not None:
            raise PolicyViolationError("Ingestion mixed security model metadata")
        return
    raise ValidationError("Unsupported security model")


def _security_payload(security: DocumentSecurity) -> dict[str, object]:
    return {
        "clearance_label": security.clearance_label,
        "classification_labels": list(security.classification_labels),
    }
