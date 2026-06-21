from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sovereignflow.domain import (
    PipelineDefinitionError,
    PipelineExecutionError,
    SearchMode,
    SearchRequest,
)

from ._config import _positive_config_integer, _reject_unknown_config_keys, _required_config_string
from ._retrieval import _normalize_guard_query, _verify_retrieval_boundary

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_RETRIEVE_ALLOWED_KEYS = frozenset({"query_source", "search_mode", "top_k", "filters"})
_RETRIEVE_QUERY_SOURCES = frozenset({"normalized_query", "command_query"})


@dataclass(frozen=True)
class RetrieveConfig:
    query_source: str
    search_mode: SearchMode
    top_k: int
    filters: Mapping[str, Any]


def _retrieve_config(step) -> RetrieveConfig:
    _reject_unknown_config_keys(step, _RETRIEVE_ALLOWED_KEYS, "retrieve")
    query_source = _required_config_string(step, "query_source", "retrieve")
    if query_source not in _RETRIEVE_QUERY_SOURCES:
        raise PipelineDefinitionError(f"Step '{step.step_id}' retrieve.query_source is not allowed")
    search_mode = _search_mode(step)
    filters = step.config.get("filters", {})
    if not isinstance(filters, Mapping):
        raise PipelineDefinitionError(f"Step '{step.step_id}' retrieve.filters must be a mapping")
    return RetrieveConfig(
        query_source=query_source,
        search_mode=search_mode,
        top_k=_positive_config_integer(step, "top_k", "retrieve"),
        filters=filters,
    )


def _search_mode(step) -> SearchMode:
    raw = _required_config_string(step, "search_mode", "retrieve")
    try:
        return SearchMode(raw)
    except ValueError as exc:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' retrieve.search_mode is invalid"
        ) from exc


def _retrieval_query(source: str, context: PipelineContext) -> str:
    if source == "normalized_query":
        return context.normalized_query
    if source == "command_query":
        return context.command.query
    raise PipelineExecutionError(f"Unsupported retrieve query source '{source}'")


class RetrieveAction:
    action_id = "retrieve"
    behavior_version = "1.0"
    requires = frozenset({"normalized_query", "domain"})
    provides = frozenset({"hits"})

    def validate_config(self, step) -> None:
        _retrieve_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        config = _retrieve_config(step)
        domain = context.domain
        authorization = context.command.authorization
        filters = {**context.command.filters, **domain.retrieval.filters, **config.filters}
        request_query = _retrieval_query(config.query_source, context)
        context.seed_hits = tuple(
            context.retrieval.search(
                SearchRequest(
                    query=request_query,
                    domain=domain.name,
                    tenant_id=authorization.tenant_id,
                    top_k=config.top_k,
                    mode=config.search_mode,
                    filters=filters,
                    allowed_acl_labels=authorization.acl_labels,
                    security_model=domain.security_model,
                    subject_security=authorization.security,
                )
            )
        )
        context.retrieval_queries_asked_norm.add(_normalize_guard_query(request_query))
        _verify_retrieval_boundary(domain, authorization, context.seed_hits)
        context.hits = context.seed_hits
        return None
