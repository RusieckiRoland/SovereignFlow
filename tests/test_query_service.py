from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import StubModel, StubPrompts, StubRetrieval

from sovereignflow.application import RagQueryService
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
    service = RagQueryService(
        domain=domain_profile,
        retrieval=retrieval,
        model=model,
        prompts=prompts,
    )

    assert service.domain_name == "general"
    result = service.execute(command())

    assert result.answer == "Grounded answer.\n\n---\n\nVerify the result."
    assert result.pipeline_trace == (
        "normalize_query",
        "retrieve",
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


def test_query_service_uses_explicit_no_evidence_message(domain_profile) -> None:
    model = StubModel(answer="Insufficient evidence.")
    service = RagQueryService(
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
    service = RagQueryService(
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
        RagQueryService(
            domain=domain_profile,
            retrieval=StubRetrieval(),
            model=StubModel(scope="external"),
            prompts=StubPrompts(),
        )


def test_query_domain_must_match_service(domain_profile) -> None:
    service = RagQueryService(
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
    service = RagQueryService(
        domain=domain_profile,
        retrieval=StubRetrieval((SearchHit(chunk, 1.0, "hybrid"),)),
        model=StubModel(),
        prompts=StubPrompts(),
    )

    with pytest.raises(PolicyViolationError, match=message):
        service.execute(command())
