from __future__ import annotations

import json
from typing import Any

from sovereignflow.application import EmbeddingGatewayPort
from sovereignflow.domain import (
    DependencyUnavailableError,
    DocumentChunk,
    ProviderProtocolError,
    SearchHit,
    SearchMode,
    SearchRequest,
)


class WeaviateHealthProbe:
    name = "weaviate"

    def __init__(self, client: Any) -> None:
        self._client = client

    def check(self) -> None:
        try:
            ready = self._client.is_ready()
        except Exception as exc:
            raise DependencyUnavailableError("Weaviate is unavailable") from exc
        if not ready:
            raise DependencyUnavailableError("Weaviate is not ready")


class WeaviateRetrievalAdapter:
    def __init__(
        self,
        *,
        client: Any,
        collection_name: str,
        embeddings: EmbeddingGatewayPort,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._collection = client.collections.use(collection_name)
        self._embeddings = embeddings

    def healthcheck(self) -> None:
        WeaviateHealthProbe(self._client).check()
        if not self._client.collections.exists(self._collection_name):
            raise DependencyUnavailableError(
                f"Weaviate collection does not exist: {self._collection_name}"
            )

    def search(self, request: SearchRequest) -> tuple[SearchHit, ...]:
        query_filter = self._build_filter(request)
        metadata_query = self._metadata_query(request.mode)
        try:
            if request.mode == SearchMode.BM25:
                response = self._collection.query.bm25(
                    query=request.query,
                    limit=request.top_k,
                    filters=query_filter,
                    return_metadata=metadata_query,
                )
            elif request.mode == SearchMode.SEMANTIC:
                response = self._collection.query.near_vector(
                    near_vector=list(self._embeddings.embed_query(request.query)),
                    limit=request.top_k,
                    filters=query_filter,
                    return_metadata=metadata_query,
                )
            else:
                response = self._collection.query.hybrid(
                    query=request.query,
                    vector=list(self._embeddings.embed_query(request.query)),
                    limit=request.top_k,
                    filters=query_filter,
                    return_metadata=metadata_query,
                )
        except (DependencyUnavailableError, ProviderProtocolError):
            raise
        except Exception as exc:
            raise DependencyUnavailableError("Weaviate query failed") from exc
        return tuple(self._to_hit(item, request.mode) for item in response.objects)

    @staticmethod
    def _metadata_query(mode: SearchMode) -> Any:
        try:
            from weaviate.classes.query import MetadataQuery
        except ImportError as exc:
            raise DependencyUnavailableError("weaviate-client is not installed") from exc
        if mode == SearchMode.SEMANTIC:
            return MetadataQuery(distance=True)
        return MetadataQuery(score=True)

    @staticmethod
    def _build_filter(request: SearchRequest) -> Any:
        try:
            from weaviate.classes.query import Filter
        except ImportError as exc:
            raise DependencyUnavailableError("weaviate-client is not installed") from exc
        query_filter = Filter.by_property("domain").equal(request.domain)
        query_filter = query_filter & Filter.by_property("tenant_id").equal(request.tenant_id)
        if request.max_classification_level is not None:
            query_filter = query_filter & Filter.by_property("classification_level").less_or_equal(
                request.max_classification_level
            )
        for key, value in request.filters.items():
            query_filter = query_filter & Filter.by_property(str(key)).equal(value)
        return query_filter

    @staticmethod
    def _to_hit(item: Any, mode: SearchMode) -> SearchHit:
        properties = dict(item.properties or {})
        try:
            metadata_raw = properties.pop("metadata_json", "{}")
            metadata = json.loads(str(metadata_raw or "{}"))
            if not isinstance(metadata, dict):
                raise TypeError("metadata_json must decode to an object")
            chunk = DocumentChunk(
                chunk_id=str(properties["chunk_id"]),
                domain=str(properties["domain"]),
                tenant_id=str(properties["tenant_id"]),
                source_id=str(properties["source_id"]),
                source_uri=properties.get("source_uri"),
                text=str(properties["text"]),
                metadata=metadata,
                acl_labels=tuple(properties.get("acl_labels") or ()),
                classification_level=int(properties.get("classification_level") or 0),
            )
            if mode == SearchMode.SEMANTIC:
                distance = float(item.metadata.distance)
                score = 1.0 - distance
                score_type = "certainty"
            else:
                score = float(item.metadata.score)
                score_type = "bm25" if mode == SearchMode.BM25 else "hybrid"
        except (KeyError, TypeError, ValueError, AttributeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError("Weaviate returned an invalid object") from exc
        return SearchHit(chunk=chunk, score=score, score_type=score_type)
