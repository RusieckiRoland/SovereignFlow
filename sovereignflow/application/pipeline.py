from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol

from sovereignflow.domain import (
    Citation,
    ContextSecurityRequirement,
    DomainProfile,
    ExternalTransmissionPolicy,
    GraphDirection,
    GraphTraversalRequest,
    ModelServerDefinition,
    ModelTransmissionDiagnostic,
    PipelineDefinition,
    PipelineDefinitionError,
    PipelineExecutionError,
    PipelineRun,
    PipelineStepAudit,
    PolicyViolationError,
    QueryCommand,
    QueryDiagnostics,
    QueryResult,
    RetrievalDiagnostic,
    SearchHit,
    SearchMode,
    SearchRequest,
    SecurityModelKind,
    SovereignFlowError,
    TrustBoundary,
    context_security_requirement,
    document_visible_to_subject,
    model_server_satisfies_requirement,
)

from .ports import (
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
    model_servers: Mapping[str, ModelServerRuntime] = field(default_factory=dict)
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


class NormalizeQueryAction:
    action_id = "normalize_query"
    behavior_version = "1.0"
    requires = frozenset({"command"})
    provides = frozenset({"normalized_query"})

    def execute(self, step, context: PipelineContext) -> str | None:
        context.normalized_query = " ".join(context.command.query.split())
        return None


class RetrieveAction:
    action_id = "retrieve"
    behavior_version = "1.0"
    requires = frozenset({"normalized_query", "domain"})
    provides = frozenset({"hits"})

    def validate_config(self, step) -> None:
        _retrieve_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        config = _retrieve_config(step)
        domain = context.domain
        authorization = context.command.authorization
        filters = {**context.command.filters, **domain.retrieval.filters, **config.filters}
        request_query = _retrieval_query(config.query_source, context)
        context.seed_hits = tuple(
            context.retrieval.search(
                SearchRequest(
                    query=request_query,
                    domain=domain.name,
                    tenant_id=authorization.tenant_id,
                    top_k=config.top_k,
                    mode=config.search_mode,
                    filters=filters,
                    allowed_acl_labels=authorization.acl_labels,
                    security_model=domain.security_model,
                    subject_security=authorization.security,
                )
            )
        )
        context.retrieval_queries_asked_norm.add(_normalize_guard_query(request_query))
        _verify_retrieval_boundary(domain, authorization, context.seed_hits)
        context.hits = context.seed_hits
        return None


class ExpandGraphAction:
    action_id = "expand_graph"
    behavior_version = "1.0"
    requires = frozenset({"hits", "domain"})
    provides = frozenset({"hits"})

    def validate_config(self, step) -> None:
        _expand_graph_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        config = _expand_graph_config(step)
        if not config.enabled or not context.hits:
            return None
        authorization = context.command.authorization
        expanded = tuple(
            context.graph.expand(
                GraphTraversalRequest(
                    seeds=context.hits,
                    domain=context.domain.name,
                    tenant_id=authorization.tenant_id,
                    max_depth=config.max_depth,
                    max_nodes=config.max_nodes,
                    direction=config.direction,
                    relationship_types=config.relationship_types,
                    allowed_acl_labels=authorization.acl_labels,
                    security_model=context.domain.security_model,
                    subject_security=authorization.security,
                )
            )
        )
        _verify_retrieval_boundary(context.domain, authorization, expanded)
        context.graph_hits = expanded
        unique = {hit.chunk.chunk_id: hit for hit in context.seed_hits}
        for hit in expanded:
            unique.setdefault(hit.chunk.chunk_id, hit)
        context.hits = tuple(unique.values())
        return None


class ManageContextBudgetAction:
    action_id = "manage_context_budget"
    behavior_version = "1.0"
    requires = frozenset({"hits"})
    provides = frozenset({"evidence", "citations"})

    def validate_config(self, step) -> None:
        _context_budget_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        config = _context_budget_config(step)
        (
            context.evidence,
            context.citations,
            context.context_chunk_ids,
            context.omitted_chunk_ids,
        ) = _build_context(context.hits, config.max_context_characters)
        return None


class RequireEvidenceAction:
    action_id = "require_evidence"
    behavior_version = "1.0"
    requires = frozenset({"evidence", "citations"})
    provides = frozenset()

    def execute(self, step, context: PipelineContext) -> str | None:
        if not context.evidence or not context.citations:
            raise PipelineExecutionError("The pipeline requires retrieved evidence")
        return None


class EnforceModelTransmissionPolicyAction:
    action_id = "enforce_model_transmission_policy"
    behavior_version = "2.0"
    requires = frozenset({"hits", "evidence", "citations"})
    provides = frozenset({"model_transmission_policy"})

    def validate_config(self, step) -> None:
        _model_transmission_policy_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        diagnostic = _model_transmission_decision(
            _model_transmission_policy_config(step),
            context,
        )
        context.model_transmission_checked = diagnostic.checked
        context.model_transmission_allowed = diagnostic.allowed
        context.model_transmission_reason_code = diagnostic.reason_code
        context.model_transmission_selected_server_id = diagnostic.selected_model_server_id
        context.model_transmission_final_server_id = diagnostic.final_model_server_id
        context.model_transmission_rerouted = diagnostic.rerouted
        context.model_transmission_trust_boundary = diagnostic.trust_boundary
        context.model_transmission_external_policy = diagnostic.external_transmission
        context.model_transmission_context_requirement = diagnostic.context_security_requirement
        context.model_transmission_checked_chunk_ids = diagnostic.checked_chunk_ids
        context.model_transmission_blocked_chunk_ids = diagnostic.blocked_chunk_ids
        if not diagnostic.allowed:
            raise PolicyViolationError(diagnostic.reason_code)
        return None


class SetVariablesAction:
    action_id = "set_variables"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _set_variables_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        for rule in _set_variables_config(step):
            value = rule.literal_value if rule.has_literal else _state_value(rule.source, context)
            _set_state_value(rule.target, _transform_value(rule.transform, value), context)
        return None


class PrefixRouterAction:
    action_id = "prefix_router"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _prefix_router_config(step)

    def execute(self, step, context: PipelineContext) -> str:
        config = _prefix_router_config(step)
        text = str(_state_value(config.source, context) or "").strip()
        for route_name, prefix in config.prefixes:
            if text.startswith(prefix):
                context.last_route = route_name
                context.last_prefix = route_name
                context.last_model_response = text.removeprefix(prefix).strip()
                return route_name
        context.last_route = config.on_other
        context.last_prefix = ""
        context.last_model_response = text
        return config.on_other


class JsonDecisionRouterAction:
    action_id = "json_decision_router"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _json_decision_router_config(step)

    def execute(self, step, context: PipelineContext) -> str:
        config = _json_decision_router_config(step)
        raw = str(_state_value(config.source, context) or "").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            if config.on_other is None:
                raise PipelineExecutionError("json_decision_router received invalid JSON") from exc
            context.last_route = config.on_other
            return config.on_other
        if not isinstance(payload, dict):
            if config.on_other is None:
                raise PipelineExecutionError("json_decision_router payload must be a JSON object")
            context.last_route = config.on_other
            return config.on_other
        decision = _json_decision(payload)
        cleaned = {
            str(key): value
            for key, value in payload.items()
            if str(key).strip().lower() not in {"decision", "route", "mode"}
        }
        context.last_model_response = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
        if decision in config.allowed_decisions:
            context.last_route = decision
            return decision
        if config.on_other is None:
            raise PipelineExecutionError("json_decision_router decision is not allowed")
        context.last_route = config.on_other
        return config.on_other


class LoopGuardAction:
    action_id = "loop_guard"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _loop_guard_config(step)

    def execute(self, step, context: PipelineContext) -> str:
        config = _loop_guard_config(step)
        current = context.loop_counters.get(step.step_id, 0) + 1
        context.loop_counters[step.step_id] = current
        route = config.on_allow if current <= config.max_loops else config.on_deny
        context.last_route = route
        return route


class RepeatQueryGuardAction:
    action_id = "repeat_query_guard"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _repeat_query_guard_config(step)

    def execute(self, step, context: PipelineContext) -> str:
        config = _repeat_query_guard_config(step)
        query = _guard_query(config, context)
        normalized = _normalize_guard_query(query)
        if not normalized or normalized in context.retrieval_queries_asked_norm:
            context.last_route = config.on_repeat
            return config.on_repeat
        context.retrieval_queries_asked_norm.add(normalized)
        context.last_route = config.on_ok
        return config.on_ok


class CallModelAction:
    action_id = "call_model"
    behavior_version = "1.0"
    requires = frozenset({"normalized_query", "evidence", "domain", "model_transmission_policy"})
    provides = frozenset({"answer"})

    def validate_config(self, step) -> None:
        _call_model_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        if not context.model_transmission_checked:
            raise PipelineExecutionError("Model transmission policy has not been enforced")
        if not context.model_transmission_allowed:
            raise PolicyViolationError("Model transmission policy blocked the model call")
        config = _call_model_config(step)
        system_prompt = context.prompts.load(config.prompt_key)
        user_prompt = _render_user_prompt(config.user_parts, context)
        context.prompt_key = config.prompt_key
        context.system_prompt_hash = sha256(system_prompt.encode("utf-8")).hexdigest()
        started = time.monotonic()
        generation = context.model.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            generation_parameters=config.generation_parameters,
        )
        context.model_duration_ms = max(0, round((time.monotonic() - started) * 1000))
        context.answer = generation.text
        context.last_model_response = generation.text
        context.prompt_tokens = generation.prompt_tokens
        context.completion_tokens = generation.completion_tokens
        context.estimated_cost = generation.estimated_cost
        return None


