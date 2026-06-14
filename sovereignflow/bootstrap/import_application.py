from __future__ import annotations

from dataclasses import dataclass

from sovereignflow.application import DatasetImportService
from sovereignflow.domain import DomainNotFoundError
from sovereignflow.infrastructure import (
    EmbeddingEndpoint,
    OpenAIEmbeddingGateway,
    PostgreSQLIngestionRepository,
    PostgreSQLMigrationRunner,
    WeaviateCollectionMigrator,
    WeaviateVectorIndex,
)

from .application import _connect_weaviate
from .config import SovereignFlowSettings


@dataclass
class BootstrappedImportApplication:
    service: DatasetImportService
    weaviate_client: object

    def close(self) -> None:
        self.weaviate_client.close()


def bootstrap_import(
    settings: SovereignFlowSettings,
    *,
    domain_name: str,
) -> BootstrappedImportApplication:
    domain = next(
        (candidate for candidate in settings.domains if candidate.name == domain_name),
        None,
    )
    if domain is None:
        raise DomainNotFoundError(f"Unknown domain: {domain_name}")
    PostgreSQLMigrationRunner(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    ).migrate()
    repository = PostgreSQLIngestionRepository(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    )
    embeddings = OpenAIEmbeddingGateway(
        EmbeddingEndpoint(
            name=settings.embeddings.name,
            base_url=settings.embeddings.base_url,
            model=settings.embeddings.model,
            api_key=settings.embeddings.api_key,
            timeout_seconds=settings.embeddings.timeout_seconds,
        )
    )
    client = _connect_weaviate(settings)
    try:
        WeaviateCollectionMigrator(client).ensure(domain.collection)
        vector_index = WeaviateVectorIndex(client=client, embeddings=embeddings)
        return BootstrappedImportApplication(
            service=DatasetImportService(
                domain=domain,
                repository=repository,
                vector_index=vector_index,
            ),
            weaviate_client=client,
        )
    except Exception:
        client.close()
        raise
