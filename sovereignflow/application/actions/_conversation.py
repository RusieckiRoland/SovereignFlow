from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sovereignflow.domain import ConversationTurn, PipelineExecutionError

from sovereignflow.application.ports import ConversationHistoryPort

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext


def _conversation_history_service(context: PipelineContext) -> ConversationHistoryPort:
    if context.conversation_history is None:
        raise PipelineExecutionError("Conversation history service is not configured")
    return context.conversation_history


def _fail_conversation_turn(context: PipelineContext, error_code: str) -> None:
    if context.conversation_id is None or context.conversation_turn_id is None:
        raise PipelineExecutionError("Conversation turn must be started before marking failure")
    _conversation_history_service(context).fail_turn(
        context.command.authorization,
        conversation_id=context.conversation_id,
        turn_id=context.conversation_turn_id,
        error_code=error_code,
        metadata={"request_id": context.command.request_id},
    )
    context.conversation_turn_finalized = True


def _conversation_title(source: str, context: PipelineContext) -> str:
    if source == "query":
        title = " ".join(context.command.query.split())
        return title[:80] or "Untitled conversation"
    raise PipelineExecutionError(f"Unsupported conversation title source '{source}'")


def _format_conversation_history(
    turns: Sequence[ConversationTurn],
    *,
    max_characters: int,
) -> str:
    selected: list[str] = []
    used = 0
    for turn in reversed(turns):
        if turn.answer_text is None:
            continue
        rendered = f"User: {turn.question_text}\nAssistant: {turn.answer_text}\n\n"
        length = len(rendered)
        if length > max_characters:
            continue
        if used + length > max_characters:
            break
        selected.append(rendered)
        used += length
    return "".join(reversed(selected)).strip()
