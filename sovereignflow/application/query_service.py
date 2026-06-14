from __future__ import annotations

from sovereignflow.domain import (
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

        return self._engine.execute(
            self._pipeline,
            PipelineContext(
                command=command,
                domain=self._domain,
                retrieval=self._retrieval,
                graph=self._graph,
                model=self._model,
                prompts=self._prompts,
            ),
        )
