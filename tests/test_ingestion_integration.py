from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from sovereignflow.application import DatasetImportService, DocumentIngestionService
from sovereignflow.domain import (
    DatasetImportStatus,
    DependencyUnavailableError,
    DocumentChunk,
    DomainProfile,
    GraphDirection,
    GraphNodeRef,
    GraphRelationship,
    GraphTraversalProfile,
    GraphTraversalRequest,
    IngestionCommand,
    RetrievalProfile,
    SearchHit,
    SearchMode,
    SearchRequest,
)
from sovereignflow.infrastructure import (
    EmbeddingEndpoint,
    JsonlDatasetReader,
    OpenAIEmbeddingGateway,
    PostgreSQLGraphTraversal,
    PostgreSQLIngestionRepository,
    PostgreSQLMigrationRunner,
    RelationshipScope,
    WeaviateCollectionMigrator,
    WeaviateRetrievalAdapter,
    WeaviateVectorIndex,
)


def embedding_base_url(server) -> str:
    return f"http://127.0.0.1:{server.server_port}/v1"


class DeterministicEmbeddings:
    def embed_query(self, text: str):
        return (1.0, 0.0, 0.0)

    def embed_documents(self, texts):
        return tuple((1.0, 0.0, 0.0) for _ in texts)


@pytest.mark.integration
def test_document_ingestion_round_trip_across_postgresql_and_weaviate() -> None:
    postgres_url = os.getenv("SOVEREIGNFLOW_TEST_POSTGRES_URL")
    weaviate_host = os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_HOST")
    weaviate_api_key = os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_API_KEY")
    if not postgres_url or not weaviate_host or not weaviate_api_key:
        pytest.skip("Stage 3 integration services are not configured")

    import psycopg
    import weaviate
    from weaviate.classes.init import Auth

    collection_name = "Stage3IngestionIntegration"
    identity = uuid.uuid4().hex
    domain_name = f"domain-{identity}"
    tenant_id = f"tenant-{identity}"
    source_id = f"source-{identity}"
    embeddings = DeterministicEmbeddings()
    client = weaviate.connect_to_custom(
        http_host=weaviate_host,
        http_port=int(os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_HTTP_PORT", "8080")),
        http_secure=False,
        grpc_host=weaviate_host,
        grpc_port=int(os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_GRPC_PORT", "50051")),
        grpc_secure=False,
        auth_credentials=Auth.api_key(weaviate_api_key),
    )
    try:
        if client.collections.exists(collection_name):
            client.collections.delete(collection_name)
        PostgreSQLMigrationRunner(postgres_url, timeout_seconds=5).migrate()
        WeaviateCollectionMigrator(client).ensure(collection_name)
        repository = PostgreSQLIngestionRepository(postgres_url, timeout_seconds=5)
        profile = DomainProfile(
            name=domain_name,
            description="",
            collection=collection_name,
            tenant_id=tenant_id,
            prompt_name="answer",
            allow_external_model=False,
            retrieval=RetrievalProfile(SearchMode.SEMANTIC, 10, 1000),
            graph=GraphTraversalProfile(False, 1, 1, GraphDirection.BOTH),
            allowed_acl_labels=("public",),
            max_classification_level=1,
        )
        service = DocumentIngestionService(
            domain=profile,
            repository=repository,
            vector_index=WeaviateVectorIndex(client=client, embeddings=embeddings),
        )

        first = ingestion_command(
            identity="first",
            domain=domain_name,
            tenant_id=tenant_id,
            source_id=source_id,
            version="v1",
            chunk_ids=("chunk-1", "chunk-obsolete"),
        )
        first_result = service.ingest(first)
        assert service.ingest(first).job_id == first_result.job_id

        second = ingestion_command(
            identity="second",
            domain=domain_name,
            tenant_id=tenant_id,
            source_id=source_id,
            version="v2",
            chunk_ids=("chunk-1",),
        )
        service.ingest(second)

        retrieval = WeaviateRetrievalAdapter(
            client=client,
            collection_name=collection_name,
            embeddings=embeddings,
        )
        hits = retrieval.search(
            SearchRequest(
                query="current content",
                domain=domain_name,
                tenant_id=tenant_id,
                top_k=10,
                mode=SearchMode.SEMANTIC,
                filters={},
                allowed_acl_labels=("public",),
                max_classification_level=1,
            )
        )
        assert [hit.chunk.chunk_id for hit in hits] == ["chunk-1"]
        assert hits[0].chunk.text == "content-v2-chunk-1"

        target_source_id = f"target-{identity}"
        service.ingest(
            ingestion_command(
                identity="target",
                domain=domain_name,
                tenant_id=tenant_id,
                source_id=target_source_id,
                version="v1",
                chunk_ids=("target-chunk",),
            )
        )
        source_v3 = ingestion_command(
            identity="third",
            domain=domain_name,
            tenant_id=tenant_id,
            source_id=source_id,
            version="v3",
            chunk_ids=("chunk-1",),
            relationships=(
                GraphRelationship(
                    GraphNodeRef(source_id, "chunk-1"),
                    GraphNodeRef(target_source_id, "target-chunk"),
                    "references",
                ),
            ),
        )
        service.ingest(source_v3)
        graph_hits = PostgreSQLGraphTraversal(
            postgres_url,
            timeout_seconds=5,
        ).expand(
            GraphTraversalRequest(
                seeds=(
                    SearchHit(
                        source_v3.chunks[0],
                        0.9,
                        "semantic",
                    ),
                ),
                domain=domain_name,
                tenant_id=tenant_id,
                max_depth=2,
                max_nodes=10,
                direction=GraphDirection.OUTGOING,
                relationship_types=("references",),
                allowed_acl_labels=("public",),
                max_classification_level=1,
            )
        )
        assert [item.chunk.source_id for item in graph_hits] == [target_source_id]
        assert graph_hits[0].chunk.metadata["graph_depth"] == 1

        with psycopg.connect(postgres_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_version
                FROM ingestion.sources
                WHERE tenant_id = %s AND domain = %s AND source_id = %s
                """,
                (tenant_id, domain_name, source_id),
            )
            assert cursor.fetchone() == ("v3",)
    finally:
        if client.collections.exists(collection_name):
            client.collections.delete(collection_name)
        client.close()


@pytest.mark.integration
def test_dataset_import_full_lifecycle_across_real_adapters(
    tmp_path: Path,
    http_server,
) -> None:
    postgres_url = os.getenv("SOVEREIGNFLOW_TEST_POSTGRES_URL")
    weaviate_host = os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_HOST")
    weaviate_api_key = os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_API_KEY")
    if not postgres_url or not weaviate_host or not weaviate_api_key:
        pytest.skip("Stage 1 integration services are not configured")

    import weaviate
    from weaviate.classes.init import Auth

    embedding_success = (
        200,
        {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]},
        "application/json",
    )
    identity = uuid.uuid4().hex
    collection_name = f"Stage1Dataset{identity[:12]}"
    domain_name = f"neutral-{identity}"
    tenant_id = f"tenant-{identity}"
    source_a = f"source-a-{identity}"
    source_b = f"source-b-{identity}"
    chunk_a_v1 = f"chunk-a-v1-{identity}"
    chunk_a_v2 = f"chunk-a-v2-{identity}"
    chunk_b_v1 = f"chunk-b-v1-{identity}"
    client = weaviate.connect_to_custom(
        http_host=weaviate_host,
        http_port=int(os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_HTTP_PORT", "8080")),
        http_secure=False,
        grpc_host=weaviate_host,
        grpc_port=int(os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_GRPC_PORT", "50051")),
        grpc_secure=False,
        auth_credentials=Auth.api_key(weaviate_api_key),
    )
    try:
        PostgreSQLMigrationRunner(postgres_url, timeout_seconds=5).migrate()
        WeaviateCollectionMigrator(client).ensure(collection_name)
        repository = PostgreSQLIngestionRepository(postgres_url, timeout_seconds=5)
        embeddings = OpenAIEmbeddingGateway(
            EmbeddingEndpoint(
                "integration-embeddings",
                embedding_base_url(http_server),
                "integration-vector",
                "secret",
                2,
            )
        )
        vector_index = WeaviateVectorIndex(client=client, embeddings=embeddings)
        profile = DomainProfile(
            name=domain_name,
            description="Neutral integration boundary",
            collection=collection_name,
            tenant_id=tenant_id,
            prompt_name="answer",
            allow_external_model=False,
            retrieval=RetrievalProfile(SearchMode.SEMANTIC, 10, 1000),
            graph=GraphTraversalProfile(True, 4, 20, GraphDirection.BOTH),
            allowed_acl_labels=("public", "internal"),
            max_classification_level=2,
        )
        service = DatasetImportService(
            domain=profile,
            repository=repository,
            vector_index=vector_index,
        )

        initial = dataset_reader(
            tmp_path / "initial",
            import_id=f"initial-{identity}",
            domain=domain_name,
            tenant_id=tenant_id,
            nodes=(
                dataset_node(source_a, "v1", chunk_a_v1, domain_name, tenant_id),
                dataset_node(source_b, "v1", chunk_b_v1, domain_name, tenant_id),
            ),
            edges=(
                dataset_edge(
                    source_a,
                    "v1",
                    chunk_a_v1,
                    source_b,
                    "v1",
                    chunk_b_v1,
                    tenant_id,
                ),
                dataset_edge(
                    source_b,
                    "v1",
                    chunk_b_v1,
                    source_a,
                    "v1",
                    chunk_a_v1,
                    tenant_id,
                ),
            ),
            operations=(
                dataset_add(source_a, "v1", domain_name, tenant_id),
                dataset_add(source_b, "v1", domain_name, tenant_id),
            ),
        )
        http_server.responses[("POST", "/v1/embeddings")] = (
            503,
            {"error": "temporarily unavailable"},
            "application/json",
        )
        with pytest.raises(DependencyUnavailableError):
            service.execute(initial)
        failed_run = service.status(f"initial-{identity}")
        assert failed_run.status == DatasetImportStatus.FAILED
        assert failed_run.error_code == "dependency_unavailable"

        http_server.responses[("POST", "/v1/embeddings")] = embedding_success
        first_run = service.execute(initial)
        assert first_run.status == DatasetImportStatus.COMPLETED
        assert first_run.indexed_sources == 2
        assert first_run.published_relationships == 2
        assert service.execute(initial) == first_run
        assert service.consistency().consistent

        graph_hits = PostgreSQLGraphTraversal(postgres_url, timeout_seconds=5).expand(
            GraphTraversalRequest(
                seeds=(
                    SearchHit(
                        dataset_chunk(
                            source_a,
                            chunk_a_v1,
                            domain_name,
                            tenant_id,
                            "content v1",
                        ),
                        1.0,
                        "semantic",
                    ),
                ),
                domain=domain_name,
                tenant_id=tenant_id,
                max_depth=2,
                max_nodes=10,
                direction=GraphDirection.OUTGOING,
                relationship_types=("references",),
                allowed_acl_labels=("public",),
                max_classification_level=2,
            )
        )
        assert [hit.chunk.source_id for hit in graph_hits] == [source_b]

        replacement = dataset_reader(
            tmp_path / "replacement",
            import_id=f"replacement-{identity}",
            domain=domain_name,
            tenant_id=tenant_id,
            nodes=(
                dataset_node(source_a, "v1", chunk_a_v1, domain_name, tenant_id),
                dataset_node(source_a, "v2", chunk_a_v2, domain_name, tenant_id),
                dataset_node(source_b, "v1", chunk_b_v1, domain_name, tenant_id),
            ),
            edges=(
                dataset_edge(
                    source_a,
                    "v2",
                    chunk_a_v2,
                    source_b,
                    "v1",
                    chunk_b_v1,
                    tenant_id,
                ),
            ),
            operations=(
                dataset_replace(source_a, "v1", "v2", domain_name, tenant_id),
                dataset_add(source_b, "v1", domain_name, tenant_id),
            ),
        )
        replacement_run = service.execute(replacement)
        assert replacement_run.status == DatasetImportStatus.COMPLETED
        replacement_report = service.consistency()
        indexed_objects = [
            dict(item.properties) for item in client.collections.use(collection_name).iterator()
        ]
        assert replacement_report.consistent, indexed_objects

        deletion = dataset_reader(
            tmp_path / "deletion",
            import_id=f"deletion-{identity}",
            domain=domain_name,
            tenant_id=tenant_id,
            nodes=(dataset_node(source_a, "v2", chunk_a_v2, domain_name, tenant_id),),
            edges=(),
            operations=(
                dataset_add(source_a, "v2", domain_name, tenant_id),
                dataset_delete(source_b, domain_name, tenant_id),
            ),
        )
        deletion_run = service.execute(deletion)
        assert deletion_run.status == DatasetImportStatus.COMPLETED
        assert deletion_run.deleted_sources == 1
        report = service.consistency()
        assert report.consistent
        assert (report.active_sources, report.active_chunks, report.indexed_chunks) == (1, 1, 1)
        assert report.active_relationships == 0
        assert (
            len(
                [
                    request
                    for request in http_server.requests
                    if request[0] == "POST" and request[1] == "/v1/embeddings"
                ]
            )
            == 4
        )
    finally:
        if client.collections.exists(collection_name):
            client.collections.delete(collection_name)
        client.close()


def ingestion_command(
    *,
    identity: str,
    domain: str,
    tenant_id: str,
    source_id: str,
    version: str,
    chunk_ids: tuple[str, ...],
    relationships: tuple[GraphRelationship, ...] = (),
) -> IngestionCommand:
    return IngestionCommand(
        idempotency_key=f"{identity}-{source_id}",
        domain=domain,
        tenant_id=tenant_id,
        source_id=source_id,
        source_version=version,
        relationships=relationships,
        chunks=tuple(
            DocumentChunk(
                chunk_id=chunk_id,
                domain=domain,
                tenant_id=tenant_id,
                source_id=source_id,
                text=f"content-{version}-{chunk_id}",
                acl_labels=("public",),
                classification_level=1,
            )
            for chunk_id in chunk_ids
        ),
    )


def dataset_reader(
    root: Path,
    *,
    import_id: str,
    domain: str,
    tenant_id: str,
    nodes: tuple[dict, ...],
    edges: tuple[dict, ...],
    operations: tuple[dict, ...],
) -> JsonlDatasetReader:
    root.mkdir()
    nodes_path = root / "nodes.jsonl"
    edges_path = root / "edges.jsonl"
    operations_path = root / "operations.jsonl"
    write_jsonl(nodes_path, nodes)
    write_jsonl(edges_path, edges)
    write_jsonl(operations_path, operations)
    return JsonlDatasetReader(
        import_id=import_id,
        nodes_path=nodes_path,
        edges_path=edges_path,
        operations_path=operations_path,
        workspace_path=root / "workspace.sqlite",
        relationship_scope=RelationshipScope.COMPLETE,
    )


def write_jsonl(path: Path, records: tuple[dict, ...]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def dataset_chunk(
    source_id: str,
    chunk_id: str,
    domain: str,
    tenant_id: str,
    text: str,
) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=chunk_id,
        domain=domain,
        tenant_id=tenant_id,
        source_id=source_id,
        source_uri=f"test://{source_id}/{chunk_id}",
        text=text,
        metadata={"kind": "neutral"},
        acl_labels=("public",),
        classification_level=1,
    )


def dataset_node(
    source_id: str,
    version: str,
    chunk_id: str,
    domain: str,
    tenant_id: str,
) -> dict:
    chunk = dataset_chunk(
        source_id,
        chunk_id,
        domain,
        tenant_id,
        f"content {version}",
    )
    return {
        "chunk_id": chunk.chunk_id,
        "domain": chunk.domain,
        "tenant_id": chunk.tenant_id,
        "source_id": chunk.source_id,
        "source_version": version,
        "source_uri": chunk.source_uri,
        "text": chunk.text,
        "metadata": dict(chunk.metadata),
        "acl_labels": list(chunk.acl_labels),
        "classification_level": chunk.classification_level,
    }


def dataset_edge(
    owner_source_id: str,
    owner_version: str,
    from_chunk_id: str,
    target_source_id: str,
    target_version: str,
    to_chunk_id: str,
    tenant_id: str,
) -> dict:
    return {
        "tenant_id": tenant_id,
        "owner_source_id": owner_source_id,
        "owner_source_version": owner_version,
        "from_source_id": owner_source_id,
        "from_source_version": owner_version,
        "from_chunk_id": from_chunk_id,
        "to_source_id": target_source_id,
        "to_source_version": target_version,
        "to_chunk_id": to_chunk_id,
        "relationship_type": "references",
        "metadata": {"weight": 1.0},
    }


def dataset_add(source_id: str, version: str, domain: str, tenant_id: str) -> dict:
    return {
        "operation": "add_source",
        "domain": domain,
        "tenant_id": tenant_id,
        "source_id": source_id,
        "source_version": version,
    }


def dataset_replace(
    source_id: str,
    from_version: str,
    to_version: str,
    domain: str,
    tenant_id: str,
) -> dict:
    return {
        "operation": "replace_source",
        "domain": domain,
        "tenant_id": tenant_id,
        "source_id": source_id,
        "from_version": from_version,
        "to_version": to_version,
    }


def dataset_delete(source_id: str, domain: str, tenant_id: str) -> dict:
    return {
        "operation": "delete_source",
        "domain": domain,
        "tenant_id": tenant_id,
        "source_id": source_id,
    }
