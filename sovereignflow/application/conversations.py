from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sovereignflow.domain import (
    AuthorizationContext,
    Conversation,
    ConversationHistory,
    ConversationStatus,
    ConversationTurn,
    ConversationTurnStatus,
    DomainNotFoundError,
    ValidationError,
)

from .ports import ConversationHistoryPort

IdFactory = Callable[[], str]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ConversationHistoryService:
    repository: ConversationHistoryPort
    id_factory: IdFactory = lambda: str(uuid.uuid4())
    clock: Clock = lambda: datetime.now(UTC)

    def create(
        self,
        authorization: AuthorizationContext,
        *,
        session_id: str,
        domain: str,
        title: str,
    ) -> Conversation:
        conversation = Conversation(
            conversation_id=self.id_factory(),
            tenant_id=authorization.tenant_id,
            subject_hash=subject_hash(authorization),
            session_id=session_id,
            domain=domain,
            title=title,
            status=ConversationStatus.ACTIVE,
            created_at=self._now(),
            updated_at=self._now(),
        )
        return self.repository.create_conversation(conversation)

    def list(
        self,
        authorization: AuthorizationContext,
        *,
        limit: int,
    ) -> tuple[Conversation, ...]:
        _positive_limit(limit, "limit")
        return tuple(
            self.repository.list_conversations(
                tenant_id=authorization.tenant_id,
                subject_hash=subject_hash(authorization),
                limit=limit,
            )
        )

    def get(
        self,
        authorization: AuthorizationContext,
        *,
        conversation_id: str,
        turn_limit: int,
    ) -> ConversationHistory:
        _positive_limit(turn_limit, "turn_limit")
        conversation = self.repository.get_conversation(
            conversation_id=conversation_id,
            tenant_id=authorization.tenant_id,
            subject_hash=subject_hash(authorization),
        )
        if conversation is None:
            raise DomainNotFoundError("Conversation was not found")
        turns = self.repository.list_turns(
            conversation_id=conversation.conversation_id,
            tenant_id=authorization.tenant_id,
            subject_hash=subject_hash(authorization),
            limit=turn_limit,
        )
        return ConversationHistory(conversation=conversation, turns=tuple(turns))

    def rename(
        self,
        authorization: AuthorizationContext,
        *,
        conversation_id: str,
        title: str,
    ) -> Conversation:
        renamed = self.repository.rename_conversation(
            conversation_id=conversation_id,
            tenant_id=authorization.tenant_id,
            subject_hash=subject_hash(authorization),
            title=title,
        )
        if renamed is None:
            raise DomainNotFoundError("Conversation was not found")
        return renamed

    def delete(
        self,
        authorization: AuthorizationContext,
        *,
        conversation_id: str,
    ) -> None:
        deleted = self.repository.delete_conversation(
            conversation_id=conversation_id,
            tenant_id=authorization.tenant_id,
            subject_hash=subject_hash(authorization),
        )
        if not deleted:
            raise DomainNotFoundError("Conversation was not found")

    def start_turn(
        self,
        authorization: AuthorizationContext,
        *,
        conversation_id: str,
        request_id: str,
        question_text: str,
    ) -> ConversationTurn:
        self._require_conversation(authorization, conversation_id)
        turn = ConversationTurn(
            turn_id=self.id_factory(),
            conversation_id=conversation_id,
            request_id=request_id,
            sequence_number=1,
            question_text=question_text,
            status=ConversationTurnStatus.STARTED,
            created_at=self._now(),
        )
        return self.repository.start_turn(turn)

    def finalize_turn(
        self,
        authorization: AuthorizationContext,
        *,
        conversation_id: str,
        turn_id: str,
        answer_text: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ConversationTurn:
        finalized = self.repository.finalize_turn(
            conversation_id=conversation_id,
            turn_id=turn_id,
            tenant_id=authorization.tenant_id,
            subject_hash=subject_hash(authorization),
            answer_text=answer_text,
            metadata=dict(metadata or {}),
        )
        if finalized is None:
            raise DomainNotFoundError("Conversation turn was not found")
        return finalized

    def fail_turn(
        self,
        authorization: AuthorizationContext,
        *,
        conversation_id: str,
        turn_id: str,
        error_code: str,
        metadata: Mapping[str, object] | None = None,
    ) -> ConversationTurn:
        failed = self.repository.fail_turn(
            conversation_id=conversation_id,
            turn_id=turn_id,
            tenant_id=authorization.tenant_id,
            subject_hash=subject_hash(authorization),
            error_code=error_code,
            metadata=dict(metadata or {}),
        )
        if failed is None:
            raise DomainNotFoundError("Conversation turn was not found")
        return failed

    def recent_finalized_turns(
        self,
        authorization: AuthorizationContext,
        *,
        conversation_id: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]:
        _positive_limit(limit, "limit")
        return tuple(
            self.repository.recent_finalized_turns(
                conversation_id=conversation_id,
                tenant_id=authorization.tenant_id,
                subject_hash=subject_hash(authorization),
                limit=limit,
            )
        )

    def _require_conversation(
        self,
        authorization: AuthorizationContext,
        conversation_id: str,
    ) -> Conversation:
        conversation = self.repository.get_conversation(
            conversation_id=conversation_id,
            tenant_id=authorization.tenant_id,
            subject_hash=subject_hash(authorization),
        )
        if conversation is None:
            raise DomainNotFoundError("Conversation was not found")
        return conversation

    def _now(self) -> str:
        return self.clock().astimezone(UTC).isoformat()


def subject_hash(authorization: AuthorizationContext) -> str:
    return hashlib.sha256(authorization.subject.encode("utf-8")).hexdigest()


def _positive_limit(value: int, name: str) -> None:
    if isinstance(value, bool) or value < 1:
        raise ValidationError(f"{name} must be a positive integer")
