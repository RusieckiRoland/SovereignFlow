from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from sovereignflow.domain import PipelineExecutionError

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext


def _state_value(source: str, context: PipelineContext) -> Any:
    values = {
        "answer": context.answer,
        "last_model_response": context.last_model_response,
        "normalized_query": context.normalized_query,
        "evidence": context.evidence,
        "context_chunk_ids": context.context_chunk_ids,
        "last_route": context.last_route,
        "last_prefix": context.last_prefix,
        "variables": dict(context.variables),
    }
    try:
        return values[source]
    except KeyError as exc:
        raise PipelineExecutionError(f"Unsupported state source '{source}'") from exc


def _set_state_value(target: str, value: Any, context: PipelineContext) -> None:
    if target == "answer":
        context.answer = _string_state_value(value, target)
        return
    if target == "last_model_response":
        context.last_model_response = _string_state_value(value, target)
        return
    if target == "normalized_query":
        context.normalized_query = _string_state_value(value, target)
        return
    if target == "evidence":
        context.evidence = _string_state_value(value, target)
        return
    if target == "variables":
        if not isinstance(value, Mapping):
            raise PipelineExecutionError("variables must be assigned from a mapping")
        context.variables = dict(value)
        return
    raise PipelineExecutionError(f"Unsupported state target '{target}'")


def _string_state_value(value: Any, target: str) -> str:
    if not isinstance(value, str):
        raise PipelineExecutionError(f"{target} must be assigned from a string")
    return value


def _transform_value(transform: str, value: Any) -> Any:
    if transform == "copy":
        return value
    if transform == "clear":
        return _clear_value(value)
    if transform == "to_list":
        return _to_list(value)
    if transform == "split_lines":
        return _split_lines(value)
    if transform == "parse_json":
        return _parse_json_value(value)
    raise PipelineExecutionError(f"Unsupported set_variables transform '{transform}'")


def _clear_value(value: Any) -> Any:
    if isinstance(value, str):
        return ""
    if isinstance(value, Mapping):
        return {}
    if isinstance(value, tuple | list | set):
        return []
    return None


def _to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    raise PipelineExecutionError("to_list requires null, string, or list-like input")


def _split_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        raise PipelineExecutionError("split_lines requires string input")
    return [line.strip() for line in value.splitlines() if line.strip()]


def _parse_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        raise PipelineExecutionError("parse_json requires string input")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise PipelineExecutionError("parse_json received invalid JSON") from exc
