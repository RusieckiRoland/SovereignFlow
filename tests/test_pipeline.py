from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import (
    StubAudit,
    StubGraph,
    StubModel,
    StubPrompts,
    StubRetrieval,
    authorization_context,
    default_pipeline,
    model_servers,
)

from sovereignflow.application import (
    ActionRegistry,
    ConversationHistoryService,
    PipelineContext,
    PipelineEngine,
    PipelineValidator,
    default_action_registry,
)
from sovereignflow.application import pipeline as pipeline_module
from sovereignflow.application.actions import _conversation as conversation_module
from sovereignflow.application.actions import _retrieval as retrieval_module
from sovereignflow.application.actions import _state as state_module
from sovereignflow.application.actions import call_model as call_model_module
from sovereignflow.application.actions import json_decision_router as json_decision_router_module
from sovereignflow.application.actions import retrieve as retrieve_module
from sovereignflow.domain import (
    ConversationTurnStatus,
    PipelineDefinition,
    PipelineDefinitionError,
    PipelineExecutionError,
    PipelineStepDefinition,
    PolicyViolationError,
    ProviderProtocolError,
    QueryCommand,
    SearchMode,
    TrustBoundary,
)
from sovereignflow.infrastructure import YamlPipelineRepository


class Action:
    action_id = "action"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset({"result"})

    def execute(self, step, context) -> None:
        context.answer = "done"


def definition(*steps, entry: str = "start", maximum: int = 10) -> PipelineDefinition:
    return PipelineDefinition(
        name="test",
        behavior_version="1.0",
        entry_step_id=entry,
        max_steps=maximum,
        steps=steps,
        checksum="b" * 64,
    )


def step(
    step_id: str,
    action: str = "action",
    *,
    next_step: str | None = None,
    routes: dict[str, str] | None = None,
    terminal: bool = True,
    version: str = "1.0",
    config: dict | None = None,
) -> PipelineStepDefinition:
    action_version = (
        "2.0" if version == "1.0" and action == "enforce_model_transmission_policy" else version
    )
    return PipelineStepDefinition(
        step_id=step_id,
        action=action,
        action_version=action_version,
        next_step_id=next_step,
        routes=routes or {},
        terminal=terminal,
        config=config or {},
    )


def call_model_config(**overrides) -> dict:
    values = {
        "prompt_key": "answer",
        "user_parts": {
            "user_question": {
                "source": "normalized_query",
                "template": "Q:{}\n",
            },
            "evidence": {
                "source": "evidence",
                "template": "E:{}",
            },
        },
    }
    values.update(overrides)
    return values


def retrieve_config(**overrides) -> dict:
    values = {
        "query_source": "normalized_query",
        "search_mode": "hybrid",
        "top_k": 3,
        "filters": {},
    }
    values.update(overrides)
    return values


def expand_graph_config(**overrides) -> dict:
    values = {
        "enabled": True,
        "max_depth": 2,
        "max_nodes": 10,
        "direction": "both",
        "relationship_types": ["references"],
    }
    values.update(overrides)
    return values


def context_budget_config(**overrides) -> dict:
    values = {
        "source": "hits",
        "target": "evidence",
        "max_context_characters": 500,
    }
    values.update(overrides)
    return values


def model_transmission_config(**overrides) -> dict:
    values = {
        "selected_model_server_id": "default-model",
        "external_transmission": "allowed",
    }
    values.update(overrides)
    return values


def set_variables_config(**overrides) -> dict:
    values = {
        "rules": [
            {
                "set": "answer",
                "from": "last_model_response",
                "transform": "copy",
            }
        ]
    }
    values.update(overrides)
    return values


def prefix_router_config(**overrides) -> dict:
    values = {
        "source": "last_model_response",
        "prefixes": {"direct": "[DIRECT:]", "retrieve": "[RETRIEVE:]"},
        "on_other": "direct",
    }
    values.update(overrides)
    return values


def json_router_config(**overrides) -> dict:
    values = {
        "source": "last_model_response",
        "allowed_decisions": ["direct", "retrieve"],
        "on_other": "direct",
    }
    values.update(overrides)
    return values


def loop_guard_config(**overrides) -> dict:
    values = {"max_loops": 2, "on_allow": "again", "on_deny": "stop"}
    values.update(overrides)
    return values


def repeat_query_guard_config(**overrides) -> dict:
    values = {
        "source": "last_model_response",
        "query_parser": "raw",
        "on_ok": "new",
        "on_repeat": "repeat",
    }
    values.update(overrides)
    return values


def test_action_registry_rejects_duplicates_and_unknown_actions() -> None:
    with pytest.raises(PipelineDefinitionError, match="unique"):
        ActionRegistry((Action(), Action()))

    registry = ActionRegistry((Action(),))
    assert registry.actions == {"action": registry.get("action")}
    with pytest.raises(PipelineDefinitionError, match="Unknown"):
        registry.get("missing")


@pytest.mark.parametrize(
    ("pipeline", "message"),
    [
        (
            definition(step("start"), step("start")),
            "identifiers",
        ),
        (
            definition(step("start"), entry="missing"),
            "entry",
        ),
        (
            definition(step("start", version="2.0")),
            "version mismatch",
        ),
        (
            definition(step("start", next_step="missing", terminal=False)),
            "unknown step",
        ),
        (
            definition(
                step(
                    "start",
                    routes={"selected": "missing"},
                    terminal=False,
                )
            ),
            "route",
        ),
        (
            definition(
                step("start", next_step="second", terminal=False),
                step("second", next_step="start", terminal=False),
            ),
            "cycle",
        ),
        (
            definition(step("start", action="call_model", config=call_model_config())),
            "requires unavailable",
        ),
        (
            definition(step("start"), step("unused")),
            "unreachable",
        ),
        (
            definition(step("start", action="normalize_query")),
            "does not produce",
        ),
        (
            definition(
                step("start", next_step="end", terminal=False),
                step("end"),
                maximum=1,
            ),
            "exceeds max_steps",
        ),
    ],
)
def test_pipeline_validator_rejects_invalid_definitions(pipeline, message: str) -> None:
    registry = (
        default_action_registry()
        if pipeline.steps[0].action != "action"
        else ActionRegistry((Action(),))
    )

    with pytest.raises(PipelineDefinitionError, match=message):
        PipelineValidator(registry).validate(pipeline)


def test_pipeline_validator_accepts_default_pipeline() -> None:
    PipelineValidator(default_action_registry()).validate(default_pipeline())


def test_every_builtin_action_has_documentation() -> None:
    action_docs = Path("docs/Action")

    for action_id in default_action_registry().actions:
        assert (action_docs / f"{action_id}.md").is_file()


def test_pipeline_step_config_is_available_to_actions(domain_profile) -> None:
    class ConfiguredAction(Action):
        action_id = "configured"

        def execute(self, step, context) -> None:
            context.answer = f"{step.config['message']}:{step.config['nested']['items'][0]}"

    pipeline = definition(
        step(
            "start",
            action="configured",
            config={
                "message": "ok",
                "nested": {"items": ["one"]},
            },
        )
    )
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    result = PipelineEngine(
        registry=ActionRegistry((ConfiguredAction(),)),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000008",
    ).execute(pipeline, context)

    assert result.answer == "ok:one"
    assert pipeline.steps[0].config["nested"]["items"] == ("one",)


