from __future__ import annotations

from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineExecutionError

from ._config import _reject_unknown_config_keys
from ._conversation import _conversation_history_service

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext


class StartConversationTurnAction:
    action_id = "start_conversation_turn"
    behavior_version = "1.0"
    requires = frozenset({"conversation", "conversation_history_service"})
    provides = frozenset({"conversation_turn"})

    def validate_config(self, step) -> None:
        _reject_unknown_config_keys(step, frozenset(), "start_conversation_turn")

    def execute(self, step, context: PipelineContext) -> str | None:
        if context.conversation_id is None:
            raise PipelineExecutionError("Conversation must be resolved before starting a turn")
        turn = _conversation_history_service(context).start_turn(
            context.command.authorization,
            conversation_id=context.conversation_id,
            request_id=context.command.request_id,
            question_text=context.command.query,
        )
        context.conversation_turn_id = turn.turn_id
        return None
