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
    disclaimer: str = ""
    allowed_acl_labels: tuple[str, ...] = ()
    max_classification_level: int | None = None

    def __post_init__(self) -> None:
        for field_name in ("name", "collection", "tenant_id", "prompt_name"):
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