@pytest.mark.parametrize(
    ("action", "config", "message"),
    [
        ("retrieve", {}, "query_source"),
        ("retrieve", retrieve_config(query_source="domain"), "query_source"),
        ("retrieve", retrieve_config(search_mode="vector"), "search_mode"),
        ("retrieve", retrieve_config(top_k=0), "top_k"),
        ("retrieve", retrieve_config(filters=[]), "filters"),
        ("retrieve", retrieve_config(extra=True), "unsupported"),
        ("expand_graph", {}, "enabled"),
        ("expand_graph", expand_graph_config(enabled="yes"), "enabled"),
        ("expand_graph", expand_graph_config(max_depth=0), "max_depth"),
        ("expand_graph", expand_graph_config(max_nodes=0), "max_nodes"),
        ("expand_graph", expand_graph_config(direction="sideways"), "direction"),
        ("expand_graph", expand_graph_config(relationship_types=[""]), "relationship_types"),
        (
            "expand_graph",
            expand_graph_config(relationship_types="references"),
            "relationship_types",
        ),
        ("expand_graph", expand_graph_config(extra=True), "unsupported"),
        ("manage_context_budget", {}, "source"),
        ("manage_context_budget", context_budget_config(source="documents"), "source"),
        ("manage_context_budget", context_budget_config(target="prompt"), "target"),
        ("manage_context_budget", context_budget_config(max_context_characters=0), "max_context"),
        ("manage_context_budget", context_budget_config(extra=True), "unsupported"),
        ("enforce_model_transmission_policy", {}, "selected_model_server_id"),
        (
            "enforce_model_transmission_policy",
            model_transmission_config(selected_model_server_id=" "),
            "selected_model_server_id",
        ),
        (
            "enforce_model_transmission_policy",
            model_transmission_config(external_transmission="maybe"),
            "external_transmission",
        ),
        (
            "enforce_model_transmission_policy",
            model_transmission_config(restricted_acl_labels=["restricted"]),
            "unsupported",
        ),
    ],
)
def test_retrieval_graph_and_context_actions_reject_invalid_yaml_contracts(
    action,
    config,
    message: str,
) -> None:
    pipeline = definition(step("start", action=action, config=config))

    with pytest.raises(PipelineDefinitionError, match=message):
        PipelineValidator(default_action_registry()).validate(pipeline)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "prompt_key"),
        (call_model_config(prompt_key=" "), "prompt_key"),
        (call_model_config(user_parts={}), "user_parts"),
        (
            call_model_config(user_parts={"bad": {"source": "domain", "template": "{}"}}),
            "source is not allowed",
        ),
        (call_model_config(user_parts={"bad": "not-a-mapping"}), "must be a mapping"),
        (call_model_config(user_parts={"bad": {"template": "{}"}}), "source"),
        (call_model_config(user_parts={"bad": {"source": "evidence"}}), "template"),
        (call_model_config(user_parts={"bad": {"source": "evidence", "template": ""}}), "template"),
        (
            call_model_config(user_parts={"bad": {"source": "evidence", "template": "plain"}}),
            "template",
        ),
        (
            call_model_config(user_parts={"bad": {"source": "evidence", "template": "{}", "x": 1}}),
            "unsupported fields",
        ),
        (call_model_config(temperature=3), "temperature"),
        (call_model_config(temperature="cold"), "temperature"),
        (call_model_config(top_p=2), "top_p"),
        (call_model_config(max_tokens=0), "max_tokens"),
        (call_model_config(max_output_tokens=True), "max_output_tokens"),
        (call_model_config(max_tokens=1, max_output_tokens=1), "both"),
        (call_model_config(legacy_prompt_name="answer"), "unsupported"),
    ],
)
def test_call_model_rejects_invalid_yaml_contract(config, message: str) -> None:
    pipeline = definition(
        step(
            "start",
            action="call_model",
            config=config,
        )
    )

    with pytest.raises(PipelineDefinitionError, match=message):
        PipelineValidator(default_action_registry()).validate(pipeline)


@pytest.mark.parametrize(
    ("action", "config", "routes", "message"),
    [
        ("set_variables", {}, {}, "rules"),
        ("set_variables", {"rules": []}, {}, "rules"),
        ("set_variables", {"rules": ["bad"]}, {}, "rules"),
        (
            "set_variables",
            {"rules": [{"set": "answer", "from": "last_model_response", "value": "x"}]},
            {},
            "exactly one",
        ),
        ("set_variables", {"rules": [{"set": "command.authorization", "value": "x"}]}, {}, "dot"),
        ("set_variables", {"rules": [{"set": "tenant_id", "value": "x"}]}, {}, "target"),
        (
            "set_variables",
            {"rules": [{"set": "answer", "from": "command.authorization"}]},
            {},
            "dot",
        ),
        ("set_variables", {"rules": [{"set": "answer", "from": "domain"}]}, {}, "source"),
        ("set_variables", {"rules": [{"set": " ", "value": "x"}]}, {}, "set"),
        (
            "set_variables",
            {"rules": [{"set": "answer", "value": "x", "transform": "magic"}]},
            {},
            "transform",
        ),
        (
            "set_variables",
            {"rules": [{"set": "answer", "value": "x", "unexpected": True}]},
            {},
            "unsupported",
        ),
        ("prefix_router", {}, {"direct": "end"}, "source"),
        ("prefix_router", prefix_router_config(source="domain"), {"direct": "end"}, "source"),
        ("prefix_router", prefix_router_config(prefixes={}), {"direct": "end"}, "prefixes"),
        (
            "prefix_router",
            prefix_router_config(prefixes={"direct": ""}),
            {"direct": "end"},
            "prefix",
        ),
        (
            "prefix_router",
            prefix_router_config(prefixes={"missing": "[M:]"}),
            {"direct": "end"},
            "declared",
        ),
        (
            "prefix_router",
            prefix_router_config(on_other="missing"),
            {"direct": "end", "retrieve": "end"},
            "declared",
        ),
        ("json_decision_router", {}, {"direct": "end"}, "source"),
        (
            "json_decision_router",
            json_router_config(source="domain"),
            {"direct": "end", "retrieve": "end"},
            "source",
        ),
        (
            "json_decision_router",
            json_router_config(allowed_decisions=[]),
            {"direct": "end"},
            "allowed_decisions",
        ),
        (
            "json_decision_router",
            json_router_config(allowed_decisions=["missing"]),
            {"direct": "end"},
            "declared",
        ),
        (
            "json_decision_router",
            json_router_config(allowed_decisions=[None]),
            {"direct": "end"},
            "decision",
        ),
        (
            "json_decision_router",
            json_router_config(on_other="missing"),
            {"direct": "end", "retrieve": "end"},
            "declared",
        ),
        ("loop_guard", {}, {"again": "end"}, "on_allow"),
        ("loop_guard", loop_guard_config(max_loops=0), {"again": "end", "stop": "end"}, "max"),
        ("loop_guard", loop_guard_config(max_loops=True), {"again": "end", "stop": "end"}, "max"),
        ("loop_guard", loop_guard_config(on_deny="missing"), {"again": "end"}, "declared"),
        ("repeat_query_guard", {}, {"new": "end"}, "source"),
        (
            "repeat_query_guard",
            repeat_query_guard_config(source="domain"),
            {"new": "end", "repeat": "end"},
            "source",
        ),
        (
            "repeat_query_guard",
            repeat_query_guard_config(query_parser="jsonish"),
            {"new": "end", "repeat": "end"},
            "query_parser",
        ),
        (
            "repeat_query_guard",
            repeat_query_guard_config(on_repeat="missing"),
            {"new": "end"},
            "declared",
        ),
    ],
)
def test_routing_guard_and_state_actions_reject_invalid_yaml_contracts(
    action,
    config,
    routes,
    message: str,
) -> None:
    transitions = (
        {"routes": routes, "terminal": False} if routes else {"next_step": "end", "terminal": False}
    )
    pipeline = definition(
        step("start", action=action, config=config, **transitions),
        step("end", action="finalize"),
    )

    with pytest.raises(PipelineDefinitionError, match=message):
        PipelineValidator(default_action_registry()).validate(pipeline)


