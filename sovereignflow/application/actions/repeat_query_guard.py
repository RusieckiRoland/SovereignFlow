from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineDefinitionError, PipelineExecutionError

from ._config import _reject_unknown_config_keys, _required_config_string
from ._retrieval import _normalize_guard_query
from ._state import _state_value

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_REPEAT_QUERY_GUARD_ALLOWED_KEYS = frozenset({"source", "query_parser", "on_ok", "on_repeat"})
_ROUTER_SOURCES = frozenset({"answer", "last_model_response", "normalized_query", "evidence"})
_REPEAT_QUERY_PARSERS = frozenset({"raw", "json"})


@dataclass(frozen=True)
class RepeatQueryGuardConfig:
    source: str
    query_parser: str
    on_ok: str
    on_repeat: str


def _repeat_query_guard_config(step) -> RepeatQueryGuardConfig:
    _reject_unknown_config_keys(step, _REPEAT_QUERY_GUARD_ALLOWED_KEYS, "repeat_query_guard")
    source = _required_config_string(step, "source", "repeat_query_guard")
    if source not in _ROUTER_SOURCES:
        raise PipelineDefinitionError("repeat_query_guard.source is not allowed")
    parser = str(step.config.get("query_parser", "raw")).strip()
    if parser not in _REPEAT_QUERY_PARSERS:
        raise PipelineDefinitionError("repeat_query_guard.query_parser is not allowed")
    on_ok = _required_config_string(step, "on_ok", "repeat_query_guard")
    on_repeat = _required_config_string(step, "on_repeat", "repeat_query_guard")
    for route_name in (on_ok, on_repeat):
        if route_name not in step.routes:
            raise PipelineDefinitionError("repeat_query_guard route is not declared in routes")
    return RepeatQueryGuardConfig(
        source=source,
        query_parser=parser,
        on_ok=on_ok,
        on_repeat=on_repeat,
    )


def _guard_query(config: RepeatQueryGuardConfig, context: PipelineContext) -> str:
    raw = str(_state_value(config.source, context) or "")
    if config.query_parser == "raw":
        return raw
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PipelineExecutionError("repeat_query_guard received invalid JSON") from exc
    if not isinstance(payload, dict):
        raise PipelineExecutionError("repeat_query_guard JSON payload must be an object")
    query = payload.get("query", "")
    if not isinstance(query, str):
        raise PipelineExecutionError("repeat_query_guard query must be a string")
    return query


class RepeatQueryGuardAction:
    action_id = "repeat_query_guard"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _repeat_query_guard_config(step)

    def execute(self, step, context: PipelineContext) -> str:
        config = _repeat_query_guard_config(step)
        query = _guard_query(config, context)
        normalized = _normalize_guard_query(query)
        if not normalized or normalized in context.retrieval_queries_asked_norm:
            context.last_route = config.on_repeat
            return config.on_repeat
        context.retrieval_queries_asked_norm.add(normalized)
        context.last_route = config.on_ok
        return config.on_ok
