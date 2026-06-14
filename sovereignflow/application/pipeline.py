from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sovereignflow.domain import (
    Citation,
    DomainProfile,
    GraphTraversalRequest,
    PipelineDefinition,
    PipelineDefinitionError,
    PipelineExecutionError,
    PipelineRun,
    PipelineStepAudit,
    PolicyViolationError,
    QueryCommand,
    QueryResult,
    SearchHit,
    SearchRequest,
    SovereignFlowError,
)

from .ports import (
    ExecutionAuditPort,
    GraphTraversalPort,
    ModelGatewayPort,
    PromptRepositoryPort,
    RetrievalPort,
)


@dataclass
class PipelineContext:
    command: QueryCommand
    domain: DomainProfile
    retrieval: RetrievalPort
    graph: GraphTraversalPort
    model: ModelGatewayPort
    prompts: PromptRepositoryPort
    normalized_query: str = ""
    hits: tuple[SearchHit, ...] = ()
    evidence: str = ""
    citations: tuple[Citation, ...] = ()
    answer: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: float = 0.0
    trace: list[str] = field(default_factory=list)


class PipelineAction(Protocol):
    action_id: str
    behavior_version: str
    requires: frozenset[str]
    provides: frozenset[str]

    def execute(self, context: PipelineContext) -> str | None: ...


class NormalizeQueryAction:
    action_id = "normalize_query"
    behavior_version = "1.0"
    requires = frozenset({"command"})
    provides = frozenset({"normalized_query"})

    def execute(self, context: PipelineContext) -> str | None:
        context.normalized_query = " ".join(context.command.query.split())
        return None


class RetrieveAction:
    action_id = "retrieve"
    behavior_version = "1.0"
    requires = frozenset({"normalized_query", "domain"})
    provides = frozenset({"hits"})

    def execute(self, context: PipelineContext) -> str | None:
        domain = context.domain
        filters = {**context.command.filters, **domain.retrieval.filters}
        context.hits = tuple(
            context.retrieval.search(
                SearchRequest(
                    query=context.normalized_query,
                    domain=domain.name,
                    tenant_id=domain.tenant_id,
                    top_k=domain.retrieval.top_k,
                    mode=domain.retrieval.mode,
                    filters=filters,
                    allowed_acl_labels=domain.allowed_acl_labels,
                    max_classification_level=domain.max_classification_level,
                )
            )
        )
        _verify_retrieval_boundary(domain, context.hits)
        return None


class ExpandGraphAction:
    action_id = "expand_graph"
    behavior_version = "1.0"
    requires = frozenset({"hits", "domain"})
    provides = frozenset({"hits"})

    def execute(self, context: PipelineContext) -> str | None:
        profile = context.domain.graph
        if not profile.enabled or not context.hits:
            return None
        expanded = tuple(
            context.graph.expand(
                GraphTraversalRequest(
                    seeds=context.hits,
                    domain=context.domain.name,
                    tenant_id=context.domain.tenant_id,
                    max_depth=profile.max_depth,
                    max_nodes=profile.max_nodes,
                    direction=profile.direction,
                    relationship_types=profile.relationship_types,
                    allowed_acl_labels=context.domain.allowed_acl_labels,
                    max_classification_level=context.domain.max_classification_level,
                )
            )
        )
        _verify_retrieval_boundary(context.domain, expanded)
        context.hits = (*context.hits, *expanded)
        return None


class BuildContextAction:
    action_id = "build_context"
    behavior_version = "1.0"
    requires = frozenset({"hits", "domain"})
    provides = frozenset({"evidence", "citations"})

    def execute(self, context: PipelineContext) -> str | None:
        context.evidence, context.citations = _build_context(
            context.hits,
            context.domain.retrieval.max_context_characters,
        )
        return None


class CallModelAction:
    action_id = "call_model"
    behavior_version = "1.0"
    requires = frozenset({"normalized_query", "evidence", "domain"})
    provides = frozenset({"answer"})

    def execute(self, context: PipelineContext) -> str | None:
        generation = context.model.generate(
            system_prompt=context.prompts.load(context.domain.prompt_name),
            user_prompt=(
                f"USER QUESTION\n{context.normalized_query}\n\n"
                f"EVIDENCE\n{context.evidence}\n\n"
                "Answer from the evidence and state uncertainty explicitly."
            ),
        )
        context.answer = generation.text
        context.prompt_tokens = generation.prompt_tokens
        context.completion_tokens = generation.completion_tokens
        context.estimated_cost = generation.estimated_cost
        return None


