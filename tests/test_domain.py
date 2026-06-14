from __future__ import annotations

from types import MappingProxyType

import pytest

from sovereignflow.domain import (
    Citation,
    DocumentChunk,
    DomainProfile,
    GraphDirection,
    GraphNodeRef,
    GraphRelationship,
    GraphTraversalProfile,
    GraphTraversalRequest,
    ModelGeneration,
    PipelineDefinition,
    PipelineRun,
    PipelineRunStatus,
    PipelineStepAudit,
    PipelineStepDefinition,
    QueryCommand,
    RetrievalProfile,
    SearchHit,
    SearchMode,
    SearchRequest,
    ValidationError,
)


def test_domain_models_normalize_and_freeze_values() -> None:
    chunk = DocumentChunk(
        chunk_id=" chunk ",
        domain=" domain ",
        tenant_id=" tenant ",
        source_id=" source ",
        text=" text ",
        metadata={"key": "value"},
        acl_labels=("beta", "alpha", "alpha"),
    )
    request = QueryCommand(
        request_id=" request ",
        query=" query ",
        domain=" domain ",
        session_id=" session ",
        filters={"key": "value"},
    )

    assert chunk.chunk_id == "chunk"
    assert chunk.acl_labels == ("alpha", "beta")
    assert isinstance(chunk.metadata, MappingProxyType)
    assert isinstance(request.filters, MappingProxyType)

    generation = ModelGeneration(" answer ", 10, 5, 0.25)
    assert generation.text == "answer"
    assert generation.total_tokens == 15


