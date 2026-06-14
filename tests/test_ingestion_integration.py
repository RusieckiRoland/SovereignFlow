from __future__ import annotations

import os
import uuid

import pytest

from sovereignflow.application import DocumentIngestionService
from sovereignflow.domain import (
    DocumentChunk,
    DomainProfile,
    IngestionCommand,
    RetrievalProfile,
    SearchMode,
    SearchRequest,
)
from sovereignflow.infrastructure import (
    PostgreSQLIngestionRepository,
    PostgreSQLMigrationRunner,
    WeaviateCollectionMigrator,
    WeaviateRetrievalAdapter,
    WeaviateVectorIndex,
)


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

        with psycopg.connect(postgres_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_version
                FROM ingestion.sources
                WHERE tenant_id = %s AND domain = %s AND source_id = %s
                """,
                (tenant_id, domain_name, source_id),
            )
            assert cursor.fetchone() == ("v2",)
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
) -> IngestionCommand:
    return IngestionCommand(
        idempotency_key=f"{identity}-{source_id}",
        domain=domain,
        tenant_id=tenant_id,
        source_id=source_id,
        source_version=version,
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
