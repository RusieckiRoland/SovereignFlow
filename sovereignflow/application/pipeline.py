from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol

from sovereignflow.domain import (
    Citation,
    ContextSecurityRequirement,
    ConversationTurn,
    DomainProfile,
    ExternalTransmissionPolicy,
    ModelServerDefinition,
    ModelTransmissionDiagnostic,
    PipelineDefinition,
    PipelineDefinitionError,
    PipelineExecutionError,
    PipelineRun,
    PipelineStepAudit,
    QueryCommand,
    QueryDiagnostics,
    QueryResult,
    RetrievalDiagnostic,
    SearchHit,
    SecurityModelKind,
    SovereignFlowError,
    TrustBoundary,
)

from .ports import (
    ConversationHistoryPort,
    ExecutionAuditPort,
    GraphTraversalPort,
    ModelGatewayPort,
    PromptRepositoryPort,
    RetrievalPort,
)


@dataclass(frozen=True)
class ModelServerRuntime:
    definition: ModelServerDefinition
    gateway: ModelGatewayPort


@dataclass
class PipelineContext:
    command: QueryCommand
    domain: DomainProfile
    retrieval: RetrievalPort
    graph: GraphTraversalPort
    model: ModelGatewayPort
    prompts: PromptRepositoryPort
    conversation_history: ConversationHistoryPort | None = None
    model_servers: Mapping[str, ModelServerRuntime] = field(default_factory=dict)
    conversation_id: str | None = None
    conversation_turn_id: str | None = None
    conversation_turn_finalized: bool = False
    conversation_history_text: str = ""
    conversation_history_turns: tuple[ConversationTurn, ...] = ()
    normalized_query: str = ""
    hits: tuple[SearchHit, ...] = ()
    seed_hits: tuple[SearchHit, ...] = ()
    graph_hits: tuple[SearchHit, ...] = ()
    omitted_chunk_ids: tuple[str, ...] = ()
    context_chunk_ids: tuple[str, ...] = ()
    evidence: str = ""
    citations: tuple[Citation, ...] = ()
    answer: str = ""
    last_model_response: str = ""
    last_route: str = ""
    last_prefix: str = ""
    variables: dict[str, Any] = field(default_factory=dict)
    loop_counters: dict[str, int] = field(default_factory=dict)
    retrieval_queries_asked_norm: set[str] = field(default_factory=set)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost: float = 0.0
    model_duration_ms: int = 0
    prompt_key: str = ""
    system_prompt_hash: str = ""
    model_transmission_checked: bool = False
    model_transmission_allowed: bool = False
    model_transmission_reason_code: str = "not_checked"
    model_transmission_selected_server_id: str | None = None
    model_transmission_final_server_id: str | None = None
    model_transmission_rerouted: bool = False
    model_transmission_trust_boundary: TrustBoundary | None = None
    model_transmission_external_policy: ExternalTransmissionPolicy | None = None
    model_transmission_context_requirement: ContextSecurityRequirement = field(
        default_factory=lambda: ContextSecurityRequirement(SecurityModelKind.NONE)
    )
    model_transmission_checked_chunk_ids: tuple[str, ...] = ()
    model_transmission_blocked_chunk_ids: tuple[str, ...] = ()
    trace: list[str] = field(default_factory=list)


class PipelineAction(Protocol):
    action_id: str
    behavior_version: str
    requires: frozenset[str]
    provides: frozenset[str]

    def execute(self, step, context: PipelineContext) -> str | None: ...


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
    from .actions.call_model import CallModelAction
    from .actions.expand_graph import ExpandGraphAction
    from .actions.fail_conversation_turn import FailConversationTurnAction
    from .actions.finalize import FinalizeAction
    from .actions.finalize_conversation_turn import FinalizeConversationTurnAction
    from .actions.enforce_model_transmission_policy import EnforceModelTransmissionPolicyAction
    from .actions.json_decision_router import JsonDecisionRouterAction
    from .actions.load_conversation_history import LoadConversationHistoryAction
    from .actions.loop_guard import LoopGuardAction
    from .actions.manage_context_budget import ManageContextBudgetAction
    from .actions.normalize_query import NormalizeQueryAction
    from .actions.prefix_router import PrefixRouterAction
    from .actions.repeat_query_guard import RepeatQueryGuardAction
    from .actions.require_evidence import RequireEvidenceAction
    from .actions.resolve_conversation import ResolveConversationAction
    from .actions.retrieve import RetrieveAction
    from .actions.set_variables import SetVariablesAction
    from .actions.start_conversation_turn import StartConversationTurnAction

    return ActionRegistry(
        (
            NormalizeQueryAction(),
            RetrieveAction(),
            ExpandGraphAction(),
            ManageContextBudgetAction(),
            RequireEvidenceAction(),
            EnforceModelTransmissionPolicyAction(),
            SetVariablesAction(),
            PrefixRouterAction(),
            JsonDecisionRouterAction(),
            LoopGuardAction(),
            RepeatQueryGuardAction(),
            ResolveConversationAction(),
            StartConversationTurnAction(),
            LoadConversationHistoryAction(),
            CallModelAction(),
            FinalizeAction(),
            FinalizeConversationTurnAction(),
            FailConversationTurnAction(),
        )
    )