class FinalizeAction:
    action_id = "finalize"
    behavior_version = "1.0"
    requires = frozenset({"answer", "citations"})
    provides = frozenset({"result"})

    def execute(self, step, context: PipelineContext) -> str | None:
        if context.domain.disclaimer:
            context.answer = f"{context.answer}\n\n---\n\n{context.domain.disclaimer}".strip()
        return None


@dataclass(frozen=True)
class RetrieveConfig:
    query_source: str
    search_mode: SearchMode
    top_k: int
    filters: Mapping[str, Any]


@dataclass(frozen=True)
class ExpandGraphConfig:
    enabled: bool
    max_depth: int
    max_nodes: int
    direction: GraphDirection
    relationship_types: tuple[str, ...]


@dataclass(frozen=True)
class ContextBudgetConfig:
    max_context_characters: int


@dataclass(frozen=True)
class SetVariableRule:
    target: str
    source: str
    literal_value: Any
    has_literal: bool
    transform: str


@dataclass(frozen=True)
class PrefixRouterConfig:
    source: str
    prefixes: tuple[tuple[str, str], ...]
    on_other: str


@dataclass(frozen=True)
class JsonDecisionRouterConfig:
    source: str
    allowed_decisions: frozenset[str]
    on_other: str | None


@dataclass(frozen=True)
class LoopGuardConfig:
    max_loops: int
    on_allow: str
    on_deny: str


