from __future__ import annotations

from types import SimpleNamespace

import pytest

from sovereignflow.domain import (
    DependencyUnavailableError,
    DocumentChunk,
    GraphDirection,
    GraphNodeRef,
    GraphTraversalRequest,
    IngestionError,
    SearchHit,
)
from sovereignflow.infrastructure import PostgreSQLGraphTraversal
from sovereignflow.infrastructure import graph as graph_module


def hit(source_id: str = "source-1", chunk_id: str = "chunk-1", score: float = 0.8):
    return SearchHit(
        DocumentChunk(
            chunk_id=chunk_id,
            domain="general",
            tenant_id="tenant-a",
            source_id=source_id,
            text="seed",
            acl_labels=("public",),
            classification_level=1,
        ),
        score,
        "hybrid",
    )


def request(
    *,
    direction: GraphDirection = GraphDirection.OUTGOING,
    max_depth: int = 2,
    max_nodes: int = 10,
) -> GraphTraversalRequest:
    return GraphTraversalRequest(
        seeds=(hit(),),
        domain="general",
        tenant_id="tenant-a",
        max_depth=max_depth,
        max_nodes=max_nodes,
        direction=direction,
        relationship_types=("references",),
        allowed_acl_labels=("public",),
        max_classification_level=1,
    )


def chunk_row(source_id: str, chunk_id: str, text: str = "related"):
    return (
        source_id,
        chunk_id,
        f"https://example.test/{source_id}/{chunk_id}",
        text,
        '{"kind":"graph"}',
        ["public"],
        1,
    )


class Cursor:
    def __init__(
        self,
        *,
        one=(),
        all_rows=(),
        error: Exception | None = None,
    ) -> None:
        self.one = list(one)
        self.all_rows = list(all_rows)
        self.error = error
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, statement, parameters=None) -> None:
        if self.error:
            raise self.error
        self.executed.append((str(statement), parameters))

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return self.all_rows.pop(0) if self.all_rows else []


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def cursor(self):
        return self.cursor_value


def install(monkeypatch, cursor: Cursor) -> None:
    monkeypatch.setattr(
        graph_module,
        "psycopg_module",
        lambda: SimpleNamespace(connect=lambda *args, **kwargs: Connection(cursor)),
    )


def traversal() -> PostgreSQLGraphTraversal:
    return PostgreSQLGraphTraversal("postgresql://test", timeout_seconds=3)


def test_graph_traversal_expands_bounded_bfs_and_preserves_path(monkeypatch) -> None:
    cursor = Cursor(
        all_rows=[
            [("source-1", "chunk-1", "source-2", "chunk-2", "references")],
            [chunk_row("source-2", "chunk-2")],
            [("source-2", "chunk-2", "source-3", "chunk-3", "contains")],
            [chunk_row("source-3", "chunk-3", "deep")],
        ]
    )
    install(monkeypatch, cursor)

    result = traversal().expand(request())

    assert [item.chunk.source_id for item in result] == ["source-2", "source-3"]
    assert result[0].score == 0.4
    assert result[1].score == pytest.approx(0.8 / 3)
    assert result[1].chunk.metadata["graph_path"] == ["references", "contains"]
    assert result[1].chunk.metadata["graph_depth"] == 2
    assert cursor.executed[0][1][4:8] == (
        True,
        ["source-1\x1fchunk-1"],
        False,
        ["source-1\x1fchunk-1"],
    )


def test_graph_traversal_supports_incoming_and_both_directions(monkeypatch) -> None:
    incoming_cursor = Cursor(
        all_rows=[
            [("source-2", "chunk-2", "source-1", "chunk-1", "references")],
            [chunk_row("source-2", "chunk-2")],
        ]
    )
    install(monkeypatch, incoming_cursor)
    incoming = traversal().expand(request(direction=GraphDirection.INCOMING, max_depth=1))
    assert incoming[0].chunk.source_id == "source-2"
    assert incoming_cursor.executed[0][1][4] is False
    assert incoming_cursor.executed[0][1][6] is True

    both_cursor = Cursor(
        all_rows=[
            [
                ("source-1", "chunk-1", "source-2", "chunk-2", "out"),
                ("source-3", "chunk-3", "source-1", "chunk-1", "in"),
            ],
            [
                chunk_row("source-2", "chunk-2"),
                chunk_row("source-3", "chunk-3"),
            ],
        ]
    )
    install(monkeypatch, both_cursor)
    both = traversal().expand(request(direction=GraphDirection.BOTH, max_depth=1))
    assert {item.chunk.source_id for item in both} == {"source-2", "source-3"}


