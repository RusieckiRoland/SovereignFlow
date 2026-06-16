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
    build_query_service,
    default_pipeline,
)

from sovereignflow.application import PipelineEngine, default_action_registry
from sovereignflow.domain import (
    DocumentChunk,
    DocumentSecurity,
    PolicyViolationError,
    QueryCommand,
    SearchHit,
    SubjectSecurity,
)


def command(domain: str = "general") -> QueryCommand:
    return QueryCommand(
        request_id="request-1",
        query="  evidence   question ",
        domain=domain,
        session_id="session-1",
        authorization=authorization_context(),
        filters={"country": "PL", "status": "inactive"},
    )


def test_query_service_executes_complete_vertical_flow(
    domain_profile,
    search_hit,
) -> None:
    retrieval = StubRetrieval((search_hit,))
    model = StubModel(answer="Grounded answer.")
    prompts = StubPrompts()
    audit = StubAudit()
    service = build_query_service(
        domain=domain_profile,
        retrieval=retrieval,
        model=model,
        prompts=prompts,
        audit=audit,
    )

    assert service.domain_name == "general"
    result = service.execute(command())

    assert result.answer == "Grounded answer.\n\n---\n\nVerify the result."
    assert result.pipeline_trace == (
        "normalize_query",
        "retrieve",
        "expand_graph",
        "manage_context_budget",
        "enforce_model_transmission_policy",
        "call_model",
        "finalize",
    )
    assert result.citations[0].score_type == "hybrid"
    assert retrieval.requests[0].query == "evidence question"
    assert retrieval.requests[0].mode.value == "hybrid"
    assert retrieval.requests[0].top_k == 3
    assert dict(retrieval.requests[0].filters) == {
        "status": "active",
        "country": "PL",
    }
    assert "Evidence text." in model.calls[0]["user_prompt"]
    assert model.calls[0]["generation_parameters"] == {}
    assert prompts.names == ["answer"]
    assert audit.started[0].pipeline_checksum == "a" * 64
    assert [item.step_id for item in audit.steps] == list(result.pipeline_trace)
    assert audit.succeeded[0][1]["citation_count"] == 1


def test_query_service_requires_configured_model_server(domain_profile) -> None:
    from sovereignflow.application import RagQueryService

    with pytest.raises(PolicyViolationError, match="At least one model server"):
        RagQueryService(
            domain=domain_profile,
            retrieval=StubRetrieval(),
            graph=StubGraph(),
            model_servers={},
            prompts=StubPrompts(),
            pipeline=default_pipeline(),
            engine=PipelineEngine(
                registry=default_action_registry(),
                audit=StubAudit(),
            ),
        )


def test_query_service_expands_graph_with_explicit_policy(
    domain_profile,
    search_hit,
) -> None:
    related = replace(
        search_hit,
        chunk=replace(
            search_hit.chunk,
            source_id="source-2",
            chunk_id="chunk-2",
            text="Related evidence.",
        ),
        score=0.25,
        score_type="graph",
    )
    graph = StubGraph((related,))
    model = StubModel()
    result = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        graph=graph,
        model=model,
        prompts=StubPrompts(),
    ).execute(command())

    assert len(result.citations) == 2
    assert graph.requests[0].max_depth == 2
    assert graph.requests[0].relationship_types == ("references",)
    assert "Related evidence." in model.calls[0]["user_prompt"]


def test_query_service_skips_graph_when_explicitly_disabled(
    domain_profile,
    search_hit,
) -> None:
    graph = StubGraph()
    pipeline = pipeline_with_config(
        "expand_graph",
        {
            "enabled": False,
            "max_depth": 2,
            "max_nodes": 10,
            "direction": "both",
            "relationship_types": ["references"],
        },
    )

    build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        graph=graph,
        model=StubModel(),
        prompts=StubPrompts(),
        pipeline=pipeline,
    ).execute(command())

    assert graph.requests == []


def test_query_service_rechecks_graph_security_boundary(
    domain_profile,
    search_hit,
) -> None:
    forbidden = replace(
        search_hit,
        chunk=replace(
            search_hit.chunk,
            source_id="source-2",
            chunk_id="chunk-2",
            acl_labels=("private",),
        ),
        score_type="graph",
    )
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        graph=StubGraph((forbidden,)),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PolicyViolationError, match="ACL"):
        service.execute(command())


def test_query_service_uses_explicit_no_evidence_message(domain_profile) -> None:
    model = StubModel(answer="Insufficient evidence.")
    service = build_query_service(
        domain=replace(domain_profile, disclaimer=""),
        retrieval=StubRetrieval(),
        model=model,
        prompts=StubPrompts(),
    )

    result = service.execute(command())

    assert result.answer == "Insufficient evidence."
    assert result.citations == ()
    assert "No relevant evidence was retrieved." in model.calls[0]["user_prompt"]


