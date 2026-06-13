from __future__ import annotations

from typing import Protocol, Sequence

from .models import DocumentChunk, SearchHit, SearchRequest


class RetrievalBackend(Protocol):
    def search(self, request: SearchRequest) -> Sequence[SearchHit]:
        ...


class ChunkStore(Protocol):
    def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        vectors: Sequence[Sequence[float]] | None = None,
    ) -> None:
        ...


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...

    def embed_query(self, text: str) -> Sequence[float]:
        ...


class ModelClient(Protocol):
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        security_context: dict[str, object] | None = None,
    ) -> str:
        ...

