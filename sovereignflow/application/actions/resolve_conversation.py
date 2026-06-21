from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sovereignflow.domain import PipelineDefinitionError, PipelineExecutionError

from ._config import _reject_unknown_config_keys, _required_config_string
from ._conversation import _conversation_history_service, _conversation_title

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import PipelineContext

_RESOLVE_CONVERSATION_ALLOWED_KEYS = frozenset(
    {"conversation_id_source", "create_if_missing", "title_source"}
)
_CONVERSATION_ID_SOURCES = frozenset({"request"})
_CONVERSATION_TITLE_SOURCES = frozenset({"query"})


@dataclass(frozen=True)
class ResolveConversationConfig:
    conversation_id_source: str
    create_if_missing: bool
    title_source: str


def _resolve_conversation_config(step) -> ResolveConversationConfig:
    _reject_unknown_config_keys(
        step,
        _RESOLVE_CONVERSATION_ALLOWED_KEYS,
        "resolve_conversation",
    )
    conversation_id_source = _required_config_string(
        step,
        "conversation_id_source",
        "resolve_conversation",
    )
    if conversation_id_source not in _CONVERSATION_ID_SOURCES:
        raise PipelineDefinitionError("resolve_conversation.conversation_id_source is not allowed")
    create_if_missing = step.config.get("create_if_missing")
    if not isinstance(create_if_missing, bool):
        raise PipelineDefinitionError("resolve_conversation.create_if_missing must be a boolean")
    title_source = _required_config_string(step, "title_source", "resolve_conversation")
    if title_source not in _CONVERSATION_TITLE_SOURCES:
        raise PipelineDefinitionError("resolve_conversation.title_source is not allowed")
    return ResolveConversationConfig(
        conversation_id_source=conversation_id_source,
        create_if_missing=create_if_missing,
        title_source=title_source,
    )


class ResolveConversationAction:
    action_id = "resolve_conversation"
    behavior_version = "1.0"
    requires = frozenset({"command", "domain", "conversation_history_service"})
    provides = frozenset({"conversation"})

    def validate_config(self, step) -> None:
        _resolve_conversation_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        service = _conversation_history_service(context)
        config = _resolve_conversation_config(step)
        if context.command.conversation_id is not None:
            history = service.get(
                context.command.authorization,
                conversation_id=context.command.conversation_id,
                turn_limit=1,
            )
            context.conversation_id = history.conversation.conversation_id
            return None
        if not config.create_if_missing:
            raise PipelineExecutionError("Conversation id is required by the pipeline")
        title = _conversation_title(config.title_source, context)
        conversation = service.create(
            context.command.authorization,
            session_id=context.command.session_id,
            domain=context.domain.name,
            title=title,
        )
        context.conversation_id = conversation.conversation_id
        return None
