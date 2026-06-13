from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any

from ..models import DocumentChunk, SearchHit, SearchRequest
from ..ports import EmbeddingProvider


class WeaviateDocumentStore:
    def __init__(
        self,
        client: Any,
        collection_name: str,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._collection = client.collections.get(collection_name)
        self._embedding_provider = embedding_provider

    def upsert(
        self,
        chunks: Sequence[DocumentChunk],
        vectors: Sequence[Sequence[float]] | None = None,
    ) -> None:
        materialized = list(chunks)
        resolved_vectors = vectors or self._embedding_provider.embed_documents(
            [chunk.text for chunk in materialized]
        )
        if len(resolved_vectors) != len(materialized):
            raise ValueError("vectors and chunks must have the same length")

        for chunk, vector in zip(materialized, resolved_vectors, strict=True):
            properties = {
                **chunk.metadata,
                "chunk_id": chunk.chunk_id,
                "domain": chunk.domain,
                "tenant_id": chunk.tenant_id,
                "source_id": chunk.source_id,
                "source_uri": chunk.source_uri,
                "text": chunk.text,
                "metadata_json": json.dumps(chunk.metadata, ensure_ascii=False),
                "acl_labels": list(chunk.acl_labels),
                "classification_level": chunk.classification_level,
            }
            identity = f"{chunk.domain}:{chunk.tenant_id}:{chunk.chunk_id}"
            object_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
            if self._collection.data.exists(object_uuid):
                self._collection.data.replace(
                    uuid=object_uuid,
                    properties=properties,
                    vector=list(vector),
                )
            else:
                self._collection.data.insert(
                    properties=properties,
                    vector=list(vector),
                    uuid=object_uuid,
                )

    def search(self, request: SearchRequest) -> Sequence[SearchHit]:
        query_filter = self._build_filter(request)
        vector = None
        if request.mode in {"semantic", "hybrid"}:
            vector = list(self._embedding_provider.embed_query(request.query))

        query_limit = max(request.top_k, request.top_k * 4)
        if request.mode == "bm25":
            result = self._collection.query.bm25(
                query=request.query,
                limit=query_limit,
                filters=query_filter,
                return_metadata=self._metadata_query(score=True),
            )
        elif request.mode == "semantic":
            result = self._collection.query.near_vector(
                near_vector=vector,
                limit=query_limit,
                filters=query_filter,
                return_metadata=self._metadata_query(distance=True),
            )
        else:
            result = self._collection.query.hybrid(
                query=request.query,
                vector=vector,
                limit=query_limit,
                filters=query_filter,
                return_metadata=self._metadata_query(score=True),
            )

        hits: list[SearchHit] = []
        allowed_labels = set(request.allowed_acl_labels)
        for item in result.objects:
            properties = dict(item.properties or {})
            metadata = _decode_metadata(properties.pop("metadata_json", "{}"))
            chunk = DocumentChunk(
                chunk_id=str(properties.pop("chunk_id")),
                domain=str(properties.pop("domain")),
                tenant_id=str(properties.pop("tenant_id")),
                source_id=str(properties.pop("source_id")),
                source_uri=properties.pop("source_uri", None),
                text=str(properties.pop("text")),
                metadata=metadata,
                acl_labels=tuple(properties.pop("acl_labels", ()) or ()),
                classification_level=int(properties.pop("classification_level", 0) or 0),
            )
            if chunk.acl_labels and not set(chunk.acl_labels).issubset(allowed_labels):
                continue
            raw_score = getattr(item.metadata, "score", None)
            distance = getattr(item.metadata, "distance", None)
            score = float(raw_score) if raw_score is not None else 1.0 - float(distance or 1.0)
            hits.append(SearchHit(chunk=chunk, score=score))
            if len(hits) >= request.top_k:
                break
        return hits

    @staticmethod
    def _metadata_query(**kwargs: bool) -> Any:
        try:
            from weaviate.classes.query import MetadataQuery
        except ImportError as exc:
            raise RuntimeError("Install SovereignFlow with the 'weaviate' extra") from exc
        return MetadataQuery(**kwargs)

    @staticmethod
    def _build_filter(request: SearchRequest) -> Any:
        try:
            from weaviate.classes.query import Filter
        except ImportError as exc:
            raise RuntimeError("Install SovereignFlow with the 'weaviate' extra") from exc

        filters = Filter.by_property("domain").equal(request.domain)
        filters = filters & Filter.by_property("tenant_id").equal(request.tenant_id)
        if request.max_classification_level is not None:
            filters = filters & Filter.by_property("classification_level").less_or_equal(
                request.max_classification_level
            )
        for key, value in request.filters.items():
            filters = filters & Filter.by_property(key).equal(value)
        return filters


def _decode_metadata(value: object) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}