def test_call_model_uses_yaml_prompt_and_user_parts(domain_profile, search_hit) -> None:
    model = StubModel(answer="grounded")
    prompts = StubPrompts("yaml system")
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=model,
        prompts=prompts,
        normalized_query="normalized",
        evidence="evidence",
        hits=(search_hit,),
        citations=(),
        context_chunk_ids=("chunk-1",),
        model_transmission_checked=True,
        model_transmission_allowed=True,
    )
    pipeline = definition(
        step(
            "start",
            action="call_model",
            config=call_model_config(
                prompt_key="general/strict_answer",
                user_parts={
                    "question": {"source": "normalized_query", "template": "Q:{}\n"},
                    "chunks": {"source": "context_chunk_ids", "template": "C:{}\n"},
                    "citations": {"source": "citations_text", "template": "S:{}\n"},
                    "trace": {"source": "retrieval_trace_summary", "template": "T:{}"},
                },
                temperature=0,
                top_p=1,
                max_tokens=64,
            ),
        )
    )

    result = PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000009",
    ).execute(pipeline, context)

    assert result.answer == "grounded"
    assert prompts.names == ["general/strict_answer"]
    assert model.calls == [
        {
            "system_prompt": "yaml system",
            "user_prompt": "Q:normalized\nC:chunk-1\nS:\nT:1. source_id=source-1; "
            "chunk_id=chunk-1; hybrid=0.750000",
            "generation_parameters": {"temperature": 0.0, "top_p": 1.0, "max_tokens": 64},
        }
    ]
    assert result.diagnostics is not None
    assert result.diagnostics.prompt_key == "general/strict_answer"


def test_call_model_rejects_missing_transmission_policy(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        normalized_query="question",
        evidence="evidence",
    )

    with pytest.raises(PipelineExecutionError, match="policy has not been enforced"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000044",
        ).execute(
            definition(step("start", action="call_model", config=call_model_config())),
            context,
        )


def test_call_model_rejects_denied_transmission_policy(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        normalized_query="question",
        evidence="evidence",
        model_transmission_checked=True,
        model_transmission_allowed=False,
    )

    with pytest.raises(PolicyViolationError, match="Model transmission policy"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000050",
        ).execute(
            definition(step("start", action="call_model", config=call_model_config())),
            context,
        )


def test_model_transmission_policy_ignores_acl_for_model_server_security(
    domain_profile,
    search_hit,
) -> None:
    restricted_hit = replace(
        search_hit,
        chunk=replace(search_hit.chunk, acl_labels=("public", "restricted")),
    )
    model = StubModel(scope="external")
    context = PipelineContext(
        command=QueryCommand(
            "request",
            "question",
            "general",
            "session",
            authorization_context(allow_external_model=True, acl_labels=("public", "restricted")),
        ),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=model,
        prompts=StubPrompts(),
        model_servers=model_servers(
            default=model,
            trust_boundary=TrustBoundary.EXTERNAL,
            clearance_label="INTERNAL",
        ),
        hits=(restricted_hit,),
        evidence="restricted evidence",
        citations=(),
    )

    PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000045",
    ).execute(
        definition(
            step(
                "policy",
                action="enforce_model_transmission_policy",
                config=model_transmission_config(),
            ),
            entry="policy",
        ),
        context,
    )

    assert context.model_transmission_allowed is True
    assert context.model_transmission_reason_code == "model_server_allowed"
    assert context.model_transmission_blocked_chunk_ids == ()


def test_model_transmission_policy_blocks_server_with_too_low_clearance(
    domain_profile,
    search_hit,
) -> None:
    restricted_hit = replace(
        search_hit,
        chunk=replace(
            search_hit.chunk,
            security=replace(search_hit.chunk.security, clearance_label="INTERNAL"),
        ),
    )
    model = StubModel(scope="external")
    context = PipelineContext(
        command=QueryCommand(
            "request",
            "question",
            "general",
            "session",
            authorization_context(allow_external_model=True),
        ),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=model,
        prompts=StubPrompts(),
        model_servers=model_servers(
            default=model,
            trust_boundary=TrustBoundary.EXTERNAL,
            clearance_label="PUBLIC",
        ),
        hits=(restricted_hit,),
        evidence="restricted evidence",
        citations=(),
    )

    with pytest.raises(PolicyViolationError, match="model_server_clearance_too_low"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000047",
        ).execute(
            definition(
                step(
                    "policy",
                    action="enforce_model_transmission_policy",
                    config=model_transmission_config(),
                ),
                entry="policy",
            ),
            context,
        )

    assert context.model_transmission_allowed is False
    assert context.model_transmission_reason_code == "model_server_clearance_too_low"
    assert context.model_transmission_blocked_chunk_ids == ("chunk-1",)


def test_model_transmission_policy_reroutes_to_permitted_internal_server(
    domain_profile,
    search_hit,
) -> None:
    primary = StubModel(scope="external")
    reroute = StubModel(scope="internal")
    internal_hit = replace(
        search_hit,
        chunk=replace(
            search_hit.chunk,
            security=replace(search_hit.chunk.security, clearance_label="INTERNAL"),
        ),
    )
    context = PipelineContext(
        command=QueryCommand(
            "request",
            "question",
            "general",
            "session",
            authorization_context(allow_external_model=True),
        ),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=primary,
        prompts=StubPrompts(),
        model_servers=model_servers(
            default=primary,
            trust_boundary=TrustBoundary.EXTERNAL,
            clearance_label="PUBLIC",
            reroute_to="internal-secure",
            reroute_model=reroute,
            reroute_clearance_label="INTERNAL",
        ),
        hits=(internal_hit,),
        evidence="public evidence",
        citations=(),
    )

    PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000048",
    ).execute(
        definition(
            step(
                "policy",
                action="enforce_model_transmission_policy",
                config=model_transmission_config(),
            ),
            entry="policy",
        ),
        context,
    )

    assert context.model_transmission_allowed is True
    assert context.model_transmission_reason_code == "model_server_security_rerouted"
    assert context.model_transmission_final_server_id == "internal-secure"
    assert context.model is reroute


