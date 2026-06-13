from __future__ import annotations

from .domain import DomainProfile
from .models import QueryRequest, QueryResponse
from .pipeline import (
    PipelineEngine,
    PipelineLoader,
    PipelineRuntime,
    PipelineState,
    build_default_action_registry,
)
from .ports import ModelClient, RetrievalBackend


class RAGService:
    def __init__(
        self,
        domain: DomainProfile,
        retrieval: RetrievalBackend,
        model: ModelClient,
    ) -> None:
        self._domain = domain
        self._runtime = PipelineRuntime(domain=domain, retrieval=retrieval, model=model)
        self._definition = PipelineLoader.load(domain.pipeline)
        self._engine = PipelineEngine(build_default_action_registry())

    @property
    def domain_name(self) -> str:
        return self._domain.name

    def query(self, request: QueryRequest) -> QueryResponse:
        if request.domain != self._domain.name:
            raise ValueError(
                f"Request domain '{request.domain}' does not match service domain '{self._domain.name}'"
            )

        state = self._engine.run(
            self._definition,
            PipelineState(request=request),
            self._runtime,
        )
        return QueryResponse(
            answer=state.final_answer,
            domain=request.domain,
            session_id=request.session_id,
            citations=tuple(state.citations),
            pipeline_trace=tuple(state.trace),
        )

