from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sovereignflow.domain import (
    DependencyUnavailableError,
    DocumentChunk,
    DocumentSecurity,
    GraphDirection,
    GraphNodeRef,
    GraphTraversalRequest,
    IngestionError,
    SearchHit,
    SecurityModelKind,
)

from .postgres_support import psycopg_module

_NODE_SEPARATOR = "\x1f"


class PostgreSQLGraphTraversal:
    name = "graph_traversal"

    def __init__(self, connection_url: str, *, timeout_seconds: int) -> None:
        self._connection_url = connection_url
        self._timeout_seconds = timeout_seconds

    def check(self) -> None:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise DependencyUnavailableError("PostgreSQL graph health check failed")
        except DependencyUnavailableError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL graph health check failed") from exc

    def expand(self, request: GraphTraversalRequest) -> tuple[SearchHit, ...]:
        seed_states = {
            GraphNodeRef(hit.chunk.source_id, hit.chunk.chunk_id): (hit.score, ())
            for hit in request.seeds
        }
        visited = set(seed_states)
        frontier = seed_states
        expanded: list[SearchHit] = []
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                for depth in range(1, request.max_depth + 1):
                    proposals = self._proposals(
                        cursor,
                        request=request,
                        frontier=frontier,
                        visited=visited,
                    )
                    if not proposals:
                        break
                    chunks = self._load_chunks(
                        cursor,
                        request=request,
                        nodes=proposals,
                    )
                    next_frontier = {}
                    for node, state in sorted(
                        proposals.items(),
                        key=lambda item: (
                            -item[1][0],
                            item[0].source_id,
                            item[0].chunk_id,
                            item[1][1],
                        ),
                    ):
                        chunk = chunks.get(node)
                        if chunk is None:
                            continue
                        score, path = state
                        expanded.append(
                            SearchHit(
                                chunk=DocumentChunk(
                                    chunk_id=chunk.chunk_id,
                                    domain=chunk.domain,
                                    tenant_id=chunk.tenant_id,
                                    source_id=chunk.source_id,
                                    text=chunk.text,
                                    source_uri=chunk.source_uri,
                                    metadata={
                                        **dict(chunk.metadata),
                                        "graph_depth": depth,
                                        "graph_path": list(path),
                                    },
                                    acl_labels=chunk.acl_labels,
                                    security=chunk.security,
                                ),
                                score=score / (depth + 1),
                                score_type="graph",
                            )
                        )
                        visited.add(node)
                        next_frontier[node] = state
                        if len(expanded) == request.max_nodes:
                            return tuple(expanded)
                    if not next_frontier:
                        break
                    frontier = next_frontier
        except (DependencyUnavailableError, IngestionError):
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL graph traversal failed") from exc
        return tuple(expanded)

    def _proposals(
        self,
        cursor: Any,
        *,
        request: GraphTraversalRequest,
        frontier: dict[GraphNodeRef, tuple[float, tuple[str, ...]]],
        visited: set[GraphNodeRef],
    ) -> dict[GraphNodeRef, tuple[float, tuple[str, ...]]]:
        frontier_keys = [_node_key(node) for node in frontier]
        outgoing = request.direction in {GraphDirection.OUTGOING, GraphDirection.BOTH}
        incoming = request.direction in {GraphDirection.INCOMING, GraphDirection.BOTH}
        cursor.execute(
            """
            SELECT relationship.from_source_id, relationship.from_chunk_id,
                   relationship.to_source_id, relationship.to_chunk_id,
                   relationship.relationship_type
            FROM graph.relationships relationship
            JOIN ingestion.sources owner
              ON owner.tenant_id = relationship.tenant_id
             AND owner.domain = relationship.domain
             AND owner.source_id = relationship.owner_source_id
             AND owner.current_version = relationship.owner_source_version
            WHERE relationship.tenant_id = %s
              AND relationship.domain = %s
              AND (
                  cardinality(%s::text[]) = 0
                  OR relationship.relationship_type = ANY(%s::text[])
              )
              AND (
                  (
                      %s
                      AND concat_ws(chr(31), relationship.from_source_id,
                                    relationship.from_chunk_id) = ANY(%s::text[])
                  )
                  OR
                  (
                      %s
                      AND concat_ws(chr(31), relationship.to_source_id,
                                    relationship.to_chunk_id) = ANY(%s::text[])
                  )
              )
            ORDER BY relationship.relationship_type,
                     relationship.from_source_id,
                     relationship.from_chunk_id,
                     relationship.to_source_id,
                     relationship.to_chunk_id
            """,
            (
                request.tenant_id,
                request.domain,
                list(request.relationship_types),
                list(request.relationship_types),
                outgoing,
                frontier_keys,
                incoming,
                frontier_keys,
            ),
        )
        proposals: dict[GraphNodeRef, tuple[float, tuple[str, ...]]] = {}
        for row in cursor.fetchall():
            from_node = GraphNodeRef(str(row[0]), str(row[1]))
            to_node = GraphNodeRef(str(row[2]), str(row[3]))
            relationship_type = str(row[4])
            if outgoing and from_node in frontier:
                _select_proposal(
                    proposals,
                    node=to_node,
                    parent=frontier[from_node],
                    relationship_type=relationship_type,
                    visited=visited,
                )
            if incoming and to_node in frontier:
                _select_proposal(
                    proposals,
                    node=from_node,
                    parent=frontier[to_node],
                    relationship_type=relationship_type,
                    visited=visited,
                )
        return proposals

    @staticmethod
    def _load_chunks(
        cursor: Any,
        *,
        request: GraphTraversalRequest,
        nodes: Iterable[GraphNodeRef],
    ) -> dict[GraphNodeRef, DocumentChunk]:
        security_clause, security_params = _security_filter(request)
        cursor.execute(
            """
            SELECT chunk.source_id, chunk.chunk_id, chunk.source_uri,
                   chunk.text_content, chunk.metadata_json, chunk.acl_labels,
                   chunk.clearance_label, chunk.classification_labels
            FROM ingestion.sources source
            JOIN ingestion.chunks chunk
              ON chunk.tenant_id = source.tenant_id
             AND chunk.domain = source.domain
             AND chunk.source_id = source.source_id
             AND chunk.source_version = source.current_version
            WHERE source.tenant_id = %s
              AND source.domain = %s
              AND concat_ws(chr(31), chunk.source_id, chunk.chunk_id) = ANY(%s::text[])
              AND (
                  cardinality(chunk.acl_labels) = 0
                  OR chunk.acl_labels && %s::text[]
              )
              AND """
            + security_clause
            + """
            ORDER BY chunk.source_id, chunk.chunk_id
            """,
            (
                request.tenant_id,
                request.domain,
                [_node_key(node) for node in nodes],
                list(request.allowed_acl_labels),
                *security_params,
            ),
        )
        result = {}
        for row in cursor.fetchall():
            node = GraphNodeRef(str(row[0]), str(row[1]))
            result[node] = DocumentChunk(
                source_id=node.source_id,
                chunk_id=node.chunk_id,
                domain=request.domain,
                tenant_id=request.tenant_id,
                source_uri=row[2],
                text=str(row[3]),
                metadata=_metadata(row[4]),
                acl_labels=tuple(row[5] or ()),
                security=DocumentSecurity(
                    clearance_label=row[6],
                    classification_labels=tuple(row[7] or ()),
                ),
            )
        return result


