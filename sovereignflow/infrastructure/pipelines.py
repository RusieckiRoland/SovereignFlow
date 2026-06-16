from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from sovereignflow.domain import (
    PipelineDefinition,
    PipelineDefinitionError,
    PipelineStepDefinition,
    ValidationError,
)


class YamlPipelineRepository:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def load(self, pipeline_name: str) -> PipelineDefinition:
        path = (self._root / f"{pipeline_name}.yaml").resolve()
        if self._root not in path.parents:
            raise PipelineDefinitionError("Pipeline path escapes the configured directory")
        if not path.is_file():
            raise PipelineDefinitionError(f"Pipeline does not exist: {pipeline_name}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise PipelineDefinitionError(f"Cannot read pipeline: {pipeline_name}") from exc
        return self._parse(raw)

    @staticmethod
    def _parse(raw: Any) -> PipelineDefinition:
        if not isinstance(raw, dict) or set(raw) != {"pipeline"}:
            raise PipelineDefinitionError("Pipeline YAML must contain only the 'pipeline' root")
        pipeline = raw["pipeline"]
        if not isinstance(pipeline, dict):
            raise PipelineDefinitionError("pipeline must be a mapping")
        required_keys = {
            "name",
            "entry_step",
            "behavior_version",
            "max_steps",
            "steps",
        }
        if set(pipeline) != required_keys:
            raise PipelineDefinitionError(
                "pipeline fields must be exactly: " + ", ".join(sorted(required_keys))
            )
        raw_steps = pipeline["steps"]
        if not isinstance(raw_steps, list):
            raise PipelineDefinitionError("pipeline.steps must be a list")
        steps = tuple(YamlPipelineRepository._parse_step(item) for item in raw_steps)
        canonical = json.dumps(pipeline, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        max_steps = pipeline["max_steps"]
        if isinstance(max_steps, bool) or not isinstance(max_steps, int):
            raise PipelineDefinitionError("pipeline.max_steps must be an integer")
        try:
            return PipelineDefinition(
                name=_required_string(pipeline["name"], "pipeline.name"),
                behavior_version=_required_string(
                    pipeline["behavior_version"],
                    "pipeline.behavior_version",
                ),
                entry_step_id=_required_string(
                    pipeline["entry_step"],
                    "pipeline.entry_step",
                ),
                max_steps=max_steps,
                steps=steps,
                checksum=checksum,
            )
        except ValidationError as exc:
            raise PipelineDefinitionError(str(exc)) from exc

    @staticmethod
    def _parse_step(raw: Any) -> PipelineStepDefinition:
        if not isinstance(raw, dict):
            raise PipelineDefinitionError("Each pipeline step must be a mapping")
        if not all(isinstance(key, str) for key in raw):
            raise PipelineDefinitionError("Pipeline step field names must be strings")
        reserved = {"id", "action", "action_version", "next", "routes", "end"}
        required = {"id", "action", "action_version"}
        if not required.issubset(raw):
            missing = ", ".join(sorted(required - set(raw)))
            raise PipelineDefinitionError(f"Missing pipeline step fields: {missing}")
        terminal = raw.get("end", False)
        if not isinstance(terminal, bool):
            raise PipelineDefinitionError("Pipeline step end must be boolean")
        next_step = raw.get("next")
        if next_step is not None and not isinstance(next_step, str):
            raise PipelineDefinitionError("Pipeline step next must be a string")
        routes = raw.get("routes", {})
        if not isinstance(routes, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in routes.items()
        ):
            raise PipelineDefinitionError("Pipeline step routes must map strings to strings")
        try:
            return PipelineStepDefinition(
                step_id=_required_string(raw["id"], "pipeline.steps[].id"),
                action=_required_string(raw["action"], "pipeline.steps[].action"),
                action_version=_required_string(
                    raw["action_version"],
                    "pipeline.steps[].action_version",
                ),
                next_step_id=next_step,
                routes=routes,
                terminal=terminal,
                config={key: value for key, value in raw.items() if key not in reserved},
            )
        except ValidationError as exc:
            raise PipelineDefinitionError(str(exc)) from exc


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PipelineDefinitionError(f"{field_name} must be a non-empty string")
    return value.strip()
