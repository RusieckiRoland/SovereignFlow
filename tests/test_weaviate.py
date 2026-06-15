from __future__ import annotations

import builtins
from dataclasses import replace
from types import SimpleNamespace

import pytest

from sovereignflow.domain import (
    DependencyUnavailableError,
    DocumentChunk,
    IngestionCommand,
    IngestionJob,
    IngestionJobStatus,
    ProviderProtocolError,
    SearchMode,
    SearchRequest,
)
from sovereignflow.infrastructure.weaviate import (
    WeaviateCollectionMigrator,
    WeaviateHealthProbe,
    WeaviateRetrievalAdapter,
    WeaviateVectorIndex,
    _data_type_name,
    _tokenization_name,
)


class Embeddings:
    def __init__(self) -> None:
        self.queries = []

    def embed_query(self, text: str):
        self.queries.append(text)
        return (0.1, 0.2)

    def embed_documents(self, texts):
        return tuple((float(index), 0.2) for index, _ in enumerate(texts, start=1))

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
            allowed_acl_labels=(),
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


def ingestion_job() -> IngestionJob:
    command = IngestionCommand(
        idempotency_key="key-1",
        domain="general",
        tenant_id="tenant-a",
        source_id="source-1",
        source_version="v2",
        chunks=(
            DocumentChunk(
                chunk_id="chunk-1",
                domain="general",
                tenant_id="tenant-a",
                source_id="source-1",
                source_uri="https://example.test/1",
                text="first",
                metadata={"page": 1},
                acl_labels=("public",),
                classification_level=1,
            ),
            DocumentChunk(
                chunk_id="chunk-2",
                domain="general",
                tenant_id="tenant-a",
                source_id="source-1",
                text="second",
            ),
        ),
    )
    return IngestionJob(
        job_id="job-1",
        payload_hash="a" * 64,
        status=IngestionJobStatus.STAGED,
        command=command,
    )


class CollectionData:
    def __init__(self, existing=()) -> None:
        self.existing = set(existing)
        self.inserted = []
        self.replaced = []
        self.delete_filters = []

    def exists(self, object_uuid):
        return object_uuid in self.existing

    def insert(self, **kwargs):
        self.inserted.append(kwargs)

    def replace(self, **kwargs):
        self.replaced.append(kwargs)

    def delete_many(self, where):
        self.delete_filters.append(where)


