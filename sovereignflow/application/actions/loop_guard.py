from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineDefinitionError

from ._config import _positive_config_integer, _reject_unknown_config_keys, _required_config_string

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_LOOP_GUARD_ALLOWED_KEYS = frozenset({"max_loops", "on_allow", "on_deny"})


@dataclass(frozen=True)
class LoopGuardConfig:
    max_loops: int
    on_allow: str
    on_deny: str


def _loop_guard_config(step) -> LoopGuardConfig:
    _reject_unknown_config_keys(step, _LOOP_GUARD_ALLOWED_KEYS, "loop_guard")
    on_allow = _required_config_string(step, "on_allow", "loop_guard")
    on_deny = _required_config_string(step, "on_deny", "loop_guard")
    for route_name in (on_allow, on_deny):
        if route_name not in step.routes:
            raise PipelineDefinitionError("loop_guard route is not declared in routes")
    return LoopGuardConfig(
        max_loops=_positive_config_integer(step, "max_loops", "loop_guard"),
        on_allow=on_allow,
        on_deny=on_deny,
    )


class LoopGuardAction:
    action_id = "loop_guard"
    behavior_version = "1.0"
    requires = frozenset()
    provides = frozenset()

    def validate_config(self, step) -> None:
        _loop_guard_config(step)

    def execute(self, step, context: PipelineContext) -> str:
        config = _loop_guard_config(step)
        current = context.loop_counters.get(step.step_id, 0) + 1
        context.loop_counters[step.step_id] = current
        route = config.on_allow if current <= config.max_loops else config.on_deny
        context.last_route = route
        return route