class FinalizeAction:
    action_id = "finalize"
    behavior_version = "1.0"
    requires = frozenset({"answer", "citations"})
    provides = frozenset({"result"})

    def execute(self, context: PipelineContext) -> str | None:
        if context.domain.disclaimer:
            context.answer = f"{context.answer}\n\n---\n\n{context.domain.disclaimer}".strip()
        return None


class ActionRegistry:
    def __init__(self, actions: Sequence[PipelineAction]) -> None:
        registered = {action.action_id: action for action in actions}
        if len(registered) != len(actions):
            raise PipelineDefinitionError("Pipeline action identifiers must be unique")
        self._actions = registered

    def get(self, action_id: str) -> PipelineAction:
        try:
            return self._actions[action_id]
        except KeyError as exc:
            raise PipelineDefinitionError(f"Unknown pipeline action: {action_id}") from exc

    @property
    def actions(self) -> Mapping[str, PipelineAction]:
        return dict(self._actions)


def default_action_registry() -> ActionRegistry:
    return ActionRegistry(
        (
            NormalizeQueryAction(),
            RetrieveAction(),
            ExpandGraphAction(),
            BuildContextAction(),
            CallModelAction(),
            FinalizeAction(),
        )
    )


class PipelineValidator:
    _initial_state = frozenset({"command", "domain"})

    def __init__(self, registry: ActionRegistry) -> None:
        self._registry = registry

    def validate(self, pipeline: PipelineDefinition) -> None:
        steps = {step.step_id: step for step in pipeline.steps}
        if len(steps) != len(pipeline.steps):
            raise PipelineDefinitionError("Pipeline step identifiers must be unique")
        if pipeline.entry_step_id not in steps:
            raise PipelineDefinitionError("Pipeline entry step does not exist")

        for step in pipeline.steps:
            action = self._registry.get(step.action)
            if action.behavior_version != step.action_version:
                raise PipelineDefinitionError(
                    f"Action version mismatch for '{step.step_id}': "
                    f"expected {action.behavior_version}, got {step.action_version}"
                )
            if step.next_step_id is not None and step.next_step_id not in steps:
                raise PipelineDefinitionError(
                    f"Step '{step.step_id}' references unknown step '{step.next_step_id}'"
                )
            for route, target in step.routes.items():
                if target not in steps:
                    raise PipelineDefinitionError(
                        f"Step '{step.step_id}' route '{route}' references unknown step '{target}'"
                    )

        visited: set[str] = set()
        terminal_results: list[bool] = []

        def walk(
            step_id: str,
            available: frozenset[str],
            path: tuple[str, ...],
        ) -> None:
            if step_id in path:
                raise PipelineDefinitionError(f"Pipeline contains a cycle at step '{step_id}'")
            if len(path) + 1 > pipeline.max_steps:
                raise PipelineDefinitionError("Pipeline path exceeds max_steps")
            visited.add(step_id)
            step = steps[step_id]
            action = self._registry.get(step.action)
            missing = action.requires - available
            if missing:
                raise PipelineDefinitionError(
                    f"Step '{step.step_id}' requires unavailable state: "
                    f"{', '.join(sorted(missing))}"
                )
            next_available = available | action.provides
            if step.terminal:
                terminal_results.append("result" in next_available)
                return
            targets = set(step.routes.values())
            if step.next_step_id is not None:
                targets.add(step.next_step_id)
            for target in sorted(targets):
                walk(target, frozenset(next_available), (*path, step_id))

        walk(pipeline.entry_step_id, self._initial_state, ())
        if visited != set(steps):
            unreachable = ", ".join(sorted(set(steps) - visited))
            raise PipelineDefinitionError(f"Pipeline contains unreachable steps: {unreachable}")
        if not all(terminal_results):
            raise PipelineDefinitionError("Pipeline does not produce a result")


