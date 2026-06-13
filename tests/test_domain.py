from __future__ import annotations

from types import MappingProxyType

import pytest

from sovereignflow.domain import (
    Citation,
    DocumentChunk,
    DomainProfile,
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
