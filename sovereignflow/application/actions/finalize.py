from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext


class FinalizeAction:
    action_id = "finalize"
    behavior_version = "1.0"
    requires = frozenset({"answer", "citations"})
    provides = frozenset({"result"})

    def execute(self, step, context: PipelineContext) -> str | None:
        if context.domain.disclaimer:
            context.answer = f"{context.answer}\n\n---\n\n{context.domain.disclaimer}".strip()
        return None