class PipelineEngine:
    def __init__(
        self,
        *,
        registry: ActionRegistry,
        audit: ExecutionAuditPort,
        monotonic: Callable[[], float] = time.monotonic,
        run_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._monotonic = monotonic
        self._run_id_factory = run_id_factory

    def execute(self, pipeline: PipelineDefinition, context: PipelineContext) -> QueryResult:
        run_id = self._run_id_factory()
        self._audit.start(
            PipelineRun(
                run_id=run_id,
                request_id=context.command.request_id,
                session_id=context.command.session_id,
                domain=context.domain.name,
                tenant_id=context.domain.tenant_id,
                pipeline_name=pipeline.name,
                pipeline_version=pipeline.behavior_version,
                pipeline_checksum=pipeline.checksum,
                query=context.command.query,
            )
        )
        try:
            current = pipeline.entry_step_id
            sequence = 0
            while current is not None:
                sequence += 1
                if sequence > pipeline.max_steps:
                    raise PipelineExecutionError("Pipeline exceeded its configured step limit")
                step = pipeline.step(current)
                action = self._registry.get(step.action)
                started = self._monotonic()
                route = action.execute(context)
                duration_ms = max(0, round((self._monotonic() - started) * 1000))
                context.trace.append(step.step_id)
                next_step_id = self._next_step(step, route)
                self._audit.record_step(
                    PipelineStepAudit(
                        run_id=run_id,
                        sequence_number=sequence,
                        step_id=step.step_id,
                        action=step.action,
                        action_version=step.action_version,
                        duration_ms=duration_ms,
                        next_step_id=next_step_id,
                    )
                )
                current = next_step_id
            result = QueryResult(
                request_id=context.command.request_id,
                answer=context.answer,
                domain=context.domain.name,
                session_id=context.command.session_id,
                citations=context.citations,
                pipeline_trace=tuple(context.trace),
            )
            self._audit.succeed(
                run_id,
                answer=result.answer,
                citation_count=len(result.citations),
                prompt_tokens=context.prompt_tokens,
                completion_tokens=context.completion_tokens,
                estimated_cost=context.estimated_cost,
            )
            return result
        except Exception as exc:
            error_code = exc.code if isinstance(exc, SovereignFlowError) else "internal_error"
            error_message = (
                exc.safe_message
                if isinstance(exc, SovereignFlowError)
                else "Unhandled pipeline execution failure"
            )
            self._audit.fail(run_id, error_code=error_code, error_message=error_message)
            raise

    @staticmethod
    def _next_step(step, route: str | None) -> str | None:
        if step.terminal:
            if route is not None:
                raise PipelineExecutionError(f"Terminal step '{step.step_id}' returned a route")
            return None
        if route is not None:
            try:
                return step.routes[route]
            except KeyError as exc:
                raise PipelineExecutionError(
                    f"Step '{step.step_id}' returned unknown route '{route}'"
                ) from exc
        if step.next_step_id is None:
            raise PipelineExecutionError(
                f"Step '{step.step_id}' requires an explicit route decision"
            )
        return step.next_step_id


def _verify_retrieval_boundary(
    domain: DomainProfile,
    hits: Sequence[SearchHit],
) -> None:
    allowed_labels = set(domain.allowed_acl_labels)
    for hit in hits:
        chunk = hit.chunk
        if chunk.domain != domain.name or chunk.tenant_id != domain.tenant_id:
            raise PolicyViolationError("Retrieval provider crossed a domain or tenant boundary")
        if chunk.acl_labels and not set(chunk.acl_labels).issubset(allowed_labels):
            raise PolicyViolationError("Retrieval provider returned a forbidden ACL label")
        maximum = domain.max_classification_level
        if maximum is not None and chunk.classification_level > maximum:
            raise PolicyViolationError(
                "Retrieval provider returned a forbidden classification level"
            )


def _build_context(
    hits: Sequence[SearchHit],
    maximum: int,
) -> tuple[str, tuple[Citation, ...]]:
    used = 0
    blocks: list[str] = []
    citations: list[Citation] = []
    for hit in hits:
        block = (
            f"[source_id={hit.chunk.source_id}; chunk_id={hit.chunk.chunk_id}; "
            f"{hit.score_type}={hit.score:.6f}]\n{hit.chunk.text}"
        )
        selected = block[: maximum - used]
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
    evidence = "\n\n---\n\n".join(blocks) or "No relevant evidence was retrieved."
    return evidence, tuple(citations)