@dataclass(frozen=True)
class RepeatQueryGuardConfig:
    source: str
    query_parser: str
    on_ok: str
    on_repeat: str


@dataclass(frozen=True)
class ModelTransmissionPolicyConfig:
    selected_model_server_id: str
    external_transmission: ExternalTransmissionPolicy


@dataclass(frozen=True)
class UserPromptPart:
    name: str
    source: str
    template: str


@dataclass(frozen=True)
class CallModelConfig:
    prompt_key: str
    user_parts: tuple[UserPromptPart, ...]
    generation_parameters: Mapping[str, Any]


_RETRIEVE_ALLOWED_KEYS = frozenset({"query_source", "search_mode", "top_k", "filters"})
_RETRIEVE_QUERY_SOURCES = frozenset({"normalized_query", "command_query"})
_EXPAND_GRAPH_ALLOWED_KEYS = frozenset(
    {"enabled", "max_depth", "max_nodes", "direction", "relationship_types"}
)
_CONTEXT_BUDGET_ALLOWED_KEYS = frozenset({"source", "target", "max_context_characters"})
_SET_VARIABLES_ALLOWED_KEYS = frozenset({"rules"})
_SET_VARIABLE_RULE_KEYS = frozenset({"set", "from", "value", "transform"})
_SET_VARIABLE_SOURCES = frozenset(
    {
        "answer",
        "last_model_response",
        "normalized_query",
        "evidence",
        "context_chunk_ids",
        "last_route",
        "last_prefix",
        "variables",
    }
)
_SET_VARIABLE_TARGETS = frozenset(
    {"answer", "last_model_response", "normalized_query", "evidence", "variables"}
)
_SET_VARIABLE_TRANSFORMS = frozenset({"copy", "to_list", "split_lines", "parse_json", "clear"})
_PREFIX_ROUTER_ALLOWED_KEYS = frozenset({"source", "prefixes", "on_other"})
_JSON_DECISION_ROUTER_ALLOWED_KEYS = frozenset({"source", "allowed_decisions", "on_other"})
_LOOP_GUARD_ALLOWED_KEYS = frozenset({"max_loops", "on_allow", "on_deny"})
_REPEAT_QUERY_GUARD_ALLOWED_KEYS = frozenset({"source", "query_parser", "on_ok", "on_repeat"})
_MODEL_TRANSMISSION_POLICY_ALLOWED_KEYS = frozenset(
    {"selected_model_server_id", "external_transmission"}
)
_ROUTER_SOURCES = frozenset({"answer", "last_model_response", "normalized_query", "evidence"})
_REPEAT_QUERY_PARSERS = frozenset({"raw", "json"})
_CALL_MODEL_ALLOWED_KEYS = frozenset(
    {
        "prompt_key",
        "user_parts",
        "temperature",
        "top_p",
        "max_tokens",
        "max_output_tokens",
    }
)
_USER_PART_KEYS = frozenset({"source", "template"})
_USER_PART_SOURCES = frozenset(
    {
        "normalized_query",
        "evidence",
        "context_chunk_ids",
        "citations_text",
        "retrieval_trace_summary",
    }
)


def _retrieve_config(step) -> RetrieveConfig:
    _reject_unknown_config_keys(step, _RETRIEVE_ALLOWED_KEYS, "retrieve")
    query_source = _required_config_string(step, "query_source", "retrieve")
    if query_source not in _RETRIEVE_QUERY_SOURCES:
        raise PipelineDefinitionError(f"Step '{step.step_id}' retrieve.query_source is not allowed")
    search_mode = _search_mode(step)
    filters = step.config.get("filters", {})
    if not isinstance(filters, Mapping):
        raise PipelineDefinitionError(f"Step '{step.step_id}' retrieve.filters must be a mapping")
    return RetrieveConfig(
        query_source=query_source,
        search_mode=search_mode,
        top_k=_positive_config_integer(step, "top_k", "retrieve"),
        filters=filters,
    )


