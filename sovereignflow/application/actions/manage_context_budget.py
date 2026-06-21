from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineDefinitionError

from ._config import _positive_config_integer, _reject_unknown_config_keys, _required_config_string
from ._retrieval import _build_context

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_CONTEXT_BUDGET_ALLOWED_KEYS = frozenset({"source", "target", "max_context_characters"})


@dataclass(frozen=True)
class ContextBudgetConfig:
    max_context_characters: int


def _context_budget_config(step) -> ContextBudgetConfig:
    _reject_unknown_config_keys(step, _CONTEXT_BUDGET_ALLOWED_KEYS, "manage_context_budget")
    source = _required_config_string(step, "source", "manage_context_budget")
    target = _required_config_string(step, "target", "manage_context_budget")
    if source != "hits":
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' manage_context_budget.source must be 'hits'"
        )
    if target != "evidence":
        raise PipelineDefinitionError(
            f"Step '{step.step_id}' manage_context_budget.target must be 'evidence'"
        )
    return ContextBudgetConfig(
        max_context_characters=_positive_config_integer(
            step,
            "max_context_characters",
            "manage_context_budget",
        )
    )


class ManageContextBudgetAction:
    action_id = "manage_context_budget"
    behavior_version = "1.0"
    requires = frozenset({"hits"})
    provides = frozenset({"evidence", "citations"})

    def validate_config(self, step) -> None:
        _context_budget_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        config = _context_budget_config(step)
        (
            context.evidence,
            context.citations,
            context.context_chunk_ids,
            context.omitted_chunk_ids,
        ) = _build_context(context.hits, config.max_context_characters)
        return None