def _node_key(node: GraphNodeRef) -> str:
    return f"{node.source_id}{_NODE_SEPARATOR}{node.chunk_id}"


def _security_filter(request: GraphTraversalRequest) -> tuple[str, tuple[object, ...]]:
    model = request.security_model
    if model.kind == SecurityModelKind.NONE:
        return "TRUE", ()
    if model.kind == SecurityModelKind.CLEARANCE_LEVEL:
        if model.clearance_level is None or request.subject_security.clearance_label is None:
            return "FALSE", ()
        labels = model.clearance_level.allowed_document_labels(
            request.subject_security.clearance_label
        )
        return "chunk.clearance_label = ANY(%s::text[])", (list(labels),)
    if model.kind == SecurityModelKind.CLASSIFICATION_LABELS:
        if model.classification_labels is None:
            return "FALSE", ()
        subject_labels = model.classification_labels.validate_labels(
            request.subject_security.classification_labels,
            "GraphTraversalRequest.subject_security.classification_labels",
        )
        return "chunk.classification_labels <@ %s::text[]", (list(subject_labels),)
    return "FALSE", ()


def _select_proposal(
    proposals: dict[GraphNodeRef, tuple[float, tuple[str, ...]]],
    *,
    node: GraphNodeRef,
    parent: tuple[float, tuple[str, ...]],
    relationship_type: str,
    visited: set[GraphNodeRef],
) -> None:
    if node in visited:
        return
    candidate = (parent[0], (*parent[1], relationship_type))
    existing = proposals.get(node)
    if existing is None or (-candidate[0], candidate[1]) < (-existing[0], existing[1]):
        proposals[node] = candidate


def _metadata(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError) as exc:
        raise IngestionError("Stored graph chunk metadata is invalid") from exc
    if not isinstance(decoded, dict):
        raise IngestionError("Stored graph chunk metadata is invalid")
    return decoded
