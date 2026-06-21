from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sovereignflow.domain import PipelineDefinitionError

from ._config import (
    _reject_unknown_config_keys,
    _required_rule_string,
)
from ._state import _set_state_value, _state_value, _transform_value

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_SET_VARIABLES_ALLOWED_KEYS = frozenset({"rules"})
_SET_VARIABLE_RULE_KEYS = frozenset({"set", "from", "value", "transform"})
_SET_VARIABLE_SOURCES = frozenset(
    {
        "answer",
        "last_model_response",
        "normalized_query",
        "evidence",
        "context_chunk_ids",
        "last_route",
        "last_prefix",
        "variables",
    }
)
_SET_VARIABLE_TARGETS = frozenset(
    {"answer", "last_model_response", "normalized_query", "evidence", "variables"}
)
_SET_VARIABLE_TRANSFORMS = frozenset({"copy", "to_list", "split_lines", "parse_json", "clear"})


@dataclass(frozen=True)
class SetVariableRule:
    target: str
    source: str
    literal_value: Any
    has_literal: bool
    transform: str


def _set_variables_config(step) -> tuple[SetVariableRule, ...]:
    _reject_unknown_config_keys(step, _SET_VARIABLES_ALLOWED_KEYS, "set_variables")
    raw_rules = step.config.get("rules")
    if not isinstance(raw_rules, tuple) or not raw_rules:
        raise PipelineDefinitionError("set_variables.rules must be a non-empty list")
    rules = []
    for index, raw_rule in enumerate(raw_rules, start=1):
        if not isinstance(raw_rule, Mapping):
            raise PipelineDefinitionError("set_variables.rules[] must be a mapping")
        unknown = set(raw_rule) - _SET_VARIABLE_RULE_KEYS
        if unknown:
            raise PipelineDefinitionError(
                "set_variables rule has unsupported fields: " + ", ".join(sorted(unknown))
            )
        target = _required_rule_string(raw_rule, "set", "set_variables.rules[].set")
        if "." in target:
            raise PipelineDefinitionError("set_variables target must not contain dot paths")
        if target not in _SET_VARIABLE_TARGETS:
            raise PipelineDefinitionError("set_variables target is not allowed")
        has_source = "from" in raw_rule
        has_literal = "value" in raw_rule
        if has_source == has_literal:
            raise PipelineDefinitionError(
                f"set_variables rule {index} must define exactly one of from or value"
            )
        source = ""
        if has_source:
            source = _required_rule_string(raw_rule, "from", "set_variables.rules[].from")
            if "." in source:
                raise PipelineDefinitionError("set_variables source must not contain dot paths")
            if source not in _SET_VARIABLE_SOURCES:
                raise PipelineDefinitionError("set_variables source is not allowed")
        transform = str(raw_rule.get("transform", "copy")).strip()
        if transform not in _SET_VARIABLE_TRANSFORMS:
            raise PipelineDefinitionError("set_variables transform is not allowed")
        rules.append(
            SetVariableRule(
                target=target,
                source=source,
                literal_value=raw_rule.get("value"),
                has_literal=has_literal,
                transform=transform,
            )
        )
    return tuple(rules)


class SetVariablesAction:
    action_id = "set_variables"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _set_variables_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        for rule in _set_variables_config(step):
            value = rule.literal_value if rule.has_literal else _state_value(rule.source, context)
            _set_state_value(rule.target, _transform_value(rule.transform, value), context)
        return None
