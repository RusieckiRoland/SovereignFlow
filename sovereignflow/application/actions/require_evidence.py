from __future__ import annotations

from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineExecutionError

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext


class RequireEvidenceAction:
    action_id = "require_evidence"
    behavior_version = "1.0"
    requires = frozenset({"evidence", "citations"})
    provides = frozenset()

    def execute(self, step, context: PipelineContext) -> str | None:
        if not context.evidence or not context.citations:
            raise PipelineExecutionError("The pipeline requires retrieved evidence")
        return None
