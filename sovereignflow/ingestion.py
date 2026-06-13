from __future__ import annotations

from collections.abc import Sequence

from .models import DocumentChunk
from .ports import ChunkStore, EmbeddingProvider


class IngestionService:
    def __init__(self, store: ChunkStore, embedding_provider: EmbeddingProvider | None = None) -> None:
        self._store = store
        self._embedding_provider = embedding_provider

    def ingest(self, chunks: Sequence[DocumentChunk]) -> int:
        materialized = list(chunks)
        if not materialized:
            return 0

        vectors = None
        if self._embedding_provider is not None:
            vectors = self._embedding_provider.embed_documents([chunk.text for chunk in materialized])
            if len(vectors) != len(materialized):
                raise ValueError("Embedding provider returned a different number of vectors than chunks")

        self._store.upsert(materialized, vectors)
        return len(materialized)

