from __future__ import annotations

import re
from collections.abc import Sequence

from sovereignflow.domain import (
    Citation,
    DomainProfile,
    PolicyViolationError,
    SearchHit,
    document_visible_to_subject,
)


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


def _normalize_guard_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().lower())


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