def test_context_is_truncated_at_configured_limit(domain_profile, search_hit) -> None:
    second = replace(
        search_hit,
        chunk=replace(search_hit.chunk, chunk_id="chunk-2", source_id="source-2"),
    )
    model = StubModel()
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit, second)),
        model=model,
        prompts=StubPrompts(),
        pipeline=pipeline_with_config(
            "manage_context_budget",
            {
                "source": "hits",
                "target": "evidence",
                "max_context_characters": 20,
            },
        ),
    )

    result = service.execute(command())

    evidence = model.calls[0]["user_prompt"].split("EVIDENCE\n", 1)[1].strip()
    assert len(evidence) == 20
    assert len(result.citations) == 1


def pipeline_with_config(step_id: str, config: dict):
    pipeline = default_pipeline()
    return replace(
        pipeline,
        steps=tuple(
            replace(step, config=config) if step.step_id == step_id else step
            for step in pipeline.steps
        ),
    )


def test_external_model_requires_domain_permission_at_policy_step(domain_profile) -> None:
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval(),
        model=StubModel(scope="external"),
        prompts=StubPrompts(),
    )

    with pytest.raises(PolicyViolationError, match="external_model_not_allowed_for_subject"):
        service.execute(command())


def test_query_domain_must_match_service(domain_profile) -> None:
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PolicyViolationError, match="does not match"):
        service.execute(command("other"))


def test_query_security_is_derived_from_authenticated_user(
    domain_profile,
    search_hit,
) -> None:
    retrieval = StubRetrieval((search_hit,))
    restricted = command()
    restricted = replace(
        restricted,
        authorization=authorization_context(
            acl_labels=("public", "unknown"),
            security=SubjectSecurity(clearance_label="PUBLIC"),
        ),
    )
    forbidden_hit = replace(
        search_hit,
        chunk=replace(search_hit.chunk, security=DocumentSecurity(clearance_label="INTERNAL")),
    )
    retrieval.hits = (forbidden_hit,)
    service = build_query_service(
        domain=domain_profile,
        retrieval=retrieval,
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PolicyViolationError, match="security"):
        service.execute(restricted)
    assert retrieval.requests[0].allowed_acl_labels == ("public",)
    assert retrieval.requests[0].subject_security.clearance_label == "PUBLIC"


def test_query_rejects_foreign_tenant_forbidden_filter_and_diagnostics(
    domain_profile,
) -> None:
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval(),
        model=StubModel(),
        prompts=StubPrompts(),
    )
    with pytest.raises(PolicyViolationError, match="tenant"):
        service.execute(
            replace(
                command(),
                authorization=authorization_context(tenant_id="tenant-b"),
            )
        )
    with pytest.raises(PolicyViolationError, match="filters"):
        service.execute(replace(command(), filters={"secret": "value"}))
    with pytest.raises(PolicyViolationError, match="diagnostics"):
        service.execute(
            replace(
                command(),
                diagnostics_requested=True,
                authorization=authorization_context(diagnostic_access=False),
            )
        )


def test_external_model_requires_user_permission(domain_profile) -> None:
    domain = replace(domain_profile, allow_external_model=True)
    service = build_query_service(
        domain=domain,
        retrieval=StubRetrieval(),
        model=StubModel(scope="external"),
        prompts=StubPrompts(),
    )
    with pytest.raises(PolicyViolationError, match="external_model_not_allowed_for_subject"):
        service.execute(command())

    result = service.execute(
        replace(
            command(),
            authorization=authorization_context(allow_external_model=True),
        )
    )
    assert result.answer


def test_query_passes_domain_security_model_to_retrieval(
    domain_profile,
) -> None:
    retrieval = StubRetrieval()
    service = build_query_service(
        domain=domain_profile,
        retrieval=retrieval,
        model=StubModel(),
        prompts=StubPrompts(),
    )
    service.execute(command())
    assert retrieval.requests[-1].security_model == domain_profile.security_model


@pytest.mark.parametrize(
    ("chunk", "message"),
    [
        (
            DocumentChunk("c", "other", "tenant-a", "s", "text"),
            "domain or tenant",
        ),
        (
            DocumentChunk("c", "general", "tenant-b", "s", "text"),
            "domain or tenant",
        ),
        (
            DocumentChunk(
                "c",
                "general",
                "tenant-a",
                "s",
                "text",
                acl_labels=("private",),
            ),
            "ACL",
        ),
        (
            DocumentChunk(
                "c",
                "general",
                "tenant-a",
                "s",
                "text",
                security=DocumentSecurity(clearance_label="INTERNAL"),
            ),
            "security",
        ),
    ],
)
def test_query_service_rechecks_retrieval_security_boundary(
    domain_profile,
    chunk,
    message: str,
) -> None:
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval((SearchHit(chunk, 1.0, "hybrid"),)),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PolicyViolationError, match=message):
        service.execute(command())
