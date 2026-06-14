from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sovereignflow.domain import (
    GraphTraversalRequest,
    IngestionCommand,
    IngestionJob,
    PipelineRun,
    PipelineStepAudit,
    SearchHit,
    SearchRequest,
)


class RetrievalPort(Protocol):
    def search(self, request: SearchRequest) -> Sequence[SearchHit]: ...

    def healthcheck(self) -> None: ...


class GraphTraversalPort(Protocol):
    def expand(self, request: GraphTraversalRequest) -> Sequence[SearchHit]: ...

    def check(self) -> None: ...


class EmbeddingGatewayPort(Protocol):
    def embed_query(self, text: str) -> Sequence[float]: ...

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def healthcheck(self) -> None: ...


class ModelGatewayPort(Protocol):
    @property
    def scope(self) -> str: ...

    def generate(self, *, system_prompt: str, user_prompt: str) -> str: ...

    def healthcheck(self) -> None: ...


class PromptRepositoryPort(Protocol):
    def load(self, prompt_name: str) -> str: ...


class HealthProbe(Protocol):
    @property
    def name(self) -> str: ...

    def check(self) -> None: ...


class ExecutionAuditPort(Protocol):
    def start(self, run: PipelineRun) -> None: ...

    def record_step(self, step: PipelineStepAudit) -> None: ...

    def succeed(self, run_id: str, *, answer: str, citation_count: int) -> None: ...

    def fail(self, run_id: str, *, error_code: str, error_message: str) -> None: ...


class IngestionRepositoryPort(Protocol):
    def stage(self, command: IngestionCommand, *, payload_hash: str) -> IngestionJob: ...

    def load(self, job_id: str) -> IngestionJob: ...

    def mark_indexing(self, job_id: str) -> None: ...

    def mark_indexed(self, job_id: str) -> None: ...

    def mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> None: ...


class VectorIndexPort(Protocol):
    def replace_source(self, job: IngestionJob, *, collection_name: str) -> None: ...
