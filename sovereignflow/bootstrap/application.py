from __future__ import annotations

import atexit
from dataclasses import dataclass
from typing import Any

from flask import Flask

from sovereignflow.application import (
    ConversationHistoryService,
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
from sovereignflow.application.pipeline import ModelServerRuntime
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
    PostgreSQLConversationHistory,
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
            clearance_claim=identity.clearance_claim,
            classification_labels_claim=identity.classification_labels_claim,
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
    model_servers = {
        server.server_id: ModelServerRuntime(
            definition=server.definition,
            gateway=OpenAIModelGateway(
                ModelEndpoint(
                    name=server.server_id,
                    scope=server.trust_boundary.value,
                    base_url=server.base_url,
                    model=server.model,
                    api_key=server.api_key,
                    timeout_seconds=server.timeout_seconds,
                    input_cost_per_million=server.input_cost_per_million,
                    output_cost_per_million=server.output_cost_per_million,
                )
            ),
        )
        for server in settings.model_servers
    }
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
    conversation_history_repository = PostgreSQLConversationHistory(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    )
    conversation_history = ConversationHistoryService(conversation_history_repository)
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
            for pipeline_name in domain.allowed_pipeline_names:
                pipeline = pipelines.load(pipeline_name)
                validator.validate(pipeline)
                services[(domain.name, pipeline_name)] = RagQueryService(
                    domain=domain,
                    retrieval=retrieval,
                    graph=graph,
                    model_servers=model_servers,
                    prompts=prompts,
                    pipeline=pipeline,
                    engine=engine,
                    conversation_history=conversation_history,
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
            conversation_history_repository,
            access_policies,
            WeaviateHealthProbe(client),
            _GatewayHealthProbe(name="embeddings", gateway=embeddings),
            *(
                _GatewayHealthProbe(name=f"model:{server_id}", gateway=runtime.gateway)
                for server_id, runtime in model_servers.items()
            ),
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
                conversation_history,
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
