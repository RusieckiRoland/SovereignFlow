from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import (
    StubAudit,
    StubGraph,
    StubModel,
    StubPrompts,
    StubRetrieval,
    build_query_service,
)

from sovereignflow.domain import (
    DocumentChunk,
    PolicyViolationError,
    QueryCommand,
    SearchHit,
)


def command(domain: str = "general") -> QueryCommand:
    return QueryCommand(
        request_id="request-1",
        query="  evidence   question ",
        domain=domain,
        session_id="session-1",
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
        "build_context",
        "call_model",
        "finalize",
    )
    assert result.citations[0].score_type == "hybrid"
    assert retrieval.requests[0].query == "evidence question"
    assert dict(retrieval.requests[0].filters) == {
        "status": "active",
        "country": "PL",
    }
    assert "Evidence text." in model.calls[0]["user_prompt"]
    assert prompts.names == ["answer"]
    assert audit.started[0].pipeline_checksum == "a" * 64
    assert [item.step_id for item in audit.steps] == list(result.pipeline_trace)
    assert audit.succeeded[0][1]["citation_count"] == 1


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
    domain = replace(
        domain_profile,
        graph=replace(domain_profile.graph, enabled=False),
    )

    build_query_service(
        domain=domain,
        retrieval=StubRetrieval((search_hit,)),
        graph=graph,
        model=StubModel(),
        prompts=StubPrompts(),
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
    domain = replace(
        domain_profile,
        retrieval=replace(domain_profile.retrieval, max_context_characters=20),
    )
    second = replace(
        search_hit,
        chunk=replace(search_hit.chunk, chunk_id="chunk-2", source_id="source-2"),
    )
    model = StubModel()
    service = build_query_service(
        domain=domain,
        retrieval=StubRetrieval((search_hit, second)),
        model=model,
        prompts=StubPrompts(),
    )

    result = service.execute(command())

    evidence = (
        model.calls[0]["user_prompt"]
        .split("EVIDENCE\n", 1)[1]
        .split(
            "\n\nAnswer",
            1,
        )[0]
    )
    assert len(evidence) == 20
    assert len(result.citations) == 1


def test_external_model_requires_domain_permission(domain_profile) -> None:
    with pytest.raises(PolicyViolationError, match="does not allow external"):
        build_query_service(
            domain=domain_profile,
            retrieval=StubRetrieval(),
            model=StubModel(scope="external"),
            prompts=StubPrompts(),
        )


def test_query_domain_must_match_service(domain_profile) -> None:
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval(),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PolicyViolationError, match="does not match"):
        service.execute(command("other"))


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
                classification_level=2,
            ),
            "classification",
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