def test_graph_traversal_is_cycle_safe_security_filtered_and_limited(monkeypatch) -> None:
    cursor = Cursor(
        all_rows=[
            [
                ("source-1", "chunk-1", "source-1", "chunk-1", "cycle"),
                ("source-1", "chunk-1", "source-2", "chunk-2", "references"),
                ("source-1", "chunk-1", "source-3", "chunk-3", "references"),
            ],
            [chunk_row("source-2", "chunk-2")],
        ]
    )
    install(monkeypatch, cursor)

    result = traversal().expand(request(max_depth=1, max_nodes=1))

    assert len(result) == 1
    assert result[0].chunk.source_id == "source-2"


def test_graph_traversal_handles_empty_layers_and_missing_chunks(monkeypatch) -> None:
    install(monkeypatch, Cursor(all_rows=[[]]))
    assert traversal().expand(request()) == ()

    install(
        monkeypatch,
        Cursor(
            all_rows=[
                [("source-1", "chunk-1", "source-2", "chunk-2", "references")],
                [],
            ]
        ),
    )
    assert traversal().expand(request()) == ()


def test_graph_traversal_selects_strongest_deterministic_parent(monkeypatch) -> None:
    seeds = (hit("source-1", "chunk-1", 0.4), hit("source-2", "chunk-2", 0.9))
    graph_request = GraphTraversalRequest(
        seeds=seeds,
        domain="general",
        tenant_id="tenant-a",
        max_depth=1,
        max_nodes=10,
        direction=GraphDirection.OUTGOING,
        relationship_types=(),
        allowed_acl_labels=("public",),
        max_classification_level=None,
    )
    cursor = Cursor(
        all_rows=[
            [
                ("source-1", "chunk-1", "source-3", "chunk-3", "weak"),
                ("source-2", "chunk-2", "source-3", "chunk-3", "strong"),
            ],
            [chunk_row("source-3", "chunk-3")],
        ]
    )
    install(monkeypatch, cursor)

    result = traversal().expand(graph_request)

    assert result[0].score == 0.45
    assert result[0].chunk.metadata["graph_path"] == ["strong"]


def test_graph_health_and_failures_are_explicit(monkeypatch) -> None:
    install(monkeypatch, Cursor(one=[(1,)]))
    graph = traversal()
    assert graph.name == "graph_traversal"
    graph.check()

    install(monkeypatch, Cursor(one=[None]))
    with pytest.raises(DependencyUnavailableError, match="health"):
        graph.check()

    install(monkeypatch, Cursor(error=RuntimeError("down")))
    with pytest.raises(DependencyUnavailableError, match="health"):
        graph.check()
    with pytest.raises(DependencyUnavailableError, match="traversal failed"):
        graph.expand(request())


def test_graph_metadata_and_known_failures_are_preserved(monkeypatch) -> None:
    assert graph_module._metadata('{"ok":true}') == {"ok": True}
    with pytest.raises(IngestionError, match="metadata"):
        graph_module._metadata("{")
    with pytest.raises(IngestionError, match="metadata"):
        graph_module._metadata([])

    cursor = Cursor(
        all_rows=[
            [("source-1", "chunk-1", "source-2", "chunk-2", "references")],
            [(*chunk_row("source-2", "chunk-2")[:4], "[]", ["public"], 1)],
        ]
    )
    install(monkeypatch, cursor)
    with pytest.raises(IngestionError, match="metadata"):
        traversal().expand(request())


def test_proposal_helper_ignores_visited_and_keeps_stable_tie_break() -> None:
    node = GraphNodeRef("source-2", "chunk-2")
    proposals = {}
    graph_module._select_proposal(
        proposals,
        node=node,
        parent=(0.5, ("z",)),
        relationship_type="b",
        visited=set(),
    )
    graph_module._select_proposal(
        proposals,
        node=node,
        parent=(0.5, ("a",)),
        relationship_type="a",
        visited=set(),
    )
    graph_module._select_proposal(
        proposals,
        node=node,
        parent=(0.4, ()),
        relationship_type="better-name",
        visited=set(),
    )
    graph_module._select_proposal(
        proposals,
        node=GraphNodeRef("visited", "node"),
        parent=(1.0, ()),
        relationship_type="ignored",
        visited={GraphNodeRef("visited", "node")},
    )

    assert proposals[node] == (0.5, ("a", "a"))
    assert graph_module._node_key(node) == "source-2\x1fchunk-2"