def test_graph_models_normalize_and_freeze_values(search_hit) -> None:
    node = GraphNodeRef(" source ", " chunk ")
    relationship = GraphRelationship(node, GraphNodeRef("target", "chunk"), " reference ")
    profile = GraphTraversalProfile(
        True,
        2,
        10,
        GraphDirection.BOTH,
        ("contains", "contains", "references"),
    )
    traversal = GraphTraversalRequest(
        seeds=(search_hit,),
        domain=" general ",
        tenant_id=" tenant-a ",
        max_depth=2,
        max_nodes=10,
        direction=GraphDirection.OUTGOING,
        relationship_types=("references",),
        allowed_acl_labels=("public", "public"),
        max_classification_level=1,
    )

    assert node == GraphNodeRef("source", "chunk")
    assert relationship.relationship_type == "reference"
    assert isinstance(relationship.metadata, MappingProxyType)
    assert profile.relationship_types == ("contains", "references")
    assert traversal.allowed_acl_labels == ("public",)


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: GraphNodeRef("", "chunk"), "source_id"),
        (
            lambda: GraphRelationship(
                GraphNodeRef("source", "chunk"),
                GraphNodeRef("target", "chunk"),
                "",
            ),
            "relationship_type",
        ),
        (
            lambda: GraphTraversalProfile(True, 0, 1, GraphDirection.BOTH),
            "max_depth",
        ),
        (
            lambda: GraphTraversalProfile(True, 1, 0, GraphDirection.BOTH),
            "max_nodes",
        ),
        (
            lambda: GraphTraversalProfile(True, 1, 1, GraphDirection.BOTH, ("",)),
            "relationship_types",
        ),
        (
            lambda: GraphTraversalRequest(
                (),
                "domain",
                "tenant",
                1,
                1,
                GraphDirection.BOTH,
                (),
                (),
                None,
            ),
            "seeds",
        ),
        (
            lambda: GraphTraversalRequest(
                (SearchHit(DocumentChunk("c", "d", "t", "s", "x"), 1, "x"),),
                "domain",
                "tenant",
                0,
                1,
                GraphDirection.BOTH,
                (),
                (),
                None,
            ),
            "max_depth",
        ),
        (
            lambda: GraphTraversalRequest(
                (SearchHit(DocumentChunk("c", "d", "t", "s", "x"), 1, "x"),),
                "domain",
                "tenant",
                1,
                0,
                GraphDirection.BOTH,
                (),
                (),
                None,
            ),
            "max_nodes",
        ),
        (
            lambda: GraphTraversalRequest(
                (SearchHit(DocumentChunk("c", "d", "t", "s", "x"), 1, "x"),),
                "domain",
                "tenant",
                1,
                1,
                GraphDirection.BOTH,
                (),
                (),
                -1,
            ),
            "classification",
        ),
        (
            lambda: GraphTraversalRequest(
                (SearchHit(DocumentChunk("c", "d", "t", "s", "x"), 1, "x"),),
                "domain",
                "tenant",
                1,
                1,
                GraphDirection.BOTH,
                ("",),
                (),
                None,
            ),
            "relationship_types",
        ),
        (
            lambda: GraphTraversalRequest(
                (SearchHit(DocumentChunk("c", "d", "t", "s", "x"), 1, "x"),),
                "domain",
                "tenant",
                1,
                1,
                GraphDirection.BOTH,
                (),
                ("",),
                None,
            ),
            "allowed_acl_labels",
        ),
    ],
)
def test_graph_models_reject_invalid_values(factory, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        factory()


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: DocumentChunk("", "d", "t", "s", "x"),
            "DocumentChunk.chunk_id",
        ),
        (
            lambda: DocumentChunk("c", "d", "t", "s", "x", classification_level=-1),
            "classification_level",
        ),
        (
            lambda: DocumentChunk("c", "d", "t", "s", "x", acl_labels=("",)),
            "acl_labels",
        ),
        (lambda: ModelGeneration("answer", -1, 0, 0), "prompt_tokens"),
        (lambda: ModelGeneration("answer", 0, -1, 0), "completion_tokens"),
        (lambda: ModelGeneration("answer", 0, 0, -1), "estimated_cost"),
        (
            lambda: RetrievalProfile(SearchMode.HYBRID, 0, 1),
            "top_k",
        ),
        (
            lambda: RetrievalProfile(SearchMode.HYBRID, 1, 0),
            "max_context",
        ),
        (
            lambda: DomainProfile(
                "",
                "",
                "c",
                "t",
                "p",
                False,
                RetrievalProfile(SearchMode.HYBRID, 1, 1),
                GraphTraversalProfile(False, 1, 1, GraphDirection.BOTH),
            ),
            "DomainProfile.name",
        ),
        (
            lambda: DomainProfile(
                "d",
                "",
                "c",
                "t",
                "p",
                False,
                RetrievalProfile(SearchMode.HYBRID, 1, 1),
                GraphTraversalProfile(False, 1, 1, GraphDirection.BOTH),
                max_classification_level=-1,
            ),
            "max_classification",
        ),
        (
            lambda: DomainProfile(
                "d",
                "",
                "c",
                "t",
                "p",
                False,
                RetrievalProfile(SearchMode.HYBRID, 1, 1),
                GraphTraversalProfile(False, 1, 1, GraphDirection.BOTH),
                allowed_acl_labels=("",),
            ),
            "allowed_acl_labels",
        ),
        (
            lambda: SearchRequest(
                "",
                "d",
                "t",
                1,
                SearchMode.HYBRID,
                {},
                (),
                None,
            ),
            "SearchRequest.query",
        ),
        (
            lambda: SearchRequest(
                "q",
                "d",
                "t",
                0,
                SearchMode.HYBRID,
                {},
                (),
                None,
            ),
            "SearchRequest.top_k",
        ),
        (
            lambda: SearchHit(
                DocumentChunk("c", "d", "t", "s", "x"),
                "bad",  # type: ignore[arg-type]
                "score",
            ),
            "numeric",
        ),
        (
            lambda: SearchHit(
                DocumentChunk("c", "d", "t", "s", "x"),
                1.0,
                "",
            ),
            "score_type",
        ),
        (
            lambda: Citation("", "c", None, 1.0, "score"),
            "Citation.source_id",
        ),
        (
            lambda: QueryCommand("", "q", "d", "s"),
            "QueryCommand.request_id",
        ),
    ],
)
def test_domain_models_reject_invalid_values(factory, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        factory()


def test_search_and_citation_models_preserve_score_metadata() -> None:
    chunk = DocumentChunk("c", "d", "t", "s", "text")
    hit = SearchHit(chunk, 1, "bm25")
    citation = Citation("s", "c", None, hit.score, hit.score_type, {"x": 1})

    assert hit.score == 1.0
    assert citation.metadata["x"] == 1


def test_pipeline_models_validate_invariants() -> None:
    assert PipelineRunStatus.RUNNING.value == "running"
    terminal = PipelineStepDefinition("end", "finalize", "1.0", terminal=True)
    pipeline = PipelineDefinition("p", "1.0", "end", 1, (terminal,), "a" * 64)
    assert pipeline.step("end") == terminal

    with pytest.raises(ValidationError, match="Unknown pipeline step"):
        pipeline.step("missing")
    with pytest.raises(ValidationError, match="max_steps"):
        PipelineDefinition("p", "1.0", "end", 0, (terminal,), "a" * 64)
    with pytest.raises(ValidationError, match="cannot be empty"):
        PipelineDefinition("p", "1.0", "end", 1, (), "a" * 64)
    with pytest.raises(ValidationError, match="terminal"):
        PipelineStepDefinition(
            "end",
            "finalize",
            "1.0",
            next_step_id="other",
            terminal=True,
        )
    with pytest.raises(ValidationError, match="non-terminal"):
        PipelineStepDefinition("start", "normalize_query", "1.0")
    routed = PipelineStepDefinition(
        "route",
        "router",
        "1.0",
        routes={" selected ": " end "},
    )
    assert dict(routed.routes) == {"selected": "end"}
    with pytest.raises(ValidationError, match="routes key"):
        PipelineStepDefinition("route", "router", "1.0", routes={"": "end"})


def test_pipeline_audit_models_require_valid_values() -> None:
    run = PipelineRun(
        "run",
        "request",
        "session",
        "domain",
        "tenant",
        "pipeline",
        "1.0",
        "a" * 64,
        "query",
    )
    assert run.run_id == "run"
    with pytest.raises(ValidationError, match="PipelineRun.query"):
        PipelineRun(
            "run",
            "request",
            "session",
            "domain",
            "tenant",
            "pipeline",
            "1.0",
            "a" * 64,
            "",
        )

    audit = PipelineStepAudit("run", 1, "step", "action", "1.0", 0, None)
    assert audit.duration_ms == 0
    with pytest.raises(ValidationError, match="sequence_number"):
        PipelineStepAudit("run", 0, "step", "action", "1.0", 0, None)
    with pytest.raises(ValidationError, match="duration_ms"):
        PipelineStepAudit("run", 1, "step", "action", "1.0", -1, None)
