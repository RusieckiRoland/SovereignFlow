from __future__ import annotations

from dataclasses import replace

from sovereignflow.domain import (
    AuthorizationContext,
    DomainProfile,
    PipelineDefinition,
    PolicyViolationError,
    QueryCommand,
    QueryResult,
)

from .pipeline import ModelServerRuntime, PipelineContext, PipelineEngine
from .ports import ConversationHistoryPort, GraphTraversalPort, PromptRepositoryPort, RetrievalPort


class RagQueryService:
    def __init__(
        self,
        *,
        domain: DomainProfile,
        retrieval: RetrievalPort,
        graph: GraphTraversalPort,
        model_servers: dict[str, ModelServerRuntime],
        prompts: PromptRepositoryPort,
        pipeline: PipelineDefinition,
        engine: PipelineEngine,
        conversation_history: ConversationHistoryPort | None = None,
    ) -> None:
        if not model_servers:
            raise PolicyViolationError("At least one model server must be configured")
        self._domain = domain
        self._retrieval = retrieval
        self._graph = graph
        self._model_servers = dict(model_servers)
        self._prompts = prompts
        self._pipeline = pipeline
        self._engine = engine
        self._conversation_history = conversation_history

    @property
    def domain_name(self) -> str:
        return self._domain.name

    def execute(self, command: QueryCommand) -> QueryResult:
        if command.domain != self._domain.name:
            raise PolicyViolationError(
                f"Query domain '{command.domain}' does not match service domain"
            )
        authorization = _effective_authorization(self._domain, command.authorization)
        forbidden_filters = set(command.filters) - set(self._domain.retrieval.allowed_filter_fields)
        if forbidden_filters:
            raise PolicyViolationError(
                "Query contains filters that are not allowed by the domain profile: "
                + ", ".join(sorted(forbidden_filters))
            )
        if command.diagnostics_requested and not authorization.diagnostic_access:
            raise PolicyViolationError("The authenticated user cannot access query diagnostics")
        initial_model = next(iter(self._model_servers.values())).gateway

        return self._engine.execute(
            self._pipeline,
            PipelineContext(
                command=replace(command, authorization=authorization),
                domain=self._domain,
                retrieval=self._retrieval,
                graph=self._graph,
                model=initial_model,
                conversation_history=self._conversation_history,
                model_servers=self._model_servers,
                prompts=self._prompts,
            ),
        )


def _effective_authorization(
    domain: DomainProfile,
    authorization: AuthorizationContext,
) -> AuthorizationContext:
    if authorization.tenant_id != domain.tenant_id:
        raise PolicyViolationError("The authenticated tenant cannot access this domain")
    allowed_acl = tuple(
        sorted(set(authorization.acl_labels).intersection(domain.allowed_acl_labels))
    )
    return replace(
        authorization,
        acl_labels=allowed_acl,
        allow_external_model=(authorization.allow_external_model and domain.allow_external_model),
    )