def _search_mode(step) -> SearchMode:
    raw = _required_config_string(step, "search_mode", "retrieve")
    try:
        return SearchMode(raw)
    except ValueError as exc:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' retrieve.search_mode is invalid"
        ) from exc


def _retrieval_query(source: str, context: PipelineContext) -> str:
    if source == "normalized_query":
        return context.normalized_query
    if source == "command_query":
        return context.command.query
    raise PipelineExecutionError(f"Unsupported retrieve query source '{source}'")


def _expand_graph_config(step) -> ExpandGraphConfig:
    _reject_unknown_config_keys(step, _EXPAND_GRAPH_ALLOWED_KEYS, "expand_graph")
    enabled = step.config.get("enabled")
    if not isinstance(enabled, bool):
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' expand_graph.enabled must be a boolean"
        )
    direction = _graph_direction(step)
    return ExpandGraphConfig(
        enabled=enabled,
        max_depth=_positive_config_integer(step, "max_depth", "expand_graph"),
        max_nodes=_positive_config_integer(step, "max_nodes", "expand_graph"),
        direction=direction,
        relationship_types=_relationship_types(step),
    )


def _graph_direction(step) -> GraphDirection:
    raw = _required_config_string(step, "direction", "expand_graph")
    try:
        return GraphDirection(raw)
    except ValueError as exc:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' expand_graph.direction is invalid"
        ) from exc


def _relationship_types(step) -> tuple[str, ...]:
    raw = step.config.get("relationship_types")
    if not isinstance(raw, tuple) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' expand_graph.relationship_types must be a list of strings"
        )
    return tuple(item.strip() for item in raw)


def _context_budget_config(step) -> ContextBudgetConfig:
    _reject_unknown_config_keys(step, _CONTEXT_BUDGET_ALLOWED_KEYS, "manage_context_budget")
    source = _required_config_string(step, "source", "manage_context_budget")
    target = _required_config_string(step, "target", "manage_context_budget")
    if source != "hits":
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' manage_context_budget.source must be 'hits'"
        )
    if target != "evidence":
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' manage_context_budget.target must be 'evidence'"
        )
    return ContextBudgetConfig(
        max_context_characters=_positive_config_integer(
            step,
            "max_context_characters",
            "manage_context_budget",
        )
    )


def _set_variables_config(step) -> tuple[SetVariableRule, ...]:
    _reject_unknown_config_keys(step, _SET_VARIABLES_ALLOWED_KEYS, "set_variables")
    raw_rules = step.config.get("rules")
    if not isinstance(raw_rules, tuple) or not raw_rules:
        raise PipelineDefinitionError("set_variables.rules must be a non-empty list")
    rules = []
    for index, raw_rule in enumerate(raw_rules, start=1):
        if not isinstance(raw_rule, Mapping):
            raise PipelineDefinitionError("set_variables.rules[] must be a mapping")
        unknown = set(raw_rule) - _SET_VARIABLE_RULE_KEYS
        if unknown:
            raise PipelineDefinitionError(
                "set_variables rule has unsupported fields: " + ", ".join(sorted(unknown))
            )
        target = _required_rule_string(raw_rule, "set", "set_variables.rules[].set")
        if "." in target:
            raise PipelineDefinitionError("set_variables target must not contain dot paths")
        if target not in _SET_VARIABLE_TARGETS:
            raise PipelineDefinitionError("set_variables target is not allowed")
        has_source = "from" in raw_rule
        has_literal = "value" in raw_rule
        if has_source == has_literal:
            raise PipelineDefinitionError(
                f"set_variables rule {index} must define exactly one of from or value"
            )
        source = ""
        if has_source:
            source = _required_rule_string(raw_rule, "from", "set_variables.rules[].from")
            if "." in source:
                raise PipelineDefinitionError("set_variables source must not contain dot paths")
            if source not in _SET_VARIABLE_SOURCES:
                raise PipelineDefinitionError("set_variables source is not allowed")
        transform = str(raw_rule.get("transform", "copy")).strip()
        if transform not in _SET_VARIABLE_TRANSFORMS:
            raise PipelineDefinitionError("set_variables transform is not allowed")
        rules.append(
            SetVariableRule(
                target=target,
                source=source,
                literal_value=raw_rule.get("value"),
                has_literal=has_literal,
                transform=transform,
            )
        )
    return tuple(rules)


