from __future__ import annotations

import builtins
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sovereignflow.domain import (
    DependencyUnavailableError,
    ProviderProtocolError,
    SearchMode,
    SearchRequest,
)
from sovereignflow.infrastructure.weaviate import (
    WeaviateHealthProbe,
    WeaviateRetrievalAdapter,
)


class Embeddings:
    def __init__(self) -> None:
        self.queries = []

    def embed_query(self, text: str):
        self.queries.append(text)
        return (0.1, 0.2)

    def healthcheck(self) -> None:
        return


class Query:
    def __init__(self, objects=(), *, error: Exception | None = None) -> None:
        self.objects = list(objects)
        self.error = error
        self.calls = []

    def _call(self, mode: str, **kwargs):
        self.calls.append((mode, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(objects=self.objects)

    def bm25(self, **kwargs):
        return self._call("bm25", **kwargs)

    def near_vector(self, **kwargs):
        return self._call("semantic", **kwargs)

    def hybrid(self, **kwargs):
        return self._call("hybrid", **kwargs)


class Collections:
    def __init__(self, query: Query, exists=True) -> None:
        self._collection = SimpleNamespace(query=query)
        self._exists = exists
        self.used = []

    def use(self, name: str):
        self.used.append(name)
        return self._collection

    def exists(self, name: str) -> bool:
        return self._exists


class Client:
    def __init__(self, query: Query, *, ready=True, exists=True, error=None) -> None:
        self.collections = Collections(query, exists)
        self.ready = ready
        self.error = error

    def is_ready(self):
        if self.error:
            raise self.error
        return self.ready


def item(mode: SearchMode, *, properties=None):
    metadata = (
        SimpleNamespace(distance=0.25)
        if mode == SearchMode.SEMANTIC
        else SimpleNamespace(score=0.8)
    )
    resolved_properties = (
        {
            "chunk_id": "chunk-1",
            "domain": "general",
            "tenant_id": "tenant-a",
            "source_id": "source-1",
            "source_uri": "https://example.test",
            "text": "evidence",
            "metadata_json": '{"kind":"example"}',
            "acl_labels": ["public"],
            "classification_level": 1,
        }
        if properties is None
        else properties
    )
    return SimpleNamespace(
        properties=resolved_properties,
        metadata=metadata,
    )


def request(mode: SearchMode) -> SearchRequest:
    return SearchRequest(
        query="question",
        domain="general",
        tenant_id="tenant-a",
        top_k=3,
        mode=mode,
        filters={"status": "active"},
        allowed_acl_labels=("public",),
        max_classification_level=1,
    )


@pytest.mark.parametrize(
    ("mode", "expected_mode", "score", "score_type", "embeddings_count"),
    [
        (SearchMode.BM25, "bm25", 0.8, "bm25", 0),
        (SearchMode.SEMANTIC, "semantic", 0.75, "certainty", 1),
        (SearchMode.HYBRID, "hybrid", 0.8, "hybrid", 1),
    ],
)
def test_weaviate_adapter_executes_all_retrieval_modes(
    mode,
    expected_mode,
    score,
    score_type,
    embeddings_count,
) -> None:
    query = Query((item(mode),))
    embeddings = Embeddings()
    adapter = WeaviateRetrievalAdapter(
        client=Client(query),
        collection_name="General",
        embeddings=embeddings,
    )

    hits = adapter.search(request(mode))

    assert hits[0].score == score
    assert hits[0].score_type == score_type
    assert hits[0].chunk.metadata["kind"] == "example"
    assert query.calls[0][0] == expected_mode
    assert len(embeddings.queries) == embeddings_count


def test_weaviate_health_checks_client_and_collection() -> None:
    query = Query()
    client = Client(query)
    adapter = WeaviateRetrievalAdapter(
        client=client,
        collection_name="General",
        embeddings=Embeddings(),
    )

    adapter.healthcheck()

    client.ready = False
    with pytest.raises(DependencyUnavailableError, match="not ready"):
        adapter.healthcheck()
    client.ready = True
    client.collections._exists = False
    with pytest.raises(DependencyUnavailableError, match="does not exist"):
        adapter.healthcheck()


def test_weaviate_health_probe_maps_client_exception() -> None:
    with pytest.raises(DependencyUnavailableError, match="unavailable"):
        WeaviateHealthProbe(Client(Query(), error=RuntimeError("down"))).check()


def test_weaviate_query_maps_provider_and_client_errors() -> None:
    adapter = WeaviateRetrievalAdapter(
        client=Client(Query(error=RuntimeError("down"))),
        collection_name="General",
        embeddings=Embeddings(),
    )
    with pytest.raises(DependencyUnavailableError, match="query failed"):
        adapter.search(request(SearchMode.BM25))

    class BrokenEmbeddings(Embeddings):
        def embed_query(self, text):
            raise ProviderProtocolError("bad embeddings")

    adapter = WeaviateRetrievalAdapter(
        client=Client(Query()),
        collection_name="General",
        embeddings=BrokenEmbeddings(),
    )
    with pytest.raises(ProviderProtocolError, match="bad embeddings"):
        adapter.search(request(SearchMode.SEMANTIC))


@pytest.mark.parametrize(
    "properties",
    [
        {},
        {
            "chunk_id": "c",
            "domain": "d",
            "tenant_id": "t",
            "source_id": "s",
            "text": "x",
            "metadata_json": "[]",
        },
        {
            "chunk_id": "c",
            "domain": "d",
            "tenant_id": "t",
            "source_id": "s",
            "text": "x",
            "metadata_json": "{",
        },
    ],
)
def test_weaviate_rejects_invalid_result_objects(properties) -> None:
    adapter = WeaviateRetrievalAdapter(
        client=Client(Query((item(SearchMode.BM25, properties=properties),))),
        collection_name="General",
        embeddings=Embeddings(),
    )

    with pytest.raises(ProviderProtocolError, match="invalid object"):
        adapter.search(request(SearchMode.BM25))


def test_weaviate_filter_supports_no_classification_limit() -> None:
    query = Query((item(SearchMode.BM25),))
    adapter = WeaviateRetrievalAdapter(
        client=Client(query),
        collection_name="General",
        embeddings=Embeddings(),
    )

    hits = adapter.search(
        replace(
            request(SearchMode.BM25),
            max_classification_level=None,
            filters={},
        )
    )

    assert len(hits) == 1


def test_weaviate_reports_missing_query_sdk(monkeypatch) -> None:
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "weaviate.classes.query":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    with pytest.raises(DependencyUnavailableError, match="not installed"):
        WeaviateRetrievalAdapter._metadata_query(SearchMode.BM25)
    with pytest.raises(DependencyUnavailableError, match="not installed"):
        WeaviateRetrievalAdapter._build_filter(request(SearchMode.BM25))
