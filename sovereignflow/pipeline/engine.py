from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain import DomainProfile
from ..ports import ModelClient, RetrievalBackend
from .definitions import PipelineDefinition, StepDefinition
from .state import PipelineState


@dataclass(frozen=True)
class PipelineRuntime:
    domain: DomainProfile
    retrieval: RetrievalBackend
    model: ModelClient


class PipelineAction(Protocol):
    def execute(
        self,
        step: StepDefinition,
        state: PipelineState,
        runtime: PipelineRuntime,
    ) -> str | None:
        ...


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, PipelineAction] = {}

    def register(self, name: str, action: PipelineAction) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Action name cannot be empty")
        self._actions[normalized] = action

    def get(self, name: str) -> PipelineAction:
        try:
            return self._actions[name]
        except KeyError as exc:
            raise KeyError(f"Unknown pipeline action: {name}") from exc


class PipelineEngine:
    def __init__(self, registry: ActionRegistry) -> None:
        self._registry = registry

    def run(
        self,
        definition: PipelineDefinition,
        state: PipelineState,
        runtime: PipelineRuntime,
    ) -> PipelineState:
        steps = definition.steps_by_id()
        current = definition.entry_step
        visited = 0
        max_steps = max(16, len(steps) * 4)

        while current:
            visited += 1
            if visited > max_steps:
                raise RuntimeError("Pipeline exceeded its execution guard")

            step = steps[current]
            state.trace.append(step.id)
            next_override = self._registry.get(step.action).execute(step, state, runtime)

            if step.end:
                break
            current = next_override or step.next or ""

        return state