def _prefix_router_config(step) -> PrefixRouterConfig:
    _reject_unknown_config_keys(step, _PREFIX_ROUTER_ALLOWED_KEYS, "prefix_router")
    source = _required_config_string(step, "source", "prefix_router")
    if source not in _ROUTER_SOURCES:
        raise PipelineDefinitionError("prefix_router.source is not allowed")
    raw_prefixes = step.config.get("prefixes")
    if not isinstance(raw_prefixes, Mapping) or not raw_prefixes:
        raise PipelineDefinitionError("prefix_router.prefixes must be a non-empty mapping")
    prefixes = []
    for route_name, prefix in raw_prefixes.items():
        normalized_route = _route_name(route_name, "prefix_router.prefixes route")
        if normalized_route not in step.routes:
            raise PipelineDefinitionError("prefix_router prefix route is not declared in routes")
        if not isinstance(prefix, str) or not prefix:
            raise PipelineDefinitionError("prefix_router prefix must be a non-empty string")
        prefixes.append((normalized_route, prefix))
    on_other = _required_config_string(step, "on_other", "prefix_router")
    if on_other not in step.routes:
        raise PipelineDefinitionError("prefix_router.on_other route is not declared in routes")
    return PrefixRouterConfig(source=source, prefixes=tuple(prefixes), on_other=on_other)


def _json_decision_router_config(step) -> JsonDecisionRouterConfig:
    _reject_unknown_config_keys(step, _JSON_DECISION_ROUTER_ALLOWED_KEYS, "json_decision_router")
    source = _required_config_string(step, "source", "json_decision_router")
    if source not in _ROUTER_SOURCES:
        raise PipelineDefinitionError("json_decision_router.source is not allowed")
    raw_decisions = step.config.get("allowed_decisions")
    if not isinstance(raw_decisions, tuple) or not raw_decisions:
        raise PipelineDefinitionError(
            "json_decision_router.allowed_decisions must be a non-empty list"
        )
    decisions = frozenset(
        _route_name(item, "json_decision_router decision") for item in raw_decisions
    )
    undeclared = decisions - set(step.routes)
    if undeclared:
        raise PipelineDefinitionError("json_decision_router decisions must be declared in routes")
    raw_on_other = step.config.get("on_other")
    on_other = None
    if raw_on_other is not None:
        on_other = _required_config_string(step, "on_other", "json_decision_router")
        if on_other not in step.routes:
            raise PipelineDefinitionError(
                "json_decision_router.on_other route is not declared in routes"
            )
    return JsonDecisionRouterConfig(
        source=source,
        allowed_decisions=decisions,
        on_other=on_other,
    )


def _loop_guard_config(step) -> LoopGuardConfig:
    _reject_unknown_config_keys(step, _LOOP_GUARD_ALLOWED_KEYS, "loop_guard")
    on_allow = _required_config_string(step, "on_allow", "loop_guard")
    on_deny = _required_config_string(step, "on_deny", "loop_guard")
    for route_name in (on_allow, on_deny):
        if route_name not in step.routes:
            raise PipelineDefinitionError("loop_guard route is not declared in routes")
    return LoopGuardConfig(
        max_loops=_positive_config_integer(step, "max_loops", "loop_guard"),
        on_allow=on_allow,
        on_deny=on_deny,
    )


def _repeat_query_guard_config(step) -> RepeatQueryGuardConfig:
    _reject_unknown_config_keys(step, _REPEAT_QUERY_GUARD_ALLOWED_KEYS, "repeat_query_guard")
    source = _required_config_string(step, "source", "repeat_query_guard")
    if source not in _ROUTER_SOURCES:
        raise PipelineDefinitionError("repeat_query_guard.source is not allowed")
    parser = str(step.config.get("query_parser", "raw")).strip()
    if parser not in _REPEAT_QUERY_PARSERS:
        raise PipelineDefinitionError("repeat_query_guard.query_parser is not allowed")
    on_ok = _required_config_string(step, "on_ok", "repeat_query_guard")
    on_repeat = _required_config_string(step, "on_repeat", "repeat_query_guard")
    for route_name in (on_ok, on_repeat):
        if route_name not in step.routes:
            raise PipelineDefinitionError("repeat_query_guard route is not declared in routes")
    return RepeatQueryGuardConfig(
        source=source,
        query_parser=parser,
        on_ok=on_ok,
        on_repeat=on_repeat,
    )


def _model_transmission_policy_config(step) -> ModelTransmissionPolicyConfig:
    _reject_unknown_config_keys(
        step,
        _MODEL_TRANSMISSION_POLICY_ALLOWED_KEYS,
        "enforce_model_transmission_policy",
    )
    selected_model_server_id = _required_config_string(
        step,
        "selected_model_server_id",
        "enforce_model_transmission_policy",
    )
    raw_external_transmission = _required_config_string(
        step,
        "external_transmission",
        "enforce_model_transmission_policy",
    )
    try:
        external_transmission = ExternalTransmissionPolicy(raw_external_transmission)
    except ValueError as exc:
        raise PipelineDefinitionError(
            "enforce_model_transmission_policy.external_transmission must be allowed or forbidden"
        ) from exc
    return ModelTransmissionPolicyConfig(
        selected_model_server_id=selected_model_server_id,
        external_transmission=external_transmission,
    )


