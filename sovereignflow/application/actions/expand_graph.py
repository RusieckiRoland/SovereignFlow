from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sovereignflow.domain import (
    GraphDirection,
    GraphTraversalRequest,
    PipelineDefinitionError,
)

from ._config import _positive_config_integer, _reject_unknown_config_keys, _required_config_string
from ._retrieval import _verify_retrieval_boundary

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_EXPAND_GRAPH_ALLOWED_KEYS = frozenset(
    {"enabled", "max_depth", "max_nodes", "direction", "relationship_types"}
)


@dataclass(frozen=True)
class ExpandGraphConfig:
    enabled: bool
    max_depth: int
    max_nodes: int
    direction: GraphDirection
    relationship_types: tuple[str, ...]


def _expand_graph_config(step) -> ExpandGraphConfig:
    _reject_unknown_config_keys(step, _EXPAND_GRAPH_ALLOWED_KEYS, "expand_graph")
    enabled = step.config.get("enabled")
    if not isinstance(enabled, bool):
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' expand_graph.enabled must be a boolean"
        )
    direction = _graph_direction(step)
    return ExpandGraphConfig(
        enabled=enabled,
        max_depth=_positive_config_integer(step, "max_depth", "expand_graph"),
        max_nodes=_positive_config_integer(step, "max_nodes", "expand_graph"),
        direction=direction,
        relationship_types=_relationship_types(step),
    )


def _graph_direction(step) -> GraphDirection:
    raw = _required_config_string(step, "direction", "expand_graph")
    try:
        return GraphDirection(raw)
    except ValueError as exc:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' expand_graph.direction is invalid"
        ) from exc


def _relationship_types(step) -> tuple[str, ...]:
    raw = step.config.get("relationship_types")
    if not isinstance(raw, tuple) or any(
        not isinstance(item, str) or not item.strip() for item in raw
    ):
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' expand_graph.relationship_types must be a list of strings"
        )
    return tuple(item.strip() for item in raw)


class ExpandGraphAction:
    action_id = "expand_graph"
    behavior_version = "1.0"
    requires = frozenset({"hits", "domain"})
    provides = frozenset({"hits"})

    def validate_config(self, step) -> None:
        _expand_graph_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        config = _expand_graph_config(step)
        if not config.enabled or not context.hits:
            return None
        authorization = context.command.authorization
        expanded = tuple(
            context.graph.expand(
                GraphTraversalRequest(
                    seeds=context.hits,
                    domain=context.domain.name,
                    tenant_id=authorization.tenant_id,
                    max_depth=config.max_depth,
                    max_nodes=config.max_nodes,
                    direction=config.direction,
                    relationship_types=config.relationship_types,
                    allowed_acl_labels=authorization.acl_labels,
                    security_model=context.domain.security_model,
                    subject_security=authorization.security,
                )
            )
        )
        _verify_retrieval_boundary(context.domain, authorization, expanded)
        context.graph_hits = expanded
        unique = {hit.chunk.chunk_id: hit for hit in context.seed_hits}
        for hit in expanded:
            unique.setdefault(hit.chunk.chunk_id, hit)
        context.hits = tuple(unique.values())
        return None
