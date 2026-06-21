from __future__ import annotations

from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineExecutionError

from ._config import _reject_unknown_config_keys
from ._conversation import _conversation_history_service

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext


class FinalizeConversationTurnAction:
    action_id = "finalize_conversation_turn"
    behavior_version = "1.0"
    requires = frozenset({"conversation_turn", "answer", "conversation_history_service"})
    provides = frozenset()

    def validate_config(self, step) -> None:
        _reject_unknown_config_keys(step, frozenset(), "finalize_conversation_turn")

    def execute(self, step, context: PipelineContext) -> str | None:
        if context.conversation_id is None or context.conversation_turn_id is None:
            raise PipelineExecutionError("Conversation turn must be started before finalization")
        if not context.answer:
            raise PipelineExecutionError("Conversation turn finalization requires answer")
        turn = _conversation_history_service(context).finalize_turn(
            context.command.authorization,
            conversation_id=context.conversation_id,
            turn_id=context.conversation_turn_id,
            answer_text=context.answer,
            metadata={
                "request_id": context.command.request_id,
                "pipeline_trace": tuple(context.trace),
                "model_server_id": context.model_transmission_final_server_id,
                "citation_count": len(context.citations),
            },
        )
        context.conversation_turn_id = turn.turn_id
        context.conversation_turn_finalized = True
        return None