class PipelineValidator:
    _initial_state = frozenset({"command", "domain", "conversation_history_service"})

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
            validate_config = getattr(action, "validate_config", None)
            if validate_config is not None:
                validate_config(step)

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
                tenant_id=context.command.authorization.tenant_id,
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
                route = action.execute(step, context)
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
                diagnostics=_diagnostics(context),
                conversation_id=context.conversation_id,
                turn_id=context.conversation_turn_id,
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
            if context.conversation_turn_id is not None and not context.conversation_turn_finalized:
                try:
                    from .actions._conversation import _fail_conversation_turn
                    _fail_conversation_turn(context, error_code)
                except Exception as history_exc:
                    history_error_code = (
                        history_exc.code
                        if isinstance(history_exc, SovereignFlowError)
                        else "internal_error"
                    )
                    history_error_message = (
                        history_exc.safe_message
                        if isinstance(history_exc, SovereignFlowError)
                        else "Conversation history failure"
                    )
                    self._audit.fail(
                        run_id,
                        error_code=history_error_code,
                        error_message=history_error_message,
                    )
                    raise history_exc from exc
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


def _diagnostics(context: PipelineContext) -> QueryDiagnostics:
    origins = {hit.chunk.chunk_id: "seed" for hit in context.seed_hits}
    origins.update(
        {
            hit.chunk.chunk_id: "graph"
            for hit in context.graph_hits
            if hit.chunk.chunk_id not in origins
        }
    )
    authorization = context.command.authorization
    return QueryDiagnostics(
        contract_version="1.0",
        subject_hash=sha256(authorization.subject.encode("utf-8")).hexdigest(),
        tenant_id=authorization.tenant_id,
        allowed_acl_labels=authorization.acl_labels,
        security_model_kind=context.domain.security_model.kind,
        search_mode=context.domain.retrieval.mode,
        retrieval=tuple(
            RetrievalDiagnostic(
                chunk_id=hit.chunk.chunk_id,
                source_id=hit.chunk.source_id,
                score=hit.score,
                score_type=hit.score_type,
                rank=index,
                origin=origins.get(hit.chunk.chunk_id, "seed"),
                graph_depth=(
                    int(hit.chunk.metadata["graph_depth"])
                    if "graph_depth" in hit.chunk.metadata
                    else None
                ),
                graph_path=tuple(hit.chunk.metadata.get("graph_path", ())),
            )
            for index, hit in enumerate(context.hits, start=1)
        ),
        omitted_chunk_ids=context.omitted_chunk_ids,
        context_chunk_ids=context.context_chunk_ids,
        context_characters=len(context.evidence),
        provider=context.model.name,
        model=context.model.model_id,
        prompt_key=context.prompt_key,
        model_transmission=ModelTransmissionDiagnostic(
            checked=context.model_transmission_checked,
            allowed=context.model_transmission_allowed,
            reason_code=context.model_transmission_reason_code,
            selected_model_server_id=context.model_transmission_selected_server_id,
            final_model_server_id=context.model_transmission_final_server_id,
            rerouted=context.model_transmission_rerouted,
            trust_boundary=context.model_transmission_trust_boundary,
            external_transmission=context.model_transmission_external_policy,
            context_security_requirement=context.model_transmission_context_requirement,
            checked_chunk_ids=context.model_transmission_checked_chunk_ids,
            blocked_chunk_ids=context.model_transmission_blocked_chunk_ids,
        ),
        system_prompt_hash=context.system_prompt_hash,
        prompt_tokens=context.prompt_tokens,
        completion_tokens=context.completion_tokens,
        model_duration_ms=context.model_duration_ms,
        pipeline_trace=tuple(context.trace),
    )
