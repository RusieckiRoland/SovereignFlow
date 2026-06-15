from __future__ import annotations

import atexit
from dataclasses import dataclass
from typing import Any

from flask import Flask

from sovereignflow.application import (
    DocumentIngestionService,
    HealthProbe,
    OperationsService,
    PipelineAuthorizationService,
    PipelineEngine,
    PipelineValidator,
    PolicyAdministrationService,
    RagQueryService,
    default_action_registry,
)
from sovereignflow.domain import DependencyUnavailableError
from sovereignflow.infrastructure import (
    EmbeddingEndpoint,
    FilePromptRepository,
    ModelEndpoint,
    OidcJwtAuthenticator,
    OidcSettings,
    OpenAIEmbeddingGateway,
    OpenAIModelGateway,
    PostgreSQLAccessPolicyRepository,
    PostgreSQLExecutionAudit,
    PostgreSQLGraphTraversal,
    PostgreSQLHealthProbe,
    PostgreSQLIngestionRepository,
    PostgreSQLMigrationRunner,
    PostgreSQLSecurityDecisionAudit,
    WeaviateCollectionMigrator,
    WeaviateHealthProbe,
    WeaviateRetrievalAdapter,
    WeaviateVectorIndex,
    YamlPipelineRepository,
)
from sovereignflow.interfaces import QueryDispatcher, WebClientConfiguration, create_app

from .config import SovereignFlowSettings


@dataclass
class _GatewayHealthProbe:
    name: str
    gateway: Any

    def check(self) -> None:
        self.gateway.healthcheck()


@dataclass
class BootstrappedApplication:
    app: Flask
    weaviate_client: Any
    ingestion_services: dict[str, DocumentIngestionService]

    def close(self) -> None:
        self.weaviate_client.close()


def bootstrap(settings: SovereignFlowSettings) -> BootstrappedApplication:
    identity = settings.identity_provider
    authenticator = OidcJwtAuthenticator(
        OidcSettings(
            issuer=identity.issuer,
            audience=identity.audience,
            jwks_url=identity.jwks_url,
            algorithms=identity.algorithms,
            timeout_seconds=identity.timeout_seconds,
            cache_ttl_seconds=identity.cache_ttl_seconds,
            tenant_claim=identity.tenant_claim,
            roles_claim=identity.roles_claim,
            groups_claim=identity.groups_claim,
            acl_claim=identity.acl_claim,
            classification_claim=identity.classification_claim,
            external_model_claim=identity.external_model_claim,
            diagnostic_claim=identity.diagnostic_claim,
        )
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
    selected = settings.selected_model
    model = OpenAIModelGateway(
        ModelEndpoint(
            name=selected.name,
            scope=selected.scope,
            base_url=selected.base_url,
            model=selected.model,
            api_key=selected.api_key,
            timeout_seconds=selected.timeout_seconds,
            input_cost_per_million=selected.input_cost_per_million,
            output_cost_per_million=selected.output_cost_per_million,
        )
    )
    prompts = FilePromptRepository(settings.prompts_root)
    pipelines = YamlPipelineRepository(settings.pipelines_root)
    PostgreSQLMigrationRunner(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    ).migrate()
    audit = PostgreSQLExecutionAudit(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    )
    ingestion_repository = PostgreSQLIngestionRepository(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    )
    graph = PostgreSQLGraphTraversal(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    )
    access_policies = PostgreSQLAccessPolicyRepository(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    )
    registry = default_action_registry()
    validator = PipelineValidator(registry)
    engine = PipelineEngine(registry=registry, audit=audit)
    client = _connect_weaviate(settings)
    try:
        services: dict[tuple[str, str], RagQueryService] = {}
        ingestion_services: dict[str, DocumentIngestionService] = {}
        retrieval_probes: list[HealthProbe] = []
        collection_migrator = WeaviateCollectionMigrator(client)
        vector_index = WeaviateVectorIndex(client=client, embeddings=embeddings)
        for domain in settings.domains:
            collection_migrator.ensure(domain.collection)
            retrieval = WeaviateRetrievalAdapter(
                client=client,
                collection_name=domain.collection,
                embeddings=embeddings,
            )
            retrieval.healthcheck()
            prompts.load(domain.prompt_name)
            for pipeline_name in domain.allowed_pipeline_names:
                pipeline = pipelines.load(pipeline_name)
                validator.validate(pipeline)
                services[(domain.name, pipeline_name)] = RagQueryService(
                    domain=domain,
                    retrieval=retrieval,
                    graph=graph,
                    model=model,
                    prompts=prompts,
                    pipeline=pipeline,
                    engine=engine,
                )
            ingestion_services[domain.name] = DocumentIngestionService(
                domain=domain,
                repository=ingestion_repository,
                vector_index=vector_index,
            )
            retrieval_probes.append(
                _GatewayHealthProbe(
                    name=f"retrieval:{domain.name}",
                    gateway=retrieval,
                )
            )

        probes: tuple[HealthProbe, ...] = (
            PostgreSQLHealthProbe(
                settings.postgresql.connection_url,
                timeout_seconds=settings.postgresql.timeout_seconds,
            ),
            audit,
            ingestion_repository,
            graph,
            access_policies,
            WeaviateHealthProbe(client),
            _GatewayHealthProbe(name="embeddings", gateway=embeddings),
            _GatewayHealthProbe(name="model", gateway=model),
            *retrieval_probes,
        )
        for probe in probes:
            probe.check()
        application = BootstrappedApplication(
            app=create_app(
                QueryDispatcher(
                    services,
                    PipelineAuthorizationService(
                        access_policies,
                        PostgreSQLSecurityDecisionAudit(
                            settings.postgresql.connection_url,
                            timeout_seconds=settings.postgresql.timeout_seconds,
                        ),
                    ),
                    default_pipelines={
                        domain.name: domain.pipeline_name for domain in settings.domains
                    },
                ),
                probes,
                OperationsService(
                    audit=audit,
                    ingestion_repository=ingestion_repository,
                    ingestion_services=ingestion_services,
                ),
                settings.admin.api_key,
                authenticator,
                (
                    WebClientConfiguration(
                        client_id=settings.web_client.client_id,
                        authorization_url=settings.web_client.authorization_url,
                        token_url=settings.web_client.token_url,
                        logout_url=settings.web_client.logout_url,
                    )
                    if settings.web_client is not None
                    else None
                ),
                PolicyAdministrationService(
                    access_policies,
                    domain_pipelines={
                        domain.name: domain.allowed_pipeline_names for domain in settings.domains
                    },
                ),
            ),
            weaviate_client=client,
            ingestion_services=ingestion_services,
        )
        atexit.register(application.close)
        return application
    except Exception:
        client.close()
        raise


def _connect_weaviate(settings: SovereignFlowSettings) -> Any:
    try:
        import weaviate
        from weaviate.classes.init import Auth
    except ImportError as exc:
        raise DependencyUnavailableError("weaviate-client is not installed") from exc
    try:
        return weaviate.connect_to_custom(
            http_host=settings.weaviate.host,
            http_port=settings.weaviate.http_port,
            http_secure=settings.weaviate.secure,
            grpc_host=settings.weaviate.host,
            grpc_port=settings.weaviate.grpc_port,
            grpc_secure=settings.weaviate.secure,
            auth_credentials=Auth.api_key(settings.weaviate.api_key),
        )
    except Exception as exc:
        raise DependencyUnavailableError("Cannot connect to Weaviate") from exc
