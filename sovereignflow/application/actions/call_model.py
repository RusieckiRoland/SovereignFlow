from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from sovereignflow.domain import PipelineDefinitionError, PipelineExecutionError, PolicyViolationError

from ._config import (
    _optional_boolean,
    _reject_unknown_config_keys,
    _required_config_string,
)
from ._retrieval import _citations_text, _retrieval_trace_summary

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_CALL_MODEL_ALLOWED_KEYS = frozenset(
    {
        "prompt_key",
        "user_parts",
        "temperature",
        "top_p",
        "max_tokens",
        "max_output_tokens",
        "use_history",
        "history_source",
    }
)
_USER_PART_KEYS = frozenset({"source", "template"})
_USER_PART_SOURCES = frozenset(
    {
        "normalized_query",
        "evidence",
        "context_chunk_ids",
        "citations_text",
        "retrieval_trace_summary",
        "conversation_history",
    }
)


@dataclass(frozen=True)
class UserPromptPart:
    name: str
    source: str
    template: str


@dataclass(frozen=True)
class CallModelConfig:
    prompt_key: str
    user_parts: tuple[UserPromptPart, ...]
    generation_parameters: Mapping[str, Any]
    use_history: bool
    history_source: str | None


def _required_user_part_string(step, part_name: str, part: Mapping[str, Any], key: str) -> str:
    value = part.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.user_parts.{part_name}.{key} "
            "must be a non-empty string"
        )
    return value.strip()


def _required_user_part_template(step, part_name: str, part: Mapping[str, Any]) -> str:
    value = part.get("template")
    if not isinstance(value, str) or not value:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.user_parts.{part_name}.template "
            "must be a non-empty string"
        )
    return value


def _bounded_number(step, key: str, *, minimum: float, maximum: float) -> float:
    value = step.config[key]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise PipelineDefinitionError(f"Step '{step.step_id}' call_model.{key} must be a number")
    normalized = float(value)
    if normalized < minimum or normalized > maximum:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.{key} must be between {minimum} and {maximum}"
        )
    return normalized


def _positive_integer(step, key: str) -> int:
    value = step.config[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.{key} must be a positive integer"
        )
    return value


def _generation_parameters(step) -> Mapping[str, Any]:
    if "max_tokens" in step.config and "max_output_tokens" in step.config:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' cannot define both max_tokens and max_output_tokens"
        )
    parameters: dict[str, Any] = {}
    if "temperature" in step.config:
        parameters["temperature"] = _bounded_number(step, "temperature", minimum=0, maximum=2)
    if "top_p" in step.config:
        parameters["top_p"] = _bounded_number(step, "top_p", minimum=0, maximum=1)
    if "max_tokens" in step.config:
        parameters["max_tokens"] = _positive_integer(step, "max_tokens")
    if "max_output_tokens" in step.config:
        parameters["max_tokens"] = _positive_integer(step, "max_output_tokens")
    return parameters


def _call_model_config(step) -> CallModelConfig:
    _reject_unknown_config_keys(step, _CALL_MODEL_ALLOWED_KEYS, "call_model")
    config = step.config
    prompt_key = _required_config_string(step, "prompt_key", "call_model")
    use_history = _optional_boolean(step, "use_history", "call_model", default=False)
    raw_history_source = config.get("history_source")
    history_source = None
    if raw_history_source is not None:
        history_source = _required_config_string(step, "history_source", "call_model")
    if use_history and history_source != "conversation_history":
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.history_source must be conversation_history"
        )
    if not use_history and history_source is not None:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.history_source requires use_history=true"
        )
    raw_parts = config.get("user_parts")
    if not isinstance(raw_parts, Mapping) or not raw_parts:
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' call_model.user_parts must be a non-empty mapping"
        )
    parts: list[UserPromptPart] = []
    for name, raw_part in raw_parts.items():
        if not isinstance(raw_part, Mapping):
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name} must be a mapping"
            )
        unknown_part_keys = set(raw_part) - _USER_PART_KEYS
        if unknown_part_keys:
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name} has unsupported fields: "
                f"{', '.join(sorted(unknown_part_keys))}"
            )
        source = _required_user_part_string(step, name, raw_part, "source")
        if source not in _USER_PART_SOURCES:
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name}.source is not allowed"
            )
        if source == "conversation_history" and not use_history:
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name}.source "
                "requires use_history=true"
            )
        template = _required_user_part_template(step, name, raw_part)
        if "{}" not in template:
            raise PipelineDefinitionError(
                f"Step '{step.step_id}' call_model.user_parts.{name}.template must contain {{}}"
            )
        parts.append(UserPromptPart(name=name.strip(), source=source, template=template))
    generation_parameters = _generation_parameters(step)
    return CallModelConfig(
        prompt_key=prompt_key,
        user_parts=tuple(parts),
        generation_parameters=generation_parameters,
        use_history=use_history,
        history_source=history_source,
    )


def _source_value(source: str, context: PipelineContext) -> str:
    if source == "normalized_query":
        return context.normalized_query
    if source == "evidence":
        return context.evidence
    if source == "context_chunk_ids":
        return "\n".join(context.context_chunk_ids)
    if source == "citations_text":
        return _citations_text(context.citations)
    if source == "retrieval_trace_summary":
        return _retrieval_trace_summary(context.hits)
    if source == "conversation_history":
        return context.conversation_history_text
    raise PipelineExecutionError(f"Unsupported call_model source '{source}'")


def _render_user_prompt(parts: Sequence[UserPromptPart], context: PipelineContext) -> str:
    rendered = []
    for part in parts:
        rendered.append(part.template.format(_source_value(part.source, context)))
    return "".join(rendered)


class CallModelAction:
    action_id = "call_model"
    behavior_version = "1.0"
    requires = frozenset({"normalized_query", "evidence", "domain", "model_transmission_policy"})
    provides = frozenset({"answer"})

    def validate_config(self, step) -> None:
        _call_model_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        if not context.model_transmission_checked:
            raise PipelineExecutionError("Model transmission policy has not been enforced")
        if not context.model_transmission_allowed:
            raise PolicyViolationError("Model transmission policy blocked the model call")
        config = _call_model_config(step)
        system_prompt = context.prompts.load(config.prompt_key)
        user_prompt = _render_user_prompt(config.user_parts, context)
        context.prompt_key = config.prompt_key
        context.system_prompt_hash = sha256(system_prompt.encode("utf-8")).hexdigest()
        started = time.monotonic()
        generation = context.model.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            generation_parameters=config.generation_parameters,
        )
        context.model_duration_ms = max(0, round((time.monotonic() - started) * 1000))
        context.answer = generation.text
        context.last_model_response = generation.text
        context.prompt_tokens = generation.prompt_tokens
        context.completion_tokens = generation.completion_tokens
        context.estimated_cost = generation.estimated_cost
        return None
