from __future__ import annotations

from typing import Any

from ..models import Citation, SearchRequest
from .definitions import StepDefinition
from .engine import ActionRegistry, PipelineRuntime
from .state import PipelineState


class NormalizeQueryAction:
    def execute(self, step: StepDefinition, state: PipelineState, runtime: PipelineRuntime) -> None:
        state.retrieval_query = " ".join(state.request.query.split())
        if not state.retrieval_query:
            raise ValueError("Query cannot be empty")
        state.retrieval_filters = {
            **runtime.domain.retrieval.filters,
            **state.request.filters,
        }
        return None


class RetrieveAction:
    def execute(self, step: StepDefinition, state: PipelineState, runtime: PipelineRuntime) -> None:
        request = SearchRequest(
            query=state.retrieval_query,
            domain=runtime.domain.name,
            tenant_id=state.request.tenant_id,
            top_k=runtime.domain.retrieval.top_k,
            mode=runtime.domain.retrieval.mode,
            filters=state.retrieval_filters,
            allowed_acl_labels=state.request.allowed_acl_labels,
            max_classification_level=state.request.max_classification_level,
        )
        state.search_hits = list(runtime.retrieval.search(request))
        return None


class BuildContextAction:
    def execute(self, step: StepDefinition, state: PipelineState, runtime: PipelineRuntime) -> None:
        max_characters = runtime.domain.retrieval.max_context_characters
        used = 0
        blocks: list[str] = []
        citations: list[Citation] = []

        for hit in state.search_hits:
            chunk = hit.chunk
            block = (
                f"[source_id={chunk.source_id}; chunk_id={chunk.chunk_id}; score={hit.score:.4f}]\n"
                f"{chunk.text.strip()}"
            )
            if used + len(block) > max_characters and blocks:
                break
            blocks.append(block[: max_characters - used])
            used += len(blocks[-1])
            citations.append(
                Citation(
                    source_id=chunk.source_id,
                    chunk_id=chunk.chunk_id,
                    source_uri=chunk.source_uri,
                    score=hit.score,
                    metadata=dict(chunk.metadata),
                )
            )
            if used >= max_characters:
                break

        state.context_blocks = blocks
        state.citations = citations
        return None


class CallModelAction:
    def execute(self, step: StepDefinition, state: PipelineState, runtime: PipelineRuntime) -> None:
        evidence = "\n\n---\n\n".join(state.context_blocks)
        if not evidence:
            evidence = "No relevant evidence was retrieved."

        user_prompt = (
            f"USER QUESTION\n{state.request.query.strip()}\n\n"
            f"EVIDENCE\n{evidence}\n\n"
            "Answer the user question and distinguish evidence from uncertainty."
        )
        security_context: dict[str, Any] = {
            "tenant_id": state.request.tenant_id,
            "allow_external": runtime.domain.allow_external_models,
            "allowed_acl_labels": list(state.request.allowed_acl_labels),
            "max_classification_level": state.request.max_classification_level,
            "retrieved_classification_level": max(
                (hit.chunk.classification_level for hit in state.search_hits),
                default=0,
            ),
        }
        state.last_model_response = runtime.model.generate(
            system_prompt=runtime.domain.system_prompt,
            user_prompt=user_prompt,
            security_context=security_context,
        ).strip()
        return None


class FinalizeAction:
    def execute(self, step: StepDefinition, state: PipelineState, runtime: PipelineRuntime) -> None:
        answer = state.last_model_response.strip()
        if runtime.domain.disclaimer:
            answer = f"{answer}\n\n---\n\n{runtime.domain.disclaimer}" if answer else runtime.domain.disclaimer
        state.final_answer = answer
        return None


def build_default_action_registry() -> ActionRegistry:
    registry = ActionRegistry()
    registry.register("normalize_query", NormalizeQueryAction())
    registry.register("retrieve", RetrieveAction())
    registry.register("build_context", BuildContextAction())
    registry.register("call_model", CallModelAction())
    registry.register("finalize", FinalizeAction())
    return registry