def _model_transmission_decision(
    config: ModelTransmissionPolicyConfig,
    context: PipelineContext,
) -> ModelTransmissionDiagnostic:
    checked_chunk_ids = tuple(hit.chunk.chunk_id for hit in context.hits)
    requirement = context_security_requirement(
        model=context.domain.security_model,
        hits=context.hits,
    )
    selected = _model_server_runtime(context, config.selected_model_server_id)
    selected_decision = _server_transmission_decision(
        runtime=selected,
        requirement=requirement,
        context=context,
        external_transmission=config.external_transmission,
    )
    if selected_decision.allowed:
        context.model = selected.gateway
        return _model_transmission_diagnostic(
            allowed=True,
            reason_code="model_server_allowed",
            selected_server_id=selected.definition.server_id,
            final_server_id=selected.definition.server_id,
            rerouted=False,
            trust_boundary=selected.definition.trust_boundary,
            external_transmission=config.external_transmission,
            requirement=requirement,
            checked_chunk_ids=checked_chunk_ids,
            blocked_chunk_ids=(),
        )
    reroute_id = selected.definition.security_reroute_server_id
    if reroute_id is None:
        return _model_transmission_diagnostic(
            allowed=False,
            reason_code=selected_decision.reason_code,
            selected_server_id=selected.definition.server_id,
            final_server_id=None,
            rerouted=False,
            trust_boundary=selected.definition.trust_boundary,
            external_transmission=config.external_transmission,
            requirement=requirement,
            checked_chunk_ids=checked_chunk_ids,
            blocked_chunk_ids=checked_chunk_ids,
        )
    reroute = _model_server_runtime(context, reroute_id)
    reroute_decision = _server_transmission_decision(
        runtime=reroute,
        requirement=requirement,
        context=context,
        external_transmission=config.external_transmission,
    )
    if not reroute_decision.allowed:
        return _model_transmission_diagnostic(
            allowed=False,
            reason_code=reroute_decision.reason_code,
            selected_server_id=selected.definition.server_id,
            final_server_id=reroute.definition.server_id,
            rerouted=True,
            trust_boundary=reroute.definition.trust_boundary,
            external_transmission=config.external_transmission,
            requirement=requirement,
            checked_chunk_ids=checked_chunk_ids,
            blocked_chunk_ids=checked_chunk_ids,
        )
    context.model = reroute.gateway
    return _model_transmission_diagnostic(
        allowed=True,
        reason_code="model_server_security_rerouted",
        selected_server_id=selected.definition.server_id,
        final_server_id=reroute.definition.server_id,
        rerouted=True,
        trust_boundary=reroute.definition.trust_boundary,
        external_transmission=config.external_transmission,
        requirement=requirement,
        checked_chunk_ids=checked_chunk_ids,
        blocked_chunk_ids=(),
    )


def _model_server_runtime(
    context: PipelineContext,
    server_id: str,
) -> ModelServerRuntime:
    try:
        return context.model_servers[server_id]
    except KeyError as exc:
        raise PipelineExecutionError(f"Model server is not configured: {server_id}") from exc


def _server_transmission_decision(
    *,
    runtime: ModelServerRuntime,
    requirement: ContextSecurityRequirement,
    context: PipelineContext,
    external_transmission: ExternalTransmissionPolicy,
):
    if runtime.definition.trust_boundary == TrustBoundary.EXTERNAL:
        if external_transmission == ExternalTransmissionPolicy.FORBIDDEN:
            return _Decision(False, "external_transmission_forbidden_by_pipeline")
        if not context.command.authorization.allow_external_model:
            return _Decision(False, "external_model_not_allowed_for_subject")
    decision = model_server_satisfies_requirement(
        model=context.domain.security_model,
        server=runtime.definition,
        requirement=requirement,
    )
    return _Decision(decision.allowed, decision.reason_code)


@dataclass(frozen=True)
class _Decision:
    allowed: bool
    reason_code: str


def _model_transmission_diagnostic(
    *,
    allowed: bool,
    reason_code: str,
    selected_server_id: str,
    final_server_id: str | None,
    rerouted: bool,
    trust_boundary: TrustBoundary | None,
    external_transmission: ExternalTransmissionPolicy,
    requirement: ContextSecurityRequirement,
    checked_chunk_ids: tuple[str, ...],
    blocked_chunk_ids: tuple[str, ...],
) -> ModelTransmissionDiagnostic:
    return ModelTransmissionDiagnostic(
        checked=True,
        allowed=allowed,
        reason_code=reason_code,
        selected_model_server_id=selected_server_id,
        final_model_server_id=final_server_id,
        rerouted=rerouted,
        trust_boundary=trust_boundary,
        external_transmission=external_transmission,
        context_security_requirement=requirement,
        checked_chunk_ids=checked_chunk_ids,
        blocked_chunk_ids=blocked_chunk_ids,
    )


