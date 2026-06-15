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

from .pipeline import PipelineContext, PipelineEngine
from .ports import GraphTraversalPort, ModelGatewayPort, PromptRepositoryPort, RetrievalPort


class RagQueryService:
    def __init__(
        self,
        *,
        domain: DomainProfile,
        retrieval: RetrievalPort,
        graph: GraphTraversalPort,
        model: ModelGatewayPort,
        prompts: PromptRepositoryPort,
        pipeline: PipelineDefinition,
        engine: PipelineEngine,
    ) -> None:
        if model.scope == "external" and not domain.allow_external_model:
            raise PolicyViolationError(
                f"Domain '{domain.name}' does not allow external model transmission"
            )
        self._domain = domain
        self._retrieval = retrieval
        self._graph = graph
        self._model = model
        self._prompts = prompts
        self._pipeline = pipeline
        self._engine = engine

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
        if self._model.scope == "external" and not authorization.allow_external_model:
            raise PolicyViolationError(
                "The authenticated user cannot transmit context to an external model"
            )
        if command.diagnostics_requested and not authorization.diagnostic_access:
            raise PolicyViolationError("The authenticated user cannot access query diagnostics")

        return self._engine.execute(
            self._pipeline,
            PipelineContext(
                command=replace(command, authorization=authorization),
                domain=self._domain,
                retrieval=self._retrieval,
                graph=self._graph,
                model=self._model,
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
    user_maximum = authorization.max_classification_level
    domain_maximum = domain.max_classification_level
    if user_maximum is None:
        maximum = domain_maximum
    elif domain_maximum is None:
        maximum = user_maximum
    else:
        maximum = min(user_maximum, domain_maximum)
    return replace(
        authorization,
        acl_labels=allowed_acl,
        max_classification_level=maximum,
        allow_external_model=(authorization.allow_external_model and domain.allow_external_model),
    )
