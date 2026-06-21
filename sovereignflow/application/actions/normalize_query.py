from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext


class NormalizeQueryAction:
    action_id = "normalize_query"
    behavior_version = "1.0"
    requires = frozenset({"command"})
    provides = frozenset({"normalized_query"})

    def execute(self, step, context: PipelineContext) -> str | None:
        context.normalized_query = " ".join(context.command.query.split())
        return None