def _call_model_config(step) -> CallModelConfig:
    _reject_unknown_config_keys(step, _CALL_MODEL_ALLOWED_KEYS, "call_model")
    config = step.config
    prompt_key = _required_config_string(step, "prompt_key", "call_model")
    raw_parts = config.get("user_parts")
    if not isinstance(raw_parts, Mapping) or not raw_parts:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.user_parts must be a non-empty mapping"
        )
    parts: list[UserPromptPart] = []
    for name, raw_part in raw_parts.items():
        if not isinstance(raw_part, Mapping):
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name} must be a mapping"
            )
        unknown_part_keys = set(raw_part) - _USER_PART_KEYS
        if unknown_part_keys:
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name} has unsupported fields: "
                f"{', '.join(sorted(unknown_part_keys))}"
            )
        source = _required_user_part_string(step, name, raw_part, "source")
        if source not in _USER_PART_SOURCES:
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name}.source is not allowed"
            )
        template = _required_user_part_template(step, name, raw_part)
        if "{}" not in template:
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name}.template must contain {{}}"
            )
        parts.append(UserPromptPart(name=name.strip(), source=source, template=template))
    generation_parameters = _generation_parameters(step)
    return CallModelConfig(
        prompt_key=prompt_key,
        user_parts=tuple(parts),
        generation_parameters=generation_parameters,
    )


def _reject_unknown_config_keys(step, allowed_keys: frozenset[str], action: str) -> None:
    unknown_keys = set(step.config) - allowed_keys
    if unknown_keys:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' has unsupported {action} fields: "
            f"{', '.join(sorted(unknown_keys))}"
        )


def _required_config_string(step, key: str, action: str) -> str:
    value = step.config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' {action}.{key} must be a non-empty string"
        )
    return value.strip()


def _required_rule_string(rule: Mapping[str, Any], key: str, field_name: str) -> str:
    value = rule.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(f"{field_name} must be a non-empty string")
    return value.strip()


def _route_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(f"{field_name} must be a non-empty string")
    return value.strip().lower()


def _positive_config_integer(step, key: str, action: str) -> int:
    value = step.config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' {action}.{key} must be a positive integer"
        )
    return value


def _state_value(source: str, context: PipelineContext) -> Any:
    values = {
        "answer": context.answer,
        "last_model_response": context.last_model_response,
        "normalized_query": context.normalized_query,
        "evidence": context.evidence,
        "context_chunk_ids": context.context_chunk_ids,
        "last_route": context.last_route,
        "last_prefix": context.last_prefix,
        "variables": dict(context.variables),
    }
    try:
        return values[source]
    except KeyError as exc:
        raise PipelineExecutionError(f"Unsupported state source '{source}'") from exc


def _set_state_value(target: str, value: Any, context: PipelineContext) -> None:
    if target == "answer":
        context.answer = _string_state_value(value, target)
        return
    if target == "last_model_response":
        context.last_model_response = _string_state_value(value, target)
        return
    if target == "normalized_query":
        context.normalized_query = _string_state_value(value, target)
        return
    if target == "evidence":
        context.evidence = _string_state_value(value, target)
        return
    if target == "variables":
        if not isinstance(value, Mapping):
            raise PipelineExecutionError("variables must be assigned from a mapping")
        context.variables = dict(value)
        return
    raise PipelineExecutionError(f"Unsupported state target '{target}'")


def _string_state_value(value: Any, target: str) -> str:
    if not isinstance(value, str):
        raise PipelineExecutionError(f"{target} must be assigned from a string")
    return value


def _transform_value(transform: str, value: Any) -> Any:
    if transform == "copy":
        return value
    if transform == "clear":
        return _clear_value(value)
    if transform == "to_list":
        return _to_list(value)
    if transform == "split_lines":
        return _split_lines(value)
    if transform == "parse_json":
        return _parse_json_value(value)
    raise PipelineExecutionError(f"Unsupported set_variables transform '{transform}'")


def _clear_value(value: Any) -> Any:
    if isinstance(value, str):
        return ""
    if isinstance(value, Mapping):
        return {}
    if isinstance(value, tuple | list | set):
        return []
    return None


def _to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    raise PipelineExecutionError("to_list requires null, string, or list-like input")


def _split_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        raise PipelineExecutionError("split_lines requires string input")
    return [line.strip() for line in value.splitlines() if line.strip()]


def _parse_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        raise PipelineExecutionError("parse_json requires string input")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise PipelineExecutionError("parse_json received invalid JSON") from exc


def _json_decision(payload: Mapping[str, Any]) -> str:
    for key in ("decision", "route", "mode"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _guard_query(config: RepeatQueryGuardConfig, context: PipelineContext) -> str:
    raw = str(_state_value(config.source, context) or "")
    if config.query_parser == "raw":
        return raw
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PipelineExecutionError("repeat_query_guard received invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PipelineExecutionError("repeat_query_guard JSON payload must be an object")
    query = payload.get("query", "")
    if not isinstance(query, str):
        raise PipelineExecutionError("repeat_query_guard query must be a string")
    return query


def _normalize_guard_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().lower())


def _required_user_part_string(step, part_name: str, part: Mapping[str, Any], key: str) -> str:
    value = part.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.user_parts.{part_name}.{key} "
            "must be a non-empty string"
        )
    return value.strip()


