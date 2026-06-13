from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    domain: str
    source_id: str
    text: str
    tenant_id: str = "default"
    source_uri: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    acl_labels: tuple[str, ...] = ()
    classification_level: int = 0

    def __post_init__(self) -> None:
        for field_name in ("chunk_id", "domain", "source_id", "text", "tenant_id"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"DocumentChunk.{field_name} is required")
        if self.classification_level < 0:
            raise ValueError("classification_level cannot be negative")


@dataclass(frozen=True)
class SearchRequest:
    query: str
    domain: str
    tenant_id: str = "default"
    top_k: int = 8
    mode: str = "hybrid"
    filters: dict[str, Any] = field(default_factory=dict)
    allowed_acl_labels: tuple[str, ...] = ()
    max_classification_level: int | None = None


@dataclass(frozen=True)
class SearchHit:
    chunk: DocumentChunk
    score: float


@dataclass(frozen=True)
class Citation:
    source_id: str
    chunk_id: str
    source_uri: str | None
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryRequest:
    query: str
    domain: str
    session_id: str
    tenant_id: str = "default"
    user_id: str | None = None
    locale: str = "en"
    filters: dict[str, Any] = field(default_factory=dict)
    allowed_acl_labels: tuple[str, ...] = ()
    max_classification_level: int | None = None

    def __post_init__(self) -> None:
        for field_name in ("query", "domain", "session_id", "tenant_id"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"QueryRequest.{field_name} is required")
        if self.max_classification_level is not None and self.max_classification_level < 0:
            raise ValueError("max_classification_level cannot be negative")


@dataclass(frozen=True)
class QueryResponse:
    answer: str
    domain: str
    session_id: str
    citations: tuple[Citation, ...]
    pipeline_trace: tuple[str, ...]