class IndexQuery:
    def __init__(self, pages) -> None:
        self.pages = list(pages)
        self.calls = []

    def fetch_objects(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(objects=self.pages.pop(0))


class IndexCollections:
    def __init__(self, collection, *, exists=True, config=None) -> None:
        self.collection = collection
        self.exists_value = exists
        self.config_value = config
        self.created = []

    def exists(self, name):
        return self.exists_value

    def use(self, name):
        return self.collection

    def create(self, **kwargs):
        self.created.append(kwargs)


def test_collection_migrator_creates_and_verifies_exact_schema() -> None:
    collection = SimpleNamespace()
    collections = IndexCollections(collection, exists=False)
    WeaviateCollectionMigrator(SimpleNamespace(collections=collections)).ensure("General")
    assert collections.created[0]["name"] == "General"
    assert len(collections.created[0]["properties"]) == 11

    properties = [
        SimpleNamespace(name=name, data_type=[data_type], tokenization=tokenization)
        for name, (data_type, tokenization) in {
            "chunk_id": ("text", "field"),
            "domain": ("text", "field"),
            "tenant_id": ("text", "field"),
            "source_id": ("text", "field"),
            "source_version": ("text", "field"),
            "source_uri": ("text", "field"),
            "text": ("text", "word"),
            "metadata_json": ("text", "word"),
            "acl_labels": ("text_array", "field"),
            "acl_public": ("boolean", None),
            "classification_level": ("integer", None),
        }.items()
    ]
    configured = SimpleNamespace(
        config=SimpleNamespace(get=lambda: SimpleNamespace(properties=properties))
    )
    WeaviateCollectionMigrator(SimpleNamespace(collections=IndexCollections(configured))).ensure(
        "General"
    )


def test_collection_migrator_rejects_drift_and_maps_client_failure() -> None:
    drifted = SimpleNamespace(
        config=SimpleNamespace(
            get=lambda: SimpleNamespace(
                properties=[SimpleNamespace(name="chunk_id", data_type="text")]
            )
        )
    )
    with pytest.raises(DependencyUnavailableError, match="schema mismatch"):
        WeaviateCollectionMigrator(SimpleNamespace(collections=IndexCollections(drifted))).ensure(
            "General"
        )

    broken = SimpleNamespace(
        collections=SimpleNamespace(exists=lambda name: (_ for _ in ()).throw(RuntimeError("down")))
    )
    with pytest.raises(DependencyUnavailableError, match="migration failed"):
        WeaviateCollectionMigrator(broken).ensure("General")


def test_collection_migrator_reports_missing_sdk(monkeypatch) -> None:
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "weaviate.classes.config":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(DependencyUnavailableError, match="not installed"):
        WeaviateCollectionMigrator(SimpleNamespace()).ensure("General")


def test_vector_index_replaces_a_source_as_one_idempotent_set() -> None:
    job = ingestion_job()
    data = CollectionData()
    collection = SimpleNamespace(data=data)
    client = SimpleNamespace(collections=IndexCollections(collection))

    WeaviateVectorIndex(client=client, embeddings=Embeddings()).replace_source(
        job,
        collection_name="General",
    )

    assert len(data.replaced) == 0
    assert len(data.inserted) == 2
    assert data.inserted[0]["properties"]["source_version"] == "v2"
    assert data.inserted[0]["properties"]["acl_public"] is False
    assert data.inserted[1]["properties"]["acl_public"] is True
    assert len(data.delete_filters) == 1


def test_vector_index_validates_embedding_count_and_maps_failures() -> None:
    class WrongCount(Embeddings):
        def embed_documents(self, texts):
            return ()

    with pytest.raises(ProviderProtocolError, match="count"):
        WeaviateVectorIndex(
            client=SimpleNamespace(),
            embeddings=WrongCount(),
        ).replace_source(ingestion_job(), collection_name="General")

    collection = SimpleNamespace(
        data=SimpleNamespace(delete_many=lambda value: (_ for _ in ()).throw(RuntimeError("down"))),
        query=IndexQuery([[]]),
    )
    with pytest.raises(DependencyUnavailableError, match="replacement failed"):
        WeaviateVectorIndex(
            client=SimpleNamespace(collections=IndexCollections(collection)),
            embeddings=Embeddings(),
        ).replace_source(ingestion_job(), collection_name="General")

    unavailable = SimpleNamespace(
        data=SimpleNamespace(
            delete_many=lambda value: (_ for _ in ()).throw(
                DependencyUnavailableError("unavailable")
            )
        ),
        query=IndexQuery([[]]),
    )
    with pytest.raises(DependencyUnavailableError, match="unavailable"):
        WeaviateVectorIndex(
            client=SimpleNamespace(collections=IndexCollections(unavailable)),
            embeddings=Embeddings(),
        ).replace_source(ingestion_job(), collection_name="General")

    class ProviderFailure(Embeddings):
        def embed_documents(self, texts):
            raise ProviderProtocolError("invalid embeddings")

    with pytest.raises(ProviderProtocolError, match="invalid embeddings"):
        WeaviateVectorIndex(
            client=SimpleNamespace(),
            embeddings=ProviderFailure(),
        ).replace_source(ingestion_job(), collection_name="General")


def test_vector_index_reports_missing_sdk(monkeypatch) -> None:
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "weaviate.classes.query":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(DependencyUnavailableError, match="not installed"):
        WeaviateVectorIndex(
            client=SimpleNamespace(),
            embeddings=Embeddings(),
        ).replace_source(ingestion_job(), collection_name="General")
    index = WeaviateVectorIndex(client=SimpleNamespace(), embeddings=Embeddings())
    with pytest.raises(DependencyUnavailableError, match="not installed"):
        index.delete_source(
            collection_name="General",
            domain="general",
            tenant_id="tenant-a",
            source_id="source-1",
        )
    with pytest.raises(DependencyUnavailableError, match="not installed"):
        index.count(
            collection_name="General",
            domain="general",
            tenant_id="tenant-a",
        )


def test_vector_index_deletes_and_counts_selected_boundary() -> None:
    data = CollectionData()
    aggregate = SimpleNamespace(over_all=lambda **kwargs: SimpleNamespace(total_count=7))
    collection = SimpleNamespace(data=data, aggregate=aggregate)
    index = WeaviateVectorIndex(
        client=SimpleNamespace(collections=IndexCollections(collection)),
        embeddings=Embeddings(),
    )

    index.delete_source(
        collection_name="General",
        domain="general",
        tenant_id="tenant-a",
        source_id="source-1",
    )
    count = index.count(
        collection_name="General",
        domain="general",
        tenant_id="tenant-a",
    )

    assert len(data.delete_filters) == 1
    assert count == 7


def test_vector_index_maps_delete_and_count_failures() -> None:
    collection = SimpleNamespace(
        data=SimpleNamespace(delete_many=lambda value: (_ for _ in ()).throw(RuntimeError("down"))),
        aggregate=SimpleNamespace(
            over_all=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
        ),
    )
    index = WeaviateVectorIndex(
        client=SimpleNamespace(collections=IndexCollections(collection)),
        embeddings=Embeddings(),
    )

    with pytest.raises(DependencyUnavailableError, match="deletion failed"):
        index.delete_source(
            collection_name="General",
            domain="general",
            tenant_id="tenant-a",
            source_id="source-1",
        )
    with pytest.raises(DependencyUnavailableError, match="count failed"):
        index.count(
            collection_name="General",
            domain="general",
            tenant_id="tenant-a",
        )


def test_weaviate_data_type_normalization_is_strict() -> None:
    assert _data_type_name(["text"]) == "text"
    assert _data_type_name(["text", "int"]) == "invalid"
    assert _data_type_name(SimpleNamespace(value="TEXT_ARRAY")) == "text[]"
    assert _data_type_name("custom") == "custom"
    assert _tokenization_name(SimpleNamespace(value="FIELD")) == "field"
    assert _tokenization_name(None) is None