def test_model_transmission_policy_blocks_when_reroute_is_not_permitted(
    domain_profile,
    search_hit,
) -> None:
    primary = StubModel(scope="external")
    reroute = StubModel(scope="internal")
    internal_hit = replace(
        search_hit,
        chunk=replace(
            search_hit.chunk,
            security=replace(search_hit.chunk.security, clearance_label="INTERNAL"),
        ),
    )
    context = PipelineContext(
        command=QueryCommand(
            "request",
            "question",
            "general",
            "session",
            authorization_context(allow_external_model=True),
        ),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=primary,
        prompts=StubPrompts(),
        model_servers=model_servers(
            default=primary,
            trust_boundary=TrustBoundary.EXTERNAL,
            clearance_label="PUBLIC",
            reroute_to="internal-low",
            reroute_model=reroute,
            reroute_clearance_label="PUBLIC",
        ),
        hits=(internal_hit,),
        evidence="internal evidence",
        citations=(),
    )

    with pytest.raises(PolicyViolationError, match="model_server_clearance_too_low"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000052",
        ).execute(
            definition(
                step(
                    "policy",
                    action="enforce_model_transmission_policy",
                    config=model_transmission_config(),
                ),
                entry="policy",
            ),
            context,
        )

    assert context.model_transmission_rerouted is True
    assert context.model_transmission_final_server_id == "internal-low"
    assert context.model is primary


def test_model_transmission_policy_rejects_unknown_model_server(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PipelineExecutionError, match="Model server is not configured"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000053",
        ).execute(
            definition(
                step(
                    "policy",
                    action="enforce_model_transmission_policy",
                    config=model_transmission_config(),
                ),
                entry="policy",
            ),
            context,
        )


def test_model_transmission_policy_blocks_external_when_user_is_not_allowed(
    domain_profile,
    search_hit,
) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(scope="external"),
        prompts=StubPrompts(),
        model_servers=model_servers(
            default=StubModel(scope="external"),
            trust_boundary=TrustBoundary.EXTERNAL,
            clearance_label="INTERNAL",
        ),
        hits=(search_hit,),
        evidence="public evidence",
        citations=(),
    )

    with pytest.raises(PolicyViolationError, match="external_model_not_allowed_for_subject"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000049",
        ).execute(
            definition(
                step(
                    "policy",
                    action="enforce_model_transmission_policy",
                    config=model_transmission_config(),
                ),
                entry="policy",
            ),
            context,
        )

    assert context.model_transmission_reason_code == "external_model_not_allowed_for_subject"


def test_model_transmission_policy_blocks_external_when_pipeline_forbids_it(
    domain_profile,
    search_hit,
) -> None:
    model = StubModel(scope="external")
    context = PipelineContext(
        command=QueryCommand(
            "request",
            "question",
            "general",
            "session",
            authorization_context(allow_external_model=True),
        ),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=model,
        prompts=StubPrompts(),
        model_servers=model_servers(
            default=model,
            trust_boundary=TrustBoundary.EXTERNAL,
            clearance_label="INTERNAL",
        ),
        hits=(search_hit,),
        evidence="public evidence",
        citations=(),
    )

    with pytest.raises(PolicyViolationError, match="external_transmission_forbidden_by_pipeline"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000051",
        ).execute(
            definition(
                step(
                    "policy",
                    action="enforce_model_transmission_policy",
                    config=model_transmission_config(external_transmission="forbidden"),
                ),
                entry="policy",
            ),
            context,
        )

    assert context.model_transmission_allowed is False
    assert context.model_transmission_reason_code == "external_transmission_forbidden_by_pipeline"


def test_call_model_rejects_unvalidated_source_at_runtime(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PipelineExecutionError, match="Unsupported call_model source"):
        call_model_module._source_value("domain", context)


def test_retrieve_action_reads_query_mode_top_k_and_filters_from_yaml(domain_profile) -> None:
    retrieval = StubRetrieval()
    context = PipelineContext(
        command=QueryCommand(
            "request",
            " raw query ",
            "general",
            "session",
            authorization_context(),
            filters={"country": "PL"},
        ),
        domain=domain_profile,
        retrieval=retrieval,
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )
    pipeline = definition(
        step(
            "start",
            action="retrieve",
            config=retrieve_config(
                query_source="command_query",
                search_mode="bm25",
                top_k=7,
                filters={"status": "yaml"},
            ),
        )
    )

    PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000010",
    ).execute(pipeline, context)

    assert retrieval.requests[0].query == "raw query"
    assert retrieval.requests[0].mode == SearchMode.BM25
    assert retrieval.requests[0].top_k == 7
    assert dict(retrieval.requests[0].filters) == {"country": "PL", "status": "yaml"}


def test_retrieve_action_rejects_unvalidated_query_source_at_runtime(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PipelineExecutionError, match="Unsupported retrieve query source"):
        retrieve_module._retrieval_query("domain", context)


def test_set_variables_maps_safe_state_fields_and_transforms(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response='{"answer":"ok"}',
    )
    pipeline = definition(
        step(
            "set_variables",
            action="set_variables",
            config={
                "rules": [
                    {"set": "variables", "from": "last_model_response", "transform": "parse_json"},
                    {
                        "set": "last_model_response",
                        "value": "line 1\n\nline 2",
                        "transform": "copy",
                    },
                    {"set": "answer", "value": "done"},
                ]
            },
        ),
        entry="set_variables",
    )

    result = PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000011",
    ).execute(pipeline, context)

    assert context.variables == {"answer": "ok"}
    assert context.last_model_response == "line 1\n\nline 2"
    assert result.answer == "done"


@pytest.mark.parametrize(
    ("transform", "value", "expected"),
    [
        ("to_list", "item", ["item"]),
        ("to_list", "", []),
        ("split_lines", " a\n\n b ", ["a", "b"]),
        ("clear", {"a": 1}, {}),
        ("clear", ("a",), []),
        ("clear", 1, None),
    ],
)
def test_set_variables_transform_values(transform, value, expected) -> None:
    assert state_module._transform_value(transform, value) == expected


@pytest.mark.parametrize(
    ("transform", "value", "message"),
    [
        ("to_list", 1, "to_list"),
        ("split_lines", 1, "split_lines"),
        ("parse_json", 1, "parse_json"),
        ("parse_json", "{", "invalid JSON"),
        ("missing", "x", "Unsupported"),
    ],
)
def test_set_variables_transform_errors(transform, value, message: str) -> None:
    with pytest.raises(PipelineExecutionError, match=message):
        state_module._transform_value(transform, value)


def test_set_variables_rejects_unsafe_runtime_target(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PipelineExecutionError, match="Unsupported state target"):
        state_module._set_state_value("tenant_id", "tenant-b", context)

    with pytest.raises(PipelineExecutionError, match="Unsupported state source"):
        state_module._state_value("tenant_id", context)

    with pytest.raises(PipelineExecutionError, match="variables"):
        state_module._set_state_value("variables", "not-a-mapping", context)

    with pytest.raises(PipelineExecutionError, match="answer"):
        state_module._set_state_value("answer", {"not": "string"}, context)

    state_module._set_state_value("normalized_query", "normalized", context)
    state_module._set_state_value("evidence", "evidence", context)
    state_module._set_state_value("variables", {"safe": True}, context)
    assert context.normalized_query == "normalized"
    assert context.evidence == "evidence"
    assert context.variables == {"safe": True}


