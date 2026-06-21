from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineDefinitionError, PipelineExecutionError

from ._config import _positive_config_integer, _reject_unknown_config_keys, _required_config_string
from ._conversation import _conversation_history_service, _format_conversation_history

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_LOAD_CONVERSATION_HISTORY_ALLOWED_KEYS = frozenset(
    {"limit", "max_characters", "include_failed", "format"}
)
_CONVERSATION_HISTORY_FORMATS = frozenset({"dialog"})


@dataclass(frozen=True)
class LoadConversationHistoryConfig:
    limit: int
    max_characters: int
    include_failed: bool
    format: str


def _load_conversation_history_config(step) -> LoadConversationHistoryConfig:
    _reject_unknown_config_keys(
        step,
        _LOAD_CONVERSATION_HISTORY_ALLOWED_KEYS,
        "load_conversation_history",
    )
    include_failed = step.config.get("include_failed", False)
    if not isinstance(include_failed, bool):
        raise PipelineDefinitionError("load_conversation_history.include_failed must be a boolean")
    if include_failed:
        raise PipelineDefinitionError("load_conversation_history.include_failed cannot be true")
    history_format = _required_config_string(step, "format", "load_conversation_history")
    if history_format not in _CONVERSATION_HISTORY_FORMATS:
        raise PipelineDefinitionError("load_conversation_history.format is not allowed")
    return LoadConversationHistoryConfig(
        limit=_positive_config_integer(step, "limit", "load_conversation_history"),
        max_characters=_positive_config_integer(
            step,
            "max_characters",
            "load_conversation_history",
        ),
        include_failed=include_failed,
        format=history_format,
    )


class LoadConversationHistoryAction:
    action_id = "load_conversation_history"
    behavior_version = "1.0"
    requires = frozenset({"conversation", "conversation_history_service"})
    provides = frozenset({"conversation_history"})

    def validate_config(self, step) -> None:
        _load_conversation_history_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        if context.conversation_id is None:
            raise PipelineExecutionError("Conversation must be resolved before loading history")
        config = _load_conversation_history_config(step)
        turns = _conversation_history_service(context).recent_finalized_turns(
            context.command.authorization,
            conversation_id=context.conversation_id,
            limit=config.limit,
        )
        context.conversation_history_turns = turns
        context.conversation_history_text = _format_conversation_history(
            turns,
            max_characters=config.max_characters,
        )
        return None
