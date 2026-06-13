from __future__ import annotations

import re
from collections.abc import Sequence

from ..models import DocumentChunk, SearchHit, SearchRequest

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class InMemoryDocumentStore:
    def __init__(self, chunks: Sequence[DocumentChunk] = ()) -> None:
        self._chunks: dict[str, DocumentChunk] = {chunk.chunk_id: chunk for chunk in chunks}

    def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        vectors: Sequence[Sequence[float]] | None = None,
    ) -> None:
        if vectors is not None and len(vectors) != len(chunks):
            raise ValueError("vectors and chunks must have the same length")
        for chunk in chunks:
            self._chunks[chunk.chunk_id] = chunk

    def search(self, request: SearchRequest) -> Sequence[SearchHit]:
        query_tokens = set(_tokens(request.query))
        allowed_labels = set(request.allowed_acl_labels)
        hits: list[SearchHit] = []

        for chunk in self._chunks.values():
            if chunk.domain != request.domain or chunk.tenant_id != request.tenant_id:
                continue
            if chunk.acl_labels and not set(chunk.acl_labels).issubset(allowed_labels):
                continue
            if (
                request.max_classification_level is not None
                and chunk.classification_level > request.max_classification_level
            ):
                continue
            if not _matches_filters(chunk, request.filters):
                continue

            text_tokens = set(_tokens(chunk.text))
            if not query_tokens:
                score = 0.0
            else:
                score = len(query_tokens & text_tokens) / len(query_tokens)
            if score > 0:
                hits.append(SearchHit(chunk=chunk, score=score))

        hits.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
        return hits[: request.top_k]


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]


def _matches_filters(chunk: DocumentChunk, filters: dict[str, object]) -> bool:
    for key, expected in filters.items():
        actual = chunk.metadata.get(key)
        if isinstance(expected, (list, tuple, set)):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True

