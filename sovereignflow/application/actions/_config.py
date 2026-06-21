from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sovereignflow.domain import PipelineDefinitionError


def _reject_unknown_config_keys(step, allowed_keys: frozenset[str], action: str) -> None:
    unknown_keys = set(step.config) - allowed_keys
    if unknown_keys:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' has unsupported {action} fields: "
            f"{', '.join(sorted(unknown_keys))}"
        )


def _required_config_string(step, key: str, action: str) -> str:
    value = step.config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' {action}.{key} must be a non-empty string"
        )
    return value.strip()


def _required_rule_string(rule: Mapping[str, Any], key: str, field_name: str) -> str:
    value = rule.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(f"{field_name} must be a non-empty string")
    return value.strip()


def _route_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(f"{field_name} must be a non-empty string")
    return value.strip().lower()


def _positive_config_integer(step, key: str, action: str) -> int:
    value = step.config.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' {action}.{key} must be a positive integer"
        )
    return value


def _optional_boolean(step, key: str, action: str, *, default: bool) -> bool:
    value = step.config.get(key, default)
    if not isinstance(value, bool):
        raise PipelineDefinitionError(f"Step '{step.step_id}' {action}.{key} must be a boolean")
    return value