def test_routing_helper_edge_cases() -> None:
    assert state_module._clear_value("text") == ""
    assert state_module._to_list(None) == []
    assert state_module._to_list(["a"]) == ["a"]
    assert state_module._to_list(("a",)) == ["a"]
    assert state_module._split_lines(None) == []
    assert json_decision_router_module._json_decision({"mode": " Graph "}) == "graph"
    assert json_decision_router_module._json_decision({"other": "value"}) == ""
    assert retrieval_module._normalize_guard_query(" A   B ") == "a b"


def test_prefix_router_matches_prefix_and_cleans_payload(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response=" [RETRIEVE:] find orders ",
    )
    pipeline = definition(
        step(
            "router",
            action="prefix_router",
            terminal=False,
            routes={"direct": "end", "retrieve": "end"},
            config=prefix_router_config(),
        ),
        step("end", action="finalize"),
        entry="router",
    )

    result = PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000012",
    ).execute(pipeline, context)

    assert result.pipeline_trace == ("router", "end")
    assert context.last_route == "retrieve"
    assert context.last_prefix == "retrieve"
    assert context.last_model_response == "find orders"


def test_prefix_router_uses_on_other_for_unknown_prefix(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response="answer without marker",
    )
    pipeline = definition(
        step(
            "router",
            action="prefix_router",
            terminal=False,
            routes={"direct": "end", "retrieve": "end"},
            config=prefix_router_config(),
        ),
        step("end", action="finalize"),
        entry="router",
    )

    PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000013",
    ).execute(pipeline, context)

    assert context.last_route == "direct"
    assert context.last_prefix == ""
    assert context.last_model_response == "answer without marker"


def test_json_decision_router_routes_and_cleans_payload(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response='{"decision":"retrieve","query":"orders","mode":"ignored"}',
    )
    pipeline = definition(
        step(
            "router",
            action="json_decision_router",
            terminal=False,
            routes={"direct": "end", "retrieve": "end"},
            config=json_router_config(),
        ),
        step("end", action="finalize"),
        entry="router",
    )

    PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000014",
    ).execute(pipeline, context)

    assert context.last_route == "retrieve"
    assert context.last_model_response == '{"query":"orders"}'


@pytest.mark.parametrize("payload", ["not json", "[]", '{"decision":"unknown"}'])
def test_json_decision_router_on_other_and_route_aliases(domain_profile, payload: str) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response=payload,
    )
    pipeline = definition(
        step(
            "router",
            action="json_decision_router",
            terminal=False,
            routes={"direct": "end", "retrieve": "end"},
            config=json_router_config(),
        ),
        step("end", action="finalize"),
        entry="router",
    )

    PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000015",
    ).execute(pipeline, context)

    assert context.last_route == "direct"


def test_json_decision_router_accepts_route_alias(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response='{"route":"retrieve","query":"orders"}',
    )
    pipeline = definition(
        step(
            "router",
            action="json_decision_router",
            terminal=False,
            routes={"direct": "end", "retrieve": "end"},
            config=json_router_config(),
        ),
        step("end", action="finalize"),
        entry="router",
    )

    PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000018",
    ).execute(pipeline, context)

    assert context.last_route == "retrieve"
    assert context.last_model_response == '{"query":"orders"}'


@pytest.mark.parametrize("payload", ["not json", "[]", '{"decision":"unknown"}'])
def test_json_decision_router_can_fail_without_on_other(domain_profile, payload: str) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response=payload,
    )
    config = json_router_config(on_other=None)
    pipeline = definition(
        step(
            "router",
            action="json_decision_router",
            terminal=False,
            routes={"direct": "end", "retrieve": "end"},
            config=config,
        ),
        step("end", action="finalize"),
        entry="router",
    )

    with pytest.raises(PipelineExecutionError):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000016",
        ).execute(pipeline, context)


def test_loop_guard_allows_then_denies(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )
    action = default_action_registry().get("loop_guard")
    guarded_step = step(
        "loop",
        action="loop_guard",
        terminal=False,
        routes={"again": "loop", "stop": "end"},
        config=loop_guard_config(max_loops=2),
    )

    assert action.execute(guarded_step, context) == "again"
    assert action.execute(guarded_step, context) == "again"
    assert action.execute(guarded_step, context) == "stop"
    assert context.loop_counters == {"loop": 3}


def test_repeat_query_guard_routes_new_and_repeated_queries(domain_profile) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response=" Class   PaymentService ",
    )
    action = default_action_registry().get("repeat_query_guard")
    guarded_step = step(
        "guard",
        action="repeat_query_guard",
        terminal=False,
        routes={"new": "retrieve", "repeat": "end"},
        config=repeat_query_guard_config(),
    )

    assert action.execute(guarded_step, context) == "new"
    context.last_model_response = "class paymentservice"
    assert action.execute(guarded_step, context) == "repeat"


@pytest.mark.parametrize(
    ("payload", "route"),
    [
        ('{"query":"orders"}', "new"),
        ('{"query":"  "}', "repeat"),
    ],
)
def test_repeat_query_guard_json_parser(domain_profile, payload: str, route: str) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response=payload,
    )
    action = default_action_registry().get("repeat_query_guard")
    guarded_step = step(
        "guard",
        action="repeat_query_guard",
        terminal=False,
        routes={"new": "retrieve", "repeat": "end"},
        config=repeat_query_guard_config(query_parser="json"),
    )

    assert action.execute(guarded_step, context) == route


@pytest.mark.parametrize("payload", ["bad json", "[]", '{"query":1}'])
def test_repeat_query_guard_json_parser_errors(domain_profile, payload: str) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        last_model_response=payload,
    )
    action = default_action_registry().get("repeat_query_guard")
    guarded_step = step(
        "guard",
        action="repeat_query_guard",
        terminal=False,
        routes={"new": "retrieve", "repeat": "end"},
        config=repeat_query_guard_config(query_parser="json"),
    )

    with pytest.raises(PipelineExecutionError, match="repeat_query_guard"):
        action.execute(guarded_step, context)


def test_multi_step_pipeline_routes_between_direct_and_retrieval_answer(
    domain_profile,
    search_hit,
) -> None:
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )
    pipeline = definition(
        step(
            "normalize_query",
            action="normalize_query",
            next_step="router_output",
            terminal=False,
        ),
        step(
            "router_output",
            action="set_variables",
            next_step="route_prefix",
            terminal=False,
            config={
                "rules": [
                    {
                        "set": "last_model_response",
                        "value": "[RETRIEVE:] Evidence question",
                    }
                ]
            },
        ),
        step(
            "route_prefix",
            action="prefix_router",
            terminal=False,
            routes={"direct": "set_direct", "retrieve": "retrieve"},
            config=prefix_router_config(),
        ),
        step(
            "set_direct",
            action="set_variables",
            next_step="finalize",
            terminal=False,
            config=set_variables_config(),
        ),
        step(
            "retrieve",
            action="retrieve",
            next_step="manage_context_budget",
            terminal=False,
            config=retrieve_config(),
        ),
        step(
            "manage_context_budget",
            action="manage_context_budget",
            next_step="set_retrieved_answer",
            terminal=False,
            config=context_budget_config(),
        ),
        step(
            "set_retrieved_answer",
            action="set_variables",
            next_step="finalize",
            terminal=False,
            config={"rules": [{"set": "answer", "value": "retrieved answer"}]},
        ),
        step("finalize", action="finalize"),
        entry="normalize_query",
        maximum=9,
    )

    result = PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000017",
    ).execute(pipeline, context)

    assert result.pipeline_trace == (
        "normalize_query",
        "router_output",
        "route_prefix",
        "retrieve",
        "manage_context_budget",
        "set_retrieved_answer",
        "finalize",
    )
    assert context.last_route == "retrieve"
    assert result.citations[0].chunk_id == "chunk-1"


