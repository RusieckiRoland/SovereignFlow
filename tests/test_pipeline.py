from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import (
    StubAudit,
    StubGraph,
    StubModel,
    StubPrompts,
    StubRetrieval,
    authorization_context,
    default_pipeline,
)

from sovereignflow.application import (
    ActionRegistry,
    PipelineContext,
    PipelineEngine,
    PipelineValidator,
    default_action_registry,
)
from sovereignflow.domain import (
    PipelineDefinition,
    PipelineDefinitionError,
    PipelineExecutionError,
    PipelineStepDefinition,
    ProviderProtocolError,
    QueryCommand,
)
from sovereignflow.infrastructure import YamlPipelineRepository


class Action:
    action_id = "action"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset({"result"})

    def execute(self, context) -> None:
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
) -> PipelineStepDefinition:
    return PipelineStepDefinition(
        step_id=step_id,
        action=action,
        action_version=version,
        next_step_id=next_step,
        routes=routes or {},
        terminal=terminal,
    )


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
            definition(step("start", action="call_model")),
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


def test_repository_loads_three_distinct_supported_pipelines() -> None:
    repository = YamlPipelineRepository("pipelines")
    validator = PipelineValidator(default_action_registry())

    loaded = {name: repository.load(name) for name in ("direct", "graph", "strict")}
    for pipeline in loaded.values():
        validator.validate(pipeline)

    assert tuple(step.action for step in loaded["direct"].steps) == (
        "normalize_query",
        "retrieve",
        "build_context",
        "call_model",
        "finalize",
    )
    assert "expand_graph" in tuple(step.action for step in loaded["graph"].steps)
    assert "require_evidence" in tuple(step.action for step in loaded["strict"].steps)


def test_strict_pipeline_refuses_to_call_model_without_evidence(domain_profile) -> None:
    model = StubModel()
    context = PipelineContext(
        command=QueryCommand("request", "question", "general", "session", authorization_context()),
        domain=domain_profile,
        retrieval=StubRetrieval(),
        graph=StubGraph(),
        model=model,
        prompts=StubPrompts(),
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
        def execute(self, context) -> None:
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
        def execute(self, context) -> None:
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

        def execute(self, context) -> str:
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

        def execute(self, context):
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
