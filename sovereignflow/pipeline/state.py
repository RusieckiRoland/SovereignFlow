from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Citation, QueryRequest, SearchHit


@dataclass
class PipelineState:
    request: QueryRequest
    retrieval_query: str = ""
    retrieval_filters: dict[str, Any] = field(default_factory=dict)
    search_hits: list[SearchHit] = field(default_factory=list)
    context_blocks: list[str] = field(default_factory=list)
    last_model_response: str = ""
    final_answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