def _required_user_part_template(step, part_name: str, part: Mapping[str, Any]) -> str:
    value = part.get("template")
    if not isinstance(value, str) or not value:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.user_parts.{part_name}.template "
            "must be a non-empty string"
        )
    return value


def _generation_parameters(step) -> Mapping[str, Any]:
    if "max_tokens" in step.config and "max_output_tokens" in step.config:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' cannot define both max_tokens and max_output_tokens"
        )
    parameters: dict[str, Any] = {}
    if "temperature" in step.config:
        parameters["temperature"] = _bounded_number(step, "temperature", minimum=0, maximum=2)
    if "top_p" in step.config:
        parameters["top_p"] = _bounded_number(step, "top_p", minimum=0, maximum=1)
    if "max_tokens" in step.config:
        parameters["max_tokens"] = _positive_integer(step, "max_tokens")
    if "max_output_tokens" in step.config:
        parameters["max_tokens"] = _positive_integer(step, "max_output_tokens")
    return parameters


def _bounded_number(step, key: str, *, minimum: float, maximum: float) -> float:
    value = step.config[key]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise PipelineDefinitionError(f"Step '{step.step_id}' call_model.{key} must be a number")
    normalized = float(value)
    if normalized < minimum or normalized > maximum:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.{key} must be between {minimum} and {maximum}"
        )
    return normalized


def _positive_integer(step, key: str) -> int:
    value = step.config[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.{key} must be a positive integer"
        )
    return value


def _render_user_prompt(parts: Sequence[UserPromptPart], context: PipelineContext) -> str:
    rendered = []
    for part in parts:
        rendered.append(part.template.format(_source_value(part.source, context)))
    return "".join(rendered)


def _source_value(source: str, context: PipelineContext) -> str:
    if source == "normalized_query":
        return context.normalized_query
    if source == "evidence":
        return context.evidence
    if source == "context_chunk_ids":
        return "\n".join(context.context_chunk_ids)
    if source == "citations_text":
        return _citations_text(context.citations)
    if source == "retrieval_trace_summary":
        return _retrieval_trace_summary(context.hits)
    raise PipelineExecutionError(f"Unsupported call_model source '{source}'")


def _citations_text(citations: Sequence[Citation]) -> str:
    return "\n".join(
        (
            f"{index}. source_id={citation.source_id}; chunk_id={citation.chunk_id}; "
            f"{citation.score_type}={citation.score:.6f}; uri={citation.source_uri}"
        )
        for index, citation in enumerate(citations, start=1)
    )


def _retrieval_trace_summary(hits: Sequence[SearchHit]) -> str:
    return "\n".join(
        (
            f"{index}. source_id={hit.chunk.source_id}; chunk_id={hit.chunk.chunk_id}; "
            f"{hit.score_type}={hit.score:.6f}"
        )
        for index, hit in enumerate(hits, start=1)
    )


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
            ManageContextBudgetAction(),
            RequireEvidenceAction(),
            EnforceModelTransmissionPolicyAction(),
            SetVariablesAction(),
            PrefixRouterAction(),
            JsonDecisionRouterAction(),
            LoopGuardAction(),
            RepeatQueryGuardAction(),
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
    authorization,
    hits: Sequence[SearchHit],
) -> None:
    allowed_labels = set(authorization.acl_labels)
    for hit in hits:
        chunk = hit.chunk
        if chunk.domain != domain.name or chunk.tenant_id != authorization.tenant_id:
            raise PolicyViolationError("Retrieval provider crossed a domain or tenant boundary")
        if chunk.acl_labels and not set(chunk.acl_labels).intersection(allowed_labels):
            raise PolicyViolationError("Retrieval provider returned a forbidden ACL label")
        security_decision = document_visible_to_subject(
            model=domain.security_model,
            document=chunk.security,
            subject=authorization.security,
        )
        if not security_decision.allowed:
            raise PolicyViolationError("Retrieval provider returned forbidden security metadata")


def _build_context(
    hits: Sequence[SearchHit],
    maximum: int,
) -> tuple[str, tuple[Citation, ...], tuple[str, ...], tuple[str, ...]]:
    used = 0
    blocks: list[str] = []
    citations: list[Citation] = []
    chunk_ids: list[str] = []
    omitted: list[str] = []
    for index, hit in enumerate(hits):
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
        chunk_ids.append(hit.chunk.chunk_id)
        if len(selected) < len(block):
            omitted.extend(item.chunk.chunk_id for item in hits[index + 1 :])
            break
    evidence = "\n\n---\n\n".join(blocks) or "No relevant evidence was retrieved."
    return evidence, tuple(citations), tuple(chunk_ids), tuple(omitted)


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
