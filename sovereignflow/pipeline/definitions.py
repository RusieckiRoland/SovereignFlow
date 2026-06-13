from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class StepDefinition:
    id: str
    action: str
    next: str | None = None
    end: bool = False
    options: dict[str, Any] | None = None


@dataclass(frozen=True)
class PipelineDefinition:
    name: str
    entry_step: str
    behavior_version: int
    steps: tuple[StepDefinition, ...]

    def steps_by_id(self) -> dict[str, StepDefinition]:
        return {step.id: step for step in self.steps}

    def validate(self) -> None:
        if not self.name:
            raise ValueError("pipeline.name is required")
        if not self.entry_step:
            raise ValueError("pipeline.entry_step is required")
        if self.behavior_version < 1:
            raise ValueError("pipeline.behavior_version must be greater than zero")

        steps = self.steps_by_id()
        if len(steps) != len(self.steps):
            raise ValueError("Pipeline step identifiers must be unique")
        if self.entry_step not in steps:
            raise ValueError(f"Unknown entry step: {self.entry_step}")

        for step in self.steps:
            if not step.id or not step.action:
                raise ValueError("Every pipeline step requires id and action")
            if not step.end and step.next and step.next not in steps:
                raise ValueError(f"Step '{step.id}' references unknown next step '{step.next}'")


class PipelineLoader:
    @staticmethod
    def load(path: str | Path) -> PipelineDefinition:
        pipeline_path = Path(path)
        raw = yaml.safe_load(pipeline_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("pipeline"), dict):
            raise ValueError(f"Missing pipeline mapping in {pipeline_path}")

        data = raw["pipeline"]
        raw_steps = data.get("steps") or []
        if not isinstance(raw_steps, list):
            raise ValueError("pipeline.steps must be a list")

        steps: list[StepDefinition] = []
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise ValueError("Every pipeline step must be a mapping")
            known = {"id", "action", "next", "end"}
            steps.append(
                StepDefinition(
                    id=str(raw_step.get("id") or "").strip(),
                    action=str(raw_step.get("action") or "").strip(),
                    next=str(raw_step.get("next") or "").strip() or None,
                    end=bool(raw_step.get("end") is True),
                    options={key: value for key, value in raw_step.items() if key not in known},
                )
            )

        definition = PipelineDefinition(
            name=str(data.get("name") or "").strip(),
            entry_step=str(data.get("entry_step") or "").strip(),
            behavior_version=int(data.get("behavior_version") or 1),
            steps=tuple(steps),
        )
        definition.validate()
        return definition

