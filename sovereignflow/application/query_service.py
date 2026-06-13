from __future__ import annotations

from collections.abc import Sequence

from sovereignflow.domain import (
    Citation,
    DomainProfile,
    PolicyViolationError,
    QueryCommand,
    QueryResult,
    SearchHit,
    SearchRequest,
)

from .ports import ModelGatewayPort, PromptRepositoryPort, RetrievalPort


class RagQueryService:
    def __init__(
        self,
        *,
        domain: DomainProfile,
        retrieval: RetrievalPort,
        model: ModelGatewayPort,
        prompts: PromptRepositoryPort,
    ) -> None:
        if model.scope == "external" and not domain.allow_external_model:
            raise PolicyViolationError(
                f"Domain '{domain.name}' does not allow external model transmission"
            )
        self._domain = domain
        self._retrieval = retrieval
        self._model = model
        self._prompts = prompts

    @property
    def domain_name(self) -> str:
        return self._domain.name

    def execute(self, command: QueryCommand) -> QueryResult:
        if command.domain != self._domain.name:
            raise PolicyViolationError(
                f"Query domain '{command.domain}' does not match service domain"
            )

        trace = ["normalize_query"]
        normalized_query = " ".join(command.query.split())
        filters = {**command.filters, **self._domain.retrieval.filters}

        trace.append("retrieve")
        hits = tuple(
            self._retrieval.search(
                SearchRequest(
                    query=normalized_query,
                    domain=self._domain.name,
                    tenant_id=self._domain.tenant_id,
                    top_k=self._domain.retrieval.top_k,
                    mode=self._domain.retrieval.mode,
                    filters=filters,
                    allowed_acl_labels=self._domain.allowed_acl_labels,
                    max_classification_level=self._domain.max_classification_level,
                )
            )
        )
        self._verify_retrieval_boundary(hits)

        trace.append("build_context")
        evidence, citations = self._build_context(hits)

        trace.append("call_model")
        answer = self._model.generate(
            system_prompt=self._prompts.load(self._domain.prompt_name),
            user_prompt=(
                f"USER QUESTION\n{normalized_query}\n\n"
                f"EVIDENCE\n{evidence}\n\n"
                "Answer from the evidence and state uncertainty explicitly."
            ),
        ).strip()

        trace.append("finalize")
        if self._domain.disclaimer:
            answer = f"{answer}\n\n---\n\n{self._domain.disclaimer}".strip()

        return QueryResult(
            request_id=command.request_id,
            answer=answer,
            domain=self._domain.name,
            session_id=command.session_id,
            citations=citations,
            pipeline_trace=tuple(trace),
        )

    def _verify_retrieval_boundary(self, hits: Sequence[SearchHit]) -> None:
        allowed_labels = set(self._domain.allowed_acl_labels)
        for hit in hits:
            chunk = hit.chunk
            if chunk.domain != self._domain.name or chunk.tenant_id != self._domain.tenant_id:
                raise PolicyViolationError("Retrieval provider crossed a domain or tenant boundary")
            if chunk.acl_labels and not set(chunk.acl_labels).issubset(allowed_labels):
                raise PolicyViolationError("Retrieval provider returned a forbidden ACL label")
            maximum = self._domain.max_classification_level
            if maximum is not None and chunk.classification_level > maximum:
                raise PolicyViolationError(
                    "Retrieval provider returned a forbidden classification level"
                )

    def _build_context(
        self,
        hits: Sequence[SearchHit],
    ) -> tuple[str, tuple[Citation, ...]]:
        maximum = self._domain.retrieval.max_context_characters
        used = 0
        blocks: list[str] = []
        citations: list[Citation] = []
        for hit in hits:
            block = (
                f"[source_id={hit.chunk.source_id}; chunk_id={hit.chunk.chunk_id}; "
                f"{hit.score_type}={hit.score:.6f}]\n{hit.chunk.text}"
            )
            remaining = maximum - used
            selected = block[:remaining]
            blocks.append(selected)
            used += len(selected)
            citations.append(
                Citation(
                    source_id=hit.chunk.source_id,
                    chunk_id=hit.chunk.chunk_id,
                    source_uri=hit.chunk.source_uri,
                    score=hit.score,
                    score_type=hit.score_type,
                    metadata=hit.chunk.metadata,
                )
            )
            if len(selected) < len(block):
                break
        return "\n\n---\n\n".join(blocks) or "No relevant evidence was retrieved.", tuple(citations)
