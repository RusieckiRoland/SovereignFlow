from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .errors import ValidationError


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValidationError(f"{field_name} is required")
    return normalized


def _immutable_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


class SearchMode(StrEnum):
    SEMANTIC = "semantic"
    BM25 = "bm25"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    domain: str
    tenant_id: str
    source_id: str
    text: str
    source_uri: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    acl_labels: tuple[str, ...] = ()
    classification_level: int = 0

    def __post_init__(self) -> None:
        for field_name in ("chunk_id", "domain", "tenant_id", "source_id", "text"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"DocumentChunk.{field_name}"),
            )
        if self.classification_level < 0:
            raise ValidationError("DocumentChunk.classification_level cannot be negative")
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))
        object.__setattr__(
            self,
            "acl_labels",
            tuple(
                sorted(
                    {_required(label, "DocumentChunk.acl_labels[]") for label in self.acl_labels}
                )
            ),
        )


@dataclass(frozen=True)
class RetrievalProfile:
    mode: SearchMode
    top_k: int
    max_context_characters: int
    filters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ValidationError("RetrievalProfile.top_k must be greater than zero")
        if self.max_context_characters < 1:
            raise ValidationError(
                "RetrievalProfile.max_context_characters must be greater than zero"
            )
        object.__setattr__(self, "filters", _immutable_mapping(self.filters))


@dataclass(frozen=True)
class DomainProfile:
    name: str
    description: str
    collection: str
    tenant_id: str
    prompt_name: str
    allow_external_model: bool
    retrieval: RetrievalProfile
    pipeline_name: str = "default"
    disclaimer: str = ""
    allowed_acl_labels: tuple[str, ...] = ()
    max_classification_level: int | None = None

    def __post_init__(self) -> None:
        for field_name in ("name", "collection", "tenant_id", "prompt_name", "pipeline_name"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"DomainProfile.{field_name}"),
            )
        if self.max_classification_level is not None and self.max_classification_level < 0:
            raise ValidationError("DomainProfile.max_classification_level cannot be negative")
        object.__setattr__(
            self,
            "allowed_acl_labels",
            tuple(
                sorted(
                    {
                        _required(label, "DomainProfile.allowed_acl_labels[]")
                        for label in self.allowed_acl_labels
                    }
                )
            ),
        )


@dataclass(frozen=True)
class SearchRequest:
    query: str
    domain: str
    tenant_id: str
    top_k: int
    mode: SearchMode
    filters: Mapping[str, Any]
    allowed_acl_labels: tuple[str, ...]
    max_classification_level: int | None

    def __post_init__(self) -> None:
        for field_name in ("query", "domain", "tenant_id"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"SearchRequest.{field_name}"),
            )
        if self.top_k < 1:
            raise ValidationError("SearchRequest.top_k must be greater than zero")
        object.__setattr__(self, "filters", _immutable_mapping(self.filters))


@dataclass(frozen=True)
class SearchHit:
    chunk: DocumentChunk
    score: float
    score_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.score, (int, float)):
            raise ValidationError("SearchHit.score must be numeric")
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(
            self,
            "score_type",
            _required(self.score_type, "SearchHit.score_type"),
        )


@dataclass(frozen=True)
class Citation:
    source_id: str
    chunk_id: str
    source_uri: str | None
    score: float
    score_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _required(self.source_id, "Citation.source_id"))
        object.__setattr__(self, "chunk_id", _required(self.chunk_id, "Citation.chunk_id"))
        object.__setattr__(self, "score_type", _required(self.score_type, "Citation.score_type"))
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))


@dataclass(frozen=True)
class QueryCommand:
    request_id: str
    query: str
    domain: str
    session_id: str
    filters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("request_id", "query", "domain", "session_id"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"QueryCommand.{field_name}"),
            )
        object.__setattr__(self, "filters", _immutable_mapping(self.filters))


@dataclass(frozen=True)
class QueryResult:
    request_id: str
    answer: str
    domain: str
    session_id: str
    citations: tuple[Citation, ...]
    pipeline_trace: tuple[str, ...]


class PipelineRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class PipelineStepDefinition:
    step_id: str
    action: str
    action_version: str
    next_step_id: str | None = None
    routes: Mapping[str, str] = field(default_factory=dict)
    terminal: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "step_id",
            _required(self.step_id, "PipelineStepDefinition.step_id"),
        )
        object.__setattr__(self, "action", _required(self.action, "PipelineStepDefinition.action"))
        object.__setattr__(
            self,
            "action_version",
            _required(self.action_version, "PipelineStepDefinition.action_version"),
        )
        if self.next_step_id is not None:
            object.__setattr__(
                self,
                "next_step_id",
                _required(self.next_step_id, "PipelineStepDefinition.next_step_id"),
            )
        normalized_routes = {
            _required(key, "PipelineStepDefinition.routes key"): _required(
                value,
                "PipelineStepDefinition.routes value",
            )
            for key, value in self.routes.items()
        }
        object.__setattr__(self, "routes", MappingProxyType(normalized_routes))
        if self.terminal and (self.next_step_id is not None or self.routes):
            raise ValidationError("A terminal pipeline step cannot define transitions")
        if not self.terminal and self.next_step_id is None and not self.routes:
            raise ValidationError("A non-terminal pipeline step must define a transition")


@dataclass(frozen=True)
class PipelineDefinition:
    name: str
    behavior_version: str
    entry_step_id: str
    max_steps: int
    steps: tuple[PipelineStepDefinition, ...]
    checksum: str

    def __post_init__(self) -> None:
        for field_name in ("name", "behavior_version", "entry_step_id", "checksum"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"PipelineDefinition.{field_name}"),
            )
        if self.max_steps < 1:
            raise ValidationError("PipelineDefinition.max_steps must be greater than zero")
        if not self.steps:
            raise ValidationError("PipelineDefinition.steps cannot be empty")

    def step(self, step_id: str) -> PipelineStepDefinition:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise ValidationError(f"Unknown pipeline step: {step_id}")


@dataclass(frozen=True)
class PipelineRun:
    run_id: str
    request_id: str
    session_id: str
    domain: str
    tenant_id: str
    pipeline_name: str
    pipeline_version: str
    pipeline_checksum: str
    query: str

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "request_id",
            "session_id",
            "domain",
            "tenant_id",
            "pipeline_name",
            "pipeline_version",
            "pipeline_checksum",
            "query",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"PipelineRun.{field_name}"),
            )


@dataclass(frozen=True)
class PipelineStepAudit:
    run_id: str
    sequence_number: int
    step_id: str
    action: str
    action_version: str
    duration_ms: int
    next_step_id: str | None

    def __post_init__(self) -> None:
        for field_name in ("run_id", "step_id", "action", "action_version"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"PipelineStepAudit.{field_name}"),
            )
        if self.sequence_number < 1:
            raise ValidationError("PipelineStepAudit.sequence_number must be greater than zero")
        if self.duration_ms < 0:
            raise ValidationError("PipelineStepAudit.duration_ms cannot be negative")


class IngestionJobStatus(StrEnum):
    STAGED = "staged"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


@dataclass(frozen=True)
class IngestionCommand:
    idempotency_key: str
    domain: str
    tenant_id: str
    source_id: str
    source_version: str
    chunks: tuple[DocumentChunk, ...]
    source_uri: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "idempotency_key",
            "domain",
            "tenant_id",
            "source_id",
            "source_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"IngestionCommand.{field_name}"),
            )
        if not self.chunks:
            raise ValidationError("IngestionCommand.chunks cannot be empty")
        chunk_ids: set[str] = set()
        for chunk in self.chunks:
            if (
                chunk.domain != self.domain
                or chunk.tenant_id != self.tenant_id
                or chunk.source_id != self.source_id
            ):
                raise ValidationError(
                    "Every ingestion chunk must match command domain, tenant and source"
                )
            if chunk.chunk_id in chunk_ids:
                raise ValidationError(f"Duplicate ingestion chunk id: {chunk.chunk_id}")
            chunk_ids.add(chunk.chunk_id)
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))


@dataclass(frozen=True)
class IngestionJob:
    job_id: str
    payload_hash: str
    status: IngestionJobStatus
    command: IngestionCommand
    attempts: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _required(self.job_id, "IngestionJob.job_id"))
        object.__setattr__(
            self,
            "payload_hash",
            _required(self.payload_hash, "IngestionJob.payload_hash"),
        )
        if self.attempts < 0:
            raise ValidationError("IngestionJob.attempts cannot be negative")


@dataclass(frozen=True)
class IngestionResult:
    job_id: str
    domain: str
    tenant_id: str
    source_id: str
    source_version: str
    status: IngestionJobStatus
    chunk_count: int

    def __post_init__(self) -> None:
        for field_name in ("job_id", "domain", "tenant_id", "source_id", "source_version"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"IngestionResult.{field_name}"),
            )
        if self.chunk_count < 1:
            raise ValidationError("IngestionResult.chunk_count must be greater than zero")
