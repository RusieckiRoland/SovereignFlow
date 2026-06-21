from __future__ import annotations

from typing import TYPE_CHECKING

from ._config import _reject_unknown_config_keys
from ._conversation import _fail_conversation_turn

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext


class FailConversationTurnAction:
    action_id = "fail_conversation_turn"
    behavior_version = "1.0"
    requires = frozenset({"conversation_turn", "conversation_history_service"})
    provides = frozenset()

    def validate_config(self, step) -> None:
        _reject_unknown_config_keys(step, frozenset(), "fail_conversation_turn")

    def execute(self, step, context: PipelineContext) -> str | None:
        _fail_conversation_turn(context, "pipeline_marked_failed")
        return None