def test_repository_loads_three_distinct_supported_pipelines() -> None:
    repository = YamlPipelineRepository("pipelines")
    validator = PipelineValidator(default_action_registry())

    loaded = {name: repository.load(name) for name in ("direct", "graph", "strict")}
    for pipeline in loaded.values():
        validator.validate(pipeline)

    assert tuple(step.action for step in loaded["direct"].steps) == (
        "normalize_query",
        "retrieve",
        "manage_context_budget",
        "enforce_model_transmission_policy",
        "call_model",
        "finalize",
    )
    assert "expand_graph" in tuple(step.action for step in loaded["graph"].steps)
    assert "require_evidence" in tuple(step.action for step in loaded["strict"].steps)
    assert loaded["direct"].step("retrieve").config["search_mode"] == "bm25"
    assert loaded["graph"].step("retrieve").config["search_mode"] == "hybrid"
    assert loaded["strict"].step("retrieve").config["search_mode"] == "semantic"


def test_strict_pipeline_refuses_to_call_model_without_evidence(domain_profile) -> None:
    model = StubModel()
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=model,
        prompts=StubPrompts(),
        model_servers=model_servers(default=model, clearance_label="INTERNAL"),
    )
    pipeline = YamlPipelineRepository("pipelines").load("strict")

    with pytest.raises(PipelineExecutionError, match="requires retrieved evidence"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000006",
        ).execute(pipeline, context)

    assert model.calls == []


def test_strict_pipeline_calls_model_when_evidence_exists(domain_profile, search_hit) -> None:
    model = StubModel(answer="grounded")
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        graph=StubGraph(),
        model=model,
        prompts=StubPrompts(),
        model_servers=model_servers(default=model, clearance_label="INTERNAL"),
    )
    pipeline = YamlPipelineRepository("pipelines").load("strict")

    result = PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000007",
    ).execute(pipeline, context)

    assert result.answer.startswith("grounded")
    assert len(model.calls) == 1


def test_pipeline_engine_records_safe_known_and_unknown_failures(domain_profile) -> None:
    command = QueryCommand("request", "question", "general", "session", authorization_context())
    context = PipelineContext(
        command=command,
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )
    audit = StubAudit()

    class KnownFailure(Action):
        def execute(self, step, context) -> None:
            raise ProviderProtocolError("safe provider failure")

    engine = PipelineEngine(
        registry=ActionRegistry((KnownFailure(),)),
        audit=audit,
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000002",
    )
    with pytest.raises(ProviderProtocolError):
        engine.execute(definition(step("start")), context)
    assert audit.failed[0][1] == {
        "error_code": "provider_protocol_error",
        "error_message": "safe provider failure",
    }

    class UnknownFailure(Action):
        def execute(self, step, context) -> None:
            raise RuntimeError("secret must not be persisted")

    audit = StubAudit()
    engine = PipelineEngine(
        registry=ActionRegistry((UnknownFailure(),)),
        audit=audit,
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000003",
    )
    with pytest.raises(RuntimeError):
        engine.execute(definition(step("start")), context)
    assert audit.failed[0][1] == {
        "error_code": "internal_error",
        "error_message": "Unhandled pipeline execution failure",
    }


def test_pipeline_engine_enforces_runtime_step_limit(domain_profile) -> None:
    pipeline = replace(default_pipeline(), max_steps=1)
    audit = StubAudit()
    engine = PipelineEngine(
        registry=default_action_registry(),
        audit=audit,
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000004",
    )
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PipelineExecutionError, match="step limit"):
        engine.execute(pipeline, context)
    assert len(audit.steps) == 1


def test_pipeline_engine_executes_explicit_route(domain_profile) -> None:
    class Router(Action):
        action_id = "router"
        provides = frozenset()

        def execute(self, step, context) -> str:
            return "selected"

    registry = ActionRegistry((Router(), Action()))
    pipeline = definition(
        step(
            "start",
            action="router",
            routes={"selected": "end"},
            terminal=False,
        ),
        step("end"),
    )
    PipelineValidator(registry).validate(pipeline)
    audit = StubAudit()
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    result = PipelineEngine(
        registry=registry,
        audit=audit,
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000005",
    ).execute(pipeline, context)

    assert result.answer == "done"
    assert result.pipeline_trace == ("start", "end")
    assert audit.steps[0].next_step_id == "end"


@pytest.mark.parametrize(
    ("returned_route", "terminal", "message"),
    [
        ("missing", False, "unknown route"),
        (None, False, "requires an explicit route"),
        ("selected", True, "Terminal step"),
    ],
)
def test_pipeline_engine_rejects_invalid_route_decisions(
    domain_profile,
    returned_route,
    terminal: bool,
    message: str,
) -> None:
    class Router(Action):
        action_id = "router"

        def execute(self, step, context):
            return returned_route

    pipeline = definition(
        step(
            "start",
            action="router",
            routes={} if terminal else {"selected": "end"},
            terminal=terminal,
        ),
        *(() if terminal else (step("end"),)),
    )
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PipelineExecutionError, match=message):
        PipelineEngine(
            registry=ActionRegistry((Router(), Action())),
            audit=StubAudit(),
        ).execute(pipeline, context)


class PipelineConversationRepository:
    def __init__(self) -> None:
        self.conversations = {}
        self.turns = {}
        self.failures = []

    def create_conversation(self, conversation):
        self.conversations[conversation.conversation_id] = conversation
        self.turns[conversation.conversation_id] = []
        return conversation

    def list_conversations(self, *, tenant_id, subject_hash, limit):
        return tuple(self.conversations.values())[:limit]

    def get_conversation(self, *, conversation_id, tenant_id, subject_hash):
        conversation = self.conversations.get(conversation_id)
        if (
            conversation
            and conversation.tenant_id == tenant_id
            and conversation.subject_hash == subject_hash
        ):
            return conversation
        return None

    def rename_conversation(self, *, conversation_id, tenant_id, subject_hash, title):
        return None

    def delete_conversation(self, *, conversation_id, tenant_id, subject_hash):
        return False

    def start_turn(self, turn):
        existing = [
            item for item in self.turns[turn.conversation_id] if item.request_id == turn.request_id
        ]
        if existing:
            return existing[0]
        stored = replace(turn, sequence_number=len(self.turns[turn.conversation_id]) + 1)
        self.turns[turn.conversation_id].append(stored)
        return stored

    def finalize_turn(
        self, *, conversation_id, turn_id, tenant_id, subject_hash, answer_text, metadata
    ):
        for index, turn in enumerate(self.turns[conversation_id]):
            if turn.turn_id == turn_id:
                stored = replace(
                    turn,
                    status=ConversationTurnStatus.FINALIZED,
                    answer_text=answer_text,
                    finalized_at="done",
                    metadata=metadata,
                )
                self.turns[conversation_id][index] = stored
                return stored
        return None

    def fail_turn(self, *, conversation_id, turn_id, tenant_id, subject_hash, error_code, metadata):
        self.failures.append(error_code)
        for index, turn in enumerate(self.turns[conversation_id]):
            if turn.turn_id == turn_id:
                stored = replace(
                    turn,
                    status=ConversationTurnStatus.FAILED,
                    finalized_at="failed",
                    error_code=error_code,
                    metadata=metadata,
                )
                self.turns[conversation_id][index] = stored
                return stored
        return None

    def list_turns(self, *, conversation_id, tenant_id, subject_hash, limit):
        return tuple(self.turns[conversation_id][:limit])

    def recent_finalized_turns(self, *, conversation_id, tenant_id, subject_hash, limit):
        finalized = [
            turn
            for turn in self.turns[conversation_id]
            if turn.status == ConversationTurnStatus.FINALIZED
        ]
        return tuple(finalized[-limit:])


