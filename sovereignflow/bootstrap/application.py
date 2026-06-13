from __future__ import annotations

import atexit
from dataclasses import dataclass
from typing import Any

from flask import Flask

from sovereignflow.application import (
    HealthProbe,
    PipelineEngine,
    PipelineValidator,
    RagQueryService,
    default_action_registry,
)
from sovereignflow.domain import DependencyUnavailableError
from sovereignflow.infrastructure import (
    EmbeddingEndpoint,
    FilePromptRepository,
    ModelEndpoint,
    OpenAIEmbeddingGateway,
    OpenAIModelGateway,
    PostgreSQLExecutionAudit,
    PostgreSQLHealthProbe,
    WeaviateHealthProbe,
    WeaviateRetrievalAdapter,
    YamlPipelineRepository,
)
from sovereignflow.interfaces import QueryDispatcher, create_app

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

    def close(self) -> None:
        self.weaviate_client.close()


def bootstrap(settings: SovereignFlowSettings) -> BootstrappedApplication:
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
        )
    )
    prompts = FilePromptRepository(settings.prompts_root)
    pipelines = YamlPipelineRepository(settings.pipelines_root)
    audit = PostgreSQLExecutionAudit(
        settings.postgresql.connection_url,
        timeout_seconds=settings.postgresql.timeout_seconds,
    )
    audit.migrate()
    registry = default_action_registry()
    validator = PipelineValidator(registry)
    engine = PipelineEngine(registry=registry, audit=audit)
    client = _connect_weaviate(settings)
    try:
        services: dict[str, RagQueryService] = {}
        retrieval_probes: list[HealthProbe] = []
        for domain in settings.domains:
            pipeline = pipelines.load(domain.pipeline_name)
            validator.validate(pipeline)
            retrieval = WeaviateRetrievalAdapter(
                client=client,
                collection_name=domain.collection,
                embeddings=embeddings,
            )
            retrieval.healthcheck()
            prompts.load(domain.prompt_name)
            services[domain.name] = RagQueryService(
                domain=domain,
                retrieval=retrieval,
                model=model,
                prompts=prompts,
                pipeline=pipeline,
                engine=engine,
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
            WeaviateHealthProbe(client),
            _GatewayHealthProbe(name="embeddings", gateway=embeddings),
            _GatewayHealthProbe(name="model", gateway=model),
            *retrieval_probes,
        )
        for probe in probes:
            probe.check()
        application = BootstrappedApplication(
            app=create_app(QueryDispatcher(services), probes),
            weaviate_client=client,
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
