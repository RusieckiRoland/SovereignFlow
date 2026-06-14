from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from sovereignflow.domain import (
    DatasetImportRequest,
    DatasetImportRun,
    DatasetImportStatus,
    GraphTraversalRequest,
    IngestionCommand,
    IngestionJob,
    ModelGeneration,
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

    def generate(self, *, system_prompt: str, user_prompt: str) -> ModelGeneration: ...

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

    def succeed(
        self,
        run_id: str,
        *,
        answer: str,
        citation_count: int,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost: float,
    ) -> None: ...

    def fail(self, run_id: str, *, error_code: str, error_message: str) -> None: ...

    def fetch(self, request_id: str, *, tenant_id: str) -> dict | None: ...

    def metrics(self, *, tenant_id: str, hours: int) -> dict: ...


class IngestionRepositoryPort(Protocol):
    def stage(self, command: IngestionCommand, *, payload_hash: str) -> IngestionJob: ...

    def load(self, job_id: str) -> IngestionJob: ...

    def load_for_tenant(self, job_id: str, *, tenant_id: str) -> IngestionJob: ...

    def mark_indexing(self, job_id: str) -> None: ...

    def mark_indexed(self, job_id: str) -> None: ...

    def mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> None: ...

    def replace_relationships(self, command: IngestionCommand) -> None: ...

    def delete_source(
        self,
        *,
        domain: str,
        tenant_id: str,
        source_id: str,
    ) -> None: ...

    def start_import(self, request: DatasetImportRequest) -> DatasetImportRun: ...

    def load_import(self, import_id: str, *, tenant_id: str) -> DatasetImportRun: ...

    def update_import(
        self,
        import_id: str,
        *,
        status: DatasetImportStatus,
        indexed_sources: int,
        published_relationships: int,
        deleted_sources: int,
    ) -> None: ...

    def fail_import(self, import_id: str, *, error_code: str, error_message: str) -> None: ...

    def consistency_counts(self, *, domain: str, tenant_id: str) -> dict[str, int]: ...


class VectorIndexPort(Protocol):
    def replace_source(self, job: IngestionJob, *, collection_name: str) -> None: ...

    def delete_source(
        self,
        *,
        collection_name: str,
        domain: str,
        tenant_id: str,
        source_id: str,
    ) -> None: ...

    def count(self, *, collection_name: str, domain: str, tenant_id: str) -> int: ...


class DatasetReaderPort(Protocol):
    def prepare(self, *, domain: str, tenant_id: str) -> DatasetImportRequest: ...

    def source_commands(self) -> Iterable[IngestionCommand]: ...

    def relationship_commands(self) -> Iterable[IngestionCommand]: ...

    def deletions(self) -> Iterable[str]: ...
