from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineDefinitionError

from ._config import _reject_unknown_config_keys, _required_config_string, _route_name
from ._state import _state_value

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_PREFIX_ROUTER_ALLOWED_KEYS = frozenset({"source", "prefixes", "on_other"})
_ROUTER_SOURCES = frozenset({"answer", "last_model_response", "normalized_query", "evidence"})


@dataclass(frozen=True)
class PrefixRouterConfig:
    source: str
    prefixes: tuple[tuple[str, str], ...]
    on_other: str


def _prefix_router_config(step) -> PrefixRouterConfig:
    _reject_unknown_config_keys(step, _PREFIX_ROUTER_ALLOWED_KEYS, "prefix_router")
    source = _required_config_string(step, "source", "prefix_router")
    if source not in _ROUTER_SOURCES:
        raise PipelineDefinitionError("prefix_router.source is not allowed")
    raw_prefixes = step.config.get("prefixes")
    if not isinstance(raw_prefixes, Mapping) or not raw_prefixes:
        raise PipelineDefinitionError("prefix_router.prefixes must be a non-empty mapping")
    prefixes = []
    for route_name, prefix in raw_prefixes.items():
        normalized_route = _route_name(route_name, "prefix_router.prefixes route")
        if normalized_route not in step.routes:
            raise PipelineDefinitionError("prefix_router prefix route is not declared in routes")
        if not isinstance(prefix, str) or not prefix:
            raise PipelineDefinitionError("prefix_router prefix must be a non-empty string")
        prefixes.append((normalized_route, prefix))
    on_other = _required_config_string(step, "on_other", "prefix_router")
    if on_other not in step.routes:
        raise PipelineDefinitionError("prefix_router.on_other route is not declared in routes")
    return PrefixRouterConfig(source=source, prefixes=tuple(prefixes), on_other=on_other)


class PrefixRouterAction:
    action_id = "prefix_router"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _prefix_router_config(step)

    def execute(self, step, context: PipelineContext) -> str:
        config = _prefix_router_config(step)
        text = str(_state_value(config.source, context) or "").strip()
        for route_name, prefix in config.prefixes:
            if text.startswith(prefix):
                context.last_route = route_name
                context.last_prefix = route_name
                context.last_model_response = text.removeprefix(prefix).strip()
                return route_name
        context.last_route = config.on_other
        context.last_prefix = ""
        context.last_model_response = text
        return config.on_other