def conversation_pipeline() -> PipelineDefinition:
    return definition(
        step("normalize", action="normalize_query", next_step="resolve", terminal=False),
        step(
            "resolve",
            action="resolve_conversation",
            config={
                "conversation_id_source": "request",
                "create_if_missing": True,
                "title_source": "query",
            },
            next_step="start_turn",
            terminal=False,
        ),
        step(
            "start_turn", action="start_conversation_turn", next_step="load_history", terminal=False
        ),
        step(
            "load_history",
            action="load_conversation_history",
            config={"limit": 5, "max_characters": 200, "include_failed": False, "format": "dialog"},
            next_step="retrieve",
            terminal=False,
        ),
        step(
            "retrieve",
            action="retrieve",
            config=retrieve_config(),
            next_step="budget",
            terminal=False,
        ),
        step(
            "budget",
            action="manage_context_budget",
            config=context_budget_config(),
            next_step="policy",
            terminal=False,
        ),
        step(
            "policy",
            action="enforce_model_transmission_policy",
            config=model_transmission_config(),
            next_step="call_model",
            terminal=False,
        ),
        step(
            "call_model",
            action="call_model",
            config=call_model_config(
                use_history=True,
                history_source="conversation_history",
                user_parts={
                    "history": {"source": "conversation_history", "template": "H:{}\n"},
                    "question": {"source": "normalized_query", "template": "Q:{}\n"},
                    "evidence": {"source": "evidence", "template": "E:{}"},
                },
            ),
            next_step="finalize",
            terminal=False,
        ),
        step("finalize", action="finalize", next_step="save_turn", terminal=False),
        step("save_turn", action="finalize_conversation_turn"),
        entry="normalize",
        maximum=12,
    )


def test_conversation_pipeline_creates_continues_and_finalizes_turns(domain_profile, search_hit):
    repository = PipelineConversationRepository()
    history = ConversationHistoryService(
        repository,
        id_factory=iter(("conversation-1", "turn-1", "turn-2")).__next__,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    model = StubModel(answer="first answer")
    first_context = PipelineContext(
        command=QueryCommand(
            "request-1", "first question", "general", "session", authorization_context()
        ),
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        graph=StubGraph(),
        model=model,
        prompts=StubPrompts(),
        conversation_history=history,
        model_servers=model_servers(default=model, clearance_label="INTERNAL"),
    )
    engine = PipelineEngine(
        registry=default_action_registry(),
        audit=StubAudit(),
        run_id_factory=lambda: "00000000-0000-0000-0000-000000000101",
    )

    first = engine.execute(conversation_pipeline(), first_context)
    model.answer = "second answer"
    second_context = replace(
        first_context,
        command=QueryCommand(
            "request-2",
            "second question",
            "general",
            "session",
            authorization_context(),
            conversation_id=first.conversation_id,
        ),
    )
    second = engine.execute(conversation_pipeline(), second_context)

    assert first.conversation_id == "conversation-1"
    assert first.turn_id == "turn-1"
    assert second.turn_id == "turn-2"
    assert repository.turns["conversation-1"][0].answer_text.endswith("Verify the result.")
    assert "H:User: first question\nAssistant: first answer" in model.calls[1]["user_prompt"]


def test_conversation_pipeline_marks_started_turn_failed_on_error(domain_profile):
    repository = PipelineConversationRepository()
    history = ConversationHistoryService(
        repository,
        id_factory=iter(("conversation-1", "turn-1")).__next__,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    context = PipelineContext(
        command=QueryCommand(
            "request-1", "question", "general", "session", authorization_context()
        ),
        domain=domain_profile,
        retrieval=StubRetrieval(()),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        conversation_history=history,
        model_servers=model_servers(default=StubModel(), clearance_label="INTERNAL"),
    )
    pipeline = definition(
        step("normalize", action="normalize_query", next_step="resolve", terminal=False),
        step(
            "resolve",
            action="resolve_conversation",
            config={
                "conversation_id_source": "request",
                "create_if_missing": True,
                "title_source": "query",
            },
            next_step="start_turn",
            terminal=False,
        ),
        step("start_turn", action="start_conversation_turn", next_step="evidence", terminal=False),
        step("evidence", action="require_evidence"),
        entry="normalize",
        maximum=6,
    )

    with pytest.raises(PipelineExecutionError, match="evidence"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=StubAudit(),
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000102",
        ).execute(pipeline, context)

    assert repository.failures == ["pipeline_execution_error"]


@pytest.mark.parametrize(
    ("action", "config", "message"),
    [
        ("resolve_conversation", {}, "conversation_id_source"),
        (
            "resolve_conversation",
            {"conversation_id_source": "body", "create_if_missing": True, "title_source": "query"},
            "conversation_id_source",
        ),
        (
            "resolve_conversation",
            {
                "conversation_id_source": "request",
                "create_if_missing": "yes",
                "title_source": "query",
            },
            "create_if_missing",
        ),
        (
            "resolve_conversation",
            {
                "conversation_id_source": "request",
                "create_if_missing": True,
                "title_source": "body",
            },
            "title_source",
        ),
        ("start_conversation_turn", {"extra": True}, "unsupported"),
        ("load_conversation_history", {}, "format"),
        (
            "load_conversation_history",
            {"limit": 1, "max_characters": 10, "include_failed": True, "format": "dialog"},
            "include_failed",
        ),
        (
            "load_conversation_history",
            {"limit": 1, "max_characters": 10, "include_failed": False, "format": "raw"},
            "format",
        ),
        ("finalize_conversation_turn", {"extra": True}, "unsupported"),
        ("fail_conversation_turn", {"extra": True}, "unsupported"),
    ],
)
def test_conversation_actions_reject_invalid_yaml_contracts(action, config, message):
    pipeline = definition(step("start", action=action, config=config))

    with pytest.raises(PipelineDefinitionError, match=message):
        PipelineValidator(default_action_registry()).validate(pipeline)


def test_conversation_actions_runtime_guards(domain_profile):
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )
    registry = default_action_registry()

    with pytest.raises(PipelineExecutionError, match="service"):
        registry.get("resolve_conversation").execute(
            step(
                "resolve",
                action="resolve_conversation",
                config={
                    "conversation_id_source": "request",
                    "create_if_missing": True,
                    "title_source": "query",
                },
            ),
            context,
        )
    with pytest.raises(PipelineExecutionError, match="starting"):
        registry.get("start_conversation_turn").execute(
            step("start", action="start_conversation_turn"),
            replace(context, conversation_history=object()),
        )
    with pytest.raises(PipelineExecutionError, match="loading"):
        registry.get("load_conversation_history").execute(
            step(
                "load",
                action="load_conversation_history",
                config={
                    "limit": 1,
                    "max_characters": 10,
                    "include_failed": False,
                    "format": "dialog",
                },
            ),
            replace(context, conversation_history=object()),
        )
    with pytest.raises(PipelineExecutionError, match="finalization"):
        registry.get("finalize_conversation_turn").execute(
            step("finalize", action="finalize_conversation_turn"),
            replace(context, conversation_history=object()),
        )
    with pytest.raises(PipelineExecutionError, match="requires answer"):
        registry.get("finalize_conversation_turn").execute(
            step("finalize", action="finalize_conversation_turn"),
            replace(
                context,
                conversation_history=object(),
                conversation_id="conversation",
                conversation_turn_id="turn",
            ),
        )
    with pytest.raises(PipelineExecutionError, match="marking failure"):
        registry.get("fail_conversation_turn").execute(
            step("fail", action="fail_conversation_turn"),
            replace(context, conversation_history=object()),
        )


def test_resolve_conversation_requires_existing_id_when_creation_is_disabled(domain_profile):
    repository = PipelineConversationRepository()
    history = ConversationHistoryService(
        repository,
        id_factory=iter(("conversation-1",)).__next__,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        conversation_history=history,
    )

    with pytest.raises(PipelineExecutionError, match="Conversation id"):
        default_action_registry().get("resolve_conversation").execute(
            step(
                "resolve",
                action="resolve_conversation",
                config={
                    "conversation_id_source": "request",
                    "create_if_missing": False,
                    "title_source": "query",
                },
            ),
            context,
        )


def test_resolve_conversation_accepts_existing_conversation(domain_profile):
    repository = PipelineConversationRepository()
    history = ConversationHistoryService(
        repository,
        id_factory=iter(("conversation-1",)).__next__,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    authorization = authorization_context()
    conversation = history.create(
        authorization, session_id="session", domain="general", title="Title"
    )
    context = PipelineContext(
        command=QueryCommand(
            "request",
            "question",
            "general",
            "session",
            authorization,
            conversation_id=conversation.conversation_id,
        ),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        conversation_history=history,
    )

    default_action_registry().get("resolve_conversation").execute(
        step(
            "resolve",
            action="resolve_conversation",
            config={
                "conversation_id_source": "request",
                "create_if_missing": False,
                "title_source": "query",
            },
        ),
        context,
    )

    assert context.conversation_id == "conversation-1"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (call_model_config(use_history="yes"), "use_history"),
        (call_model_config(use_history=True), "history_source"),
        (call_model_config(history_source="conversation_history"), "requires use_history"),
        (
            call_model_config(
                user_parts={"history": {"source": "conversation_history", "template": "{}"}}
            ),
            "requires use_history",
        ),
    ],
)
def test_call_model_rejects_invalid_history_contract(config, message):
    pipeline = definition(step("start", action="call_model", config=config))

    with pytest.raises(PipelineDefinitionError, match=message):
        PipelineValidator(default_action_registry()).validate(pipeline)


