from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sovereignflow.domain import PipelineDefinitionError, PipelineExecutionError

from ._config import _reject_unknown_config_keys, _required_config_string, _route_name
from ._state import _state_value

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_JSON_DECISION_ROUTER_ALLOWED_KEYS = frozenset({"source", "allowed_decisions", "on_other"})
_ROUTER_SOURCES = frozenset({"answer", "last_model_response", "normalized_query", "evidence"})


@dataclass(frozen=True)
class JsonDecisionRouterConfig:
    source: str
    allowed_decisions: frozenset[str]
    on_other: str | None


def _json_decision(payload: Mapping[str, Any]) -> str:
    for key in ("decision", "route", "mode"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _json_decision_router_config(step) -> JsonDecisionRouterConfig:
    _reject_unknown_config_keys(step, _JSON_DECISION_ROUTER_ALLOWED_KEYS, "json_decision_router")
    source = _required_config_string(step, "source", "json_decision_router")
    if source not in _ROUTER_SOURCES:
        raise PipelineDefinitionError("json_decision_router.source is not allowed")
    raw_decisions = step.config.get("allowed_decisions")
    if not isinstance(raw_decisions, tuple) or not raw_decisions:
        raise PipelineDefinitionError(
            "json_decision_router.allowed_decisions must be a non-empty list"
        )
    decisions = frozenset(
        _route_name(item, "json_decision_router decision") for item in raw_decisions
    )
    undeclared = decisions - set(step.routes)
    if undeclared:
        raise PipelineDefinitionError("json_decision_router decisions must be declared in routes")
    raw_on_other = step.config.get("on_other")
    on_other = None
    if raw_on_other is not None:
        on_other = _required_config_string(step, "on_other", "json_decision_router")
        if on_other not in step.routes:
            raise PipelineDefinitionError(
                "json_decision_router.on_other route is not declared in routes"
            )
    return JsonDecisionRouterConfig(
        source=source,
        allowed_decisions=decisions,
        on_other=on_other,
    )


class JsonDecisionRouterAction:
    action_id = "json_decision_router"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _json_decision_router_config(step)

    def execute(self, step, context: PipelineContext) -> str:
        config = _json_decision_router_config(step)
        raw = str(_state_value(config.source, context) or "").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            if config.on_other is None:
                raise PipelineExecutionError(
                    "json_decision_router received invalid JSON"
                ) from exc
            context.last_route = config.on_other
            return config.on_other
        if not isinstance(payload, dict):
            if config.on_other is None:
                raise PipelineExecutionError(
                    "json_decision_router payload must be a JSON object"
                )
            context.last_route = config.on_other
            return config.on_other
        decision = _json_decision(payload)
        cleaned = {
            str(key): value
            for key, value in payload.items()
            if str(key).strip().lower() not in {"decision", "route", "mode"}
        }
        context.last_model_response = json.dumps(cleaned, sort_keys=True, separators=(",", ":"))
        if decision in config.allowed_decisions:
            context.last_route = decision
            return decision
        if config.on_other is None:
            raise PipelineExecutionError("json_decision_router decision is not allowed")
        context.last_route = config.on_other
        return config.on_other