def test_conversation_private_helpers_cover_budget_and_invalid_sources(domain_profile):
    turn_without_answer = pipeline_module.ConversationTurn(
        "turn-1",
        "conversation",
        "request-1",
        1,
        "Q1",
        ConversationTurnStatus.STARTED,
        "created",
    )
    short_turn = pipeline_module.ConversationTurn(
        "turn-2",
        "conversation",
        "request-2",
        2,
        "Q2",
        ConversationTurnStatus.FINALIZED,
        "created",
        answer_text="A2",
        finalized_at="done",
    )
    long_turn = pipeline_module.ConversationTurn(
        "turn-3",
        "conversation",
        "request-3",
        3,
        "Q3",
        ConversationTurnStatus.FINALIZED,
        "created",
        answer_text="A" * 100,
        finalized_at="done",
    )
    context = PipelineContext(
        command=SimpleNamespace(query="   "),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    assert conversation_module._conversation_title("query", context) == "Untitled conversation"
    with pytest.raises(PipelineExecutionError, match="title source"):
        conversation_module._conversation_title("body", context)
    assert (
        conversation_module._format_conversation_history(
            (turn_without_answer, long_turn, short_turn),
            max_characters=40,
        )
        == "User: Q2\nAssistant: A2"
    )
    assert (
        conversation_module._format_conversation_history(
            (short_turn, short_turn, short_turn),
            max_characters=50,
        )
        == "User: Q2\nAssistant: A2\n\nUser: Q2\nAssistant: A2"
    )


class FailingConversationRepository(PipelineConversationRepository):
    def fail_turn(self, **kwargs):
        raise RuntimeError("down")


def test_engine_surfaces_history_failure_when_marking_turn_failed(domain_profile):
    repository = FailingConversationRepository()
    history = ConversationHistoryService(
        repository,
        id_factory=iter(("conversation-1", "turn-1")).__next__,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    audit = StubAudit()
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(()),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        conversation_history=history,
    )
    pipeline = definition(
        step("normalize", action="normalize_query", next_step="resolve", terminal=False),
        step(
            "resolve",
            action="resolve_conversation",
            config={
                "conversation_id_source": "request",
                "create_if_missing": True,
                "title_source": "query",
            },
            next_step="start_turn",
            terminal=False,
        ),
        step("start_turn", action="start_conversation_turn", next_step="evidence", terminal=False),
        step("evidence", action="require_evidence"),
        entry="normalize",
        maximum=6,
    )

    with pytest.raises(RuntimeError, match="down"):
        PipelineEngine(
            registry=default_action_registry(),
            audit=audit,
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000103",
        ).execute(pipeline, context)

    assert audit.failed[0][1]["error_code"] == "internal_error"


def test_fail_conversation_turn_action_marks_turn_failed(domain_profile):
    repository = PipelineConversationRepository()
    history = ConversationHistoryService(
        repository,
        id_factory=iter(("conversation-1", "turn-1")).__next__,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    authorization = authorization_context()
    conversation = history.create(
        authorization, session_id="session", domain="general", title="Title"
    )
    turn = history.start_turn(
        authorization,
        conversation_id=conversation.conversation_id,
        request_id="request-1",
        question_text="Question?",
    )
    context = PipelineContext(
        command=QueryCommand("request-1", "Question?", "general", "session", authorization),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=StubModel(),
        prompts=StubPrompts(),
        conversation_history=history,
        conversation_id=conversation.conversation_id,
        conversation_turn_id=turn.turn_id,
    )

    result = (
        default_action_registry()
        .get("fail_conversation_turn")
        .execute(
            step("fail", action="fail_conversation_turn"),
            context,
        )
    )

    assert result is None
    assert repository.failures == ["pipeline_marked_failed"]


def test_load_conversation_history_rejects_non_boolean_include_failed():
    pipeline = definition(
        step(
            "start",
            action="load_conversation_history",
            config={"limit": 1, "max_characters": 10, "include_failed": "no", "format": "dialog"},
        )
    )

    with pytest.raises(PipelineDefinitionError, match="include_failed"):
        PipelineValidator(default_action_registry()).validate(pipeline)
