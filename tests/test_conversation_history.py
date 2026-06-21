from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from conftest import StubAuthenticator, StubOperations, authorization_context

from sovereignflow.application import ConversationHistoryService, subject_hash
from sovereignflow.domain import (
    Conversation,
    ConversationStatus,
    ConversationTurn,
    ConversationTurnStatus,
    DomainNotFoundError,
    ValidationError,
)
from sovereignflow.interfaces import QueryDispatcher, create_app


class MemoryConversationRepository:
    def __init__(self) -> None:
        self.conversations: dict[str, Conversation] = {}
        self.turns: dict[str, list[ConversationTurn]] = {}

    def create_conversation(self, conversation: Conversation) -> Conversation:
        self.conversations[conversation.conversation_id] = conversation
        self.turns[conversation.conversation_id] = []
        return conversation

    def list_conversations(self, *, tenant_id: str, subject_hash: str, limit: int):
        return tuple(
            conversation
            for conversation in self.conversations.values()
            if conversation.tenant_id == tenant_id
            and conversation.subject_hash == subject_hash
            and conversation.status == ConversationStatus.ACTIVE
        )[:limit]

    def get_conversation(self, *, conversation_id: str, tenant_id: str, subject_hash: str):
        conversation = self.conversations.get(conversation_id)
        if (
            conversation is None
            or conversation.tenant_id != tenant_id
            or conversation.subject_hash != subject_hash
            or conversation.status != ConversationStatus.ACTIVE
        ):
            return None
        return conversation

    def rename_conversation(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
        title: str,
    ):
        conversation = self.get_conversation(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            subject_hash=subject_hash,
        )
        if conversation is None:
            return None
        renamed = replace(conversation, title=title, updated_at="2026-01-01T00:00:01+00:00")
        self.conversations[conversation_id] = renamed
        return renamed

    def delete_conversation(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
    ) -> bool:
        conversation = self.get_conversation(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            subject_hash=subject_hash,
        )
        if conversation is None:
            return False
        self.conversations[conversation_id] = replace(
            conversation,
            status=ConversationStatus.DELETED,
            deleted_at="2026-01-01T00:00:02+00:00",
        )
        return True

    def start_turn(self, turn: ConversationTurn) -> ConversationTurn:
        sequence_number = len(self.turns[turn.conversation_id]) + 1
        stored = replace(turn, sequence_number=sequence_number)
        self.turns[turn.conversation_id].append(stored)
        return stored

    def finalize_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        tenant_id: str,
        subject_hash: str,
        answer_text: str,
        metadata,
    ):
        if (
            self.get_conversation(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                subject_hash=subject_hash,
            )
            is None
        ):
            return None
        return self._replace_turn(
            conversation_id,
            turn_id,
            status=ConversationTurnStatus.FINALIZED,
            answer_text=answer_text,
            finalized_at="2026-01-01T00:00:03+00:00",
            metadata=metadata,
        )

    def fail_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        tenant_id: str,
        subject_hash: str,
        error_code: str,
        metadata,
    ):
        if (
            self.get_conversation(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                subject_hash=subject_hash,
            )
            is None
        ):
            return None
        return self._replace_turn(
            conversation_id,
            turn_id,
            status=ConversationTurnStatus.FAILED,
            error_code=error_code,
            finalized_at="2026-01-01T00:00:04+00:00",
            metadata=metadata,
        )

    def list_turns(self, *, conversation_id: str, tenant_id: str, subject_hash: str, limit: int):
        if (
            self.get_conversation(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                subject_hash=subject_hash,
            )
            is None
        ):
            return ()
        return tuple(self.turns[conversation_id][:limit])

    def recent_finalized_turns(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
        limit: int,
    ):
        if (
            self.get_conversation(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
                subject_hash=subject_hash,
            )
            is None
        ):
            return ()
        finalized = [
            turn
            for turn in self.turns[conversation_id]
            if turn.status == ConversationTurnStatus.FINALIZED
        ]
        return tuple(finalized[-limit:])

    def _replace_turn(self, conversation_id: str, turn_id: str, **changes):
        for index, turn in enumerate(self.turns[conversation_id]):
            if turn.turn_id == turn_id and turn.status == ConversationTurnStatus.STARTED:
                stored = replace(turn, **changes)
                self.turns[conversation_id][index] = stored
                return stored
        return None


def service(repository: MemoryConversationRepository | None = None) -> ConversationHistoryService:
    ids = iter(("conversation-1", "turn-1", "turn-2"))
    return ConversationHistoryService(
        repository or MemoryConversationRepository(),
        id_factory=lambda: next(ids),
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_conversation_models_validate_lifecycle_invariants() -> None:
    with pytest.raises(ValidationError, match="Active conversation"):
        Conversation(
            "conversation-1",
            "tenant",
            "a" * 64,
            "session",
            "domain",
            "title",
            ConversationStatus.ACTIVE,
            "created",
            "updated",
            "deleted",
        )
    with pytest.raises(ValidationError, match="deleted_at"):
        Conversation(
            "conversation-1",
            "tenant",
            "a" * 64,
            "session",
            "domain",
            "title",
            ConversationStatus.DELETED,
            "created",
            "updated",
        )
    with pytest.raises(ValidationError, match="requires answer"):
        ConversationTurn(
            "turn-1",
            "conversation-1",
            "request-1",
            1,
            "question",
            ConversationTurnStatus.FINALIZED,
            "created",
        )
    with pytest.raises(ValidationError, match="cannot be finalized"):
        ConversationTurn(
            "turn-1",
            "conversation-1",
            "request-1",
            1,
            "question",
            ConversationTurnStatus.STARTED,
            "created",
            answer_text="answer",
        )
    with pytest.raises(ValidationError, match="cannot define error_code"):
        ConversationTurn(
            "turn-1",
            "conversation-1",
            "request-1",
            1,
            "question",
            ConversationTurnStatus.STARTED,
            "created",
            error_code="error",
        )
    with pytest.raises(ValidationError, match="cannot define error_code"):
        ConversationTurn(
            "turn-1",
            "conversation-1",
            "request-1",
            1,
            "question",
            ConversationTurnStatus.FINALIZED,
            "created",
            answer_text="answer",
            finalized_at="done",
            error_code="error",
        )
    with pytest.raises(ValidationError, match="cannot define answer"):
        ConversationTurn(
            "turn-1",
            "conversation-1",
            "request-1",
            1,
            "question",
            ConversationTurnStatus.FAILED,
            "created",
            answer_text="answer",
            finalized_at="done",
            error_code="error",
        )
    with pytest.raises(ValidationError, match="requires finalized_at"):
        ConversationTurn(
            "turn-1",
            "conversation-1",
            "request-1",
            1,
            "question",
            ConversationTurnStatus.DISCARDED,
            "created",
        )
    with pytest.raises(ValidationError, match="requires error_code"):
        ConversationTurn(
            "turn-1",
            "conversation-1",
            "request-1",
            1,
            "question",
            ConversationTurnStatus.FAILED,
            "created",
            finalized_at="done",
        )
    with pytest.raises(ValidationError, match="positive"):
        ConversationTurn(
            "turn-1",
            "conversation-1",
            "request-1",
            0,
            "question",
            ConversationTurnStatus.STARTED,
            "created",
        )


def test_service_scopes_conversations_to_authenticated_subject_and_tenant() -> None:
    repository = MemoryConversationRepository()
    history = service(repository)
    owner = authorization_context(subject="alice", tenant_id="tenant-a")
    foreign_user = authorization_context(subject="bob", tenant_id="tenant-a")
    foreign_tenant = authorization_context(subject="alice", tenant_id="tenant-b")

    conversation = history.create(owner, session_id="session", domain="general", title="Title")
    turn = history.start_turn(
        owner,
        conversation_id=conversation.conversation_id,
        request_id="request-1",
        question_text="Question?",
    )
    finalized = history.finalize_turn(
        owner,
        conversation_id=conversation.conversation_id,
        turn_id=turn.turn_id,
        answer_text="Answer.",
        metadata={"citations": 1},
    )

    assert conversation.subject_hash == subject_hash(owner)
    assert conversation.tenant_id == "tenant-a"
    assert finalized.sequence_number == 1
    assert finalized.metadata["citations"] == 1
    loaded = history.get(owner, conversation_id=conversation.conversation_id, turn_limit=10)
    recent = history.recent_finalized_turns(
        owner,
        conversation_id=conversation.conversation_id,
        limit=5,
    )
    assert loaded.turns == (finalized,)
    assert history.list(owner, limit=10) == (conversation,)
    assert recent == (finalized,)
    with pytest.raises(DomainNotFoundError):
        history.get(foreign_user, conversation_id=conversation.conversation_id, turn_limit=10)
    with pytest.raises(DomainNotFoundError):
        history.rename(foreign_tenant, conversation_id=conversation.conversation_id, title="X")


def test_service_rename_delete_and_failure_paths() -> None:
    repository = MemoryConversationRepository()
    history = service(repository)
    owner = authorization_context(subject="alice", tenant_id="tenant-a")
    conversation = history.create(owner, session_id="session", domain="general", title="Title")
    turn = history.start_turn(
        owner,
        conversation_id=conversation.conversation_id,
        request_id="r",
        question_text="Q",
    )

    renamed = history.rename(owner, conversation_id=conversation.conversation_id, title="Renamed")
    failed = history.fail_turn(
        owner,
        conversation_id=conversation.conversation_id,
        turn_id=turn.turn_id,
        error_code="provider_error",
    )
    history.delete(owner, conversation_id=conversation.conversation_id)

    assert renamed.title == "Renamed"
    assert failed.status == ConversationTurnStatus.FAILED
    with pytest.raises(DomainNotFoundError):
        history.get(owner, conversation_id=conversation.conversation_id, turn_limit=10)
    with pytest.raises(DomainNotFoundError):
        history.delete(owner, conversation_id=conversation.conversation_id)
    with pytest.raises(ValidationError, match="limit"):
        history.list(owner, limit=0)
    with pytest.raises(ValidationError, match="limit"):
        history.recent_finalized_turns(owner, conversation_id=conversation.conversation_id, limit=0)
    with pytest.raises(DomainNotFoundError, match="Conversation was not found"):
        history.start_turn(
            owner,
            conversation_id="missing",
            request_id="r2",
            question_text="Q",
        )


def test_service_reports_missing_turn_updates() -> None:
    repository = MemoryConversationRepository()
    history = service(repository)
    owner = authorization_context(subject="alice", tenant_id="tenant-a")
    conversation = history.create(owner, session_id="session", domain="general", title="Title")

    with pytest.raises(DomainNotFoundError, match="Conversation turn"):
        history.finalize_turn(
            owner,
            conversation_id=conversation.conversation_id,
            turn_id="missing",
            answer_text="Answer.",
        )
    with pytest.raises(DomainNotFoundError, match="Conversation turn"):
        history.fail_turn(
            owner,
            conversation_id=conversation.conversation_id,
            turn_id="missing",
            error_code="provider_error",
        )


def test_conversation_http_api_uses_bearer_identity_and_rejects_security_body_fields() -> None:
    repository = MemoryConversationRepository()
    app = create_app(
        QueryDispatcher({}),
        (),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(authorization_context(subject="alice", tenant_id="tenant-a")),
        conversation_history=service(repository),
    )
    client = app.test_client()

    create_response = client.post(
        "/v1/conversations",
        headers={"Authorization": "Bearer token"},
        json={"session_id": "session", "domain": "general", "title": "Title"},
    )
    blocked_response = client.post(
        "/v1/conversations",
        headers={"Authorization": "Bearer token"},
        json={"session_id": "session", "domain": "general", "title": "Title", "tenant_id": "x"},
    )
    conversation_id = create_response.get_json()["conversation"]["conversation_id"]
    list_response = client.get("/v1/conversations", headers={"Authorization": "Bearer token"})
    read_response = client.get(
        f"/v1/conversations/{conversation_id}",
        headers={"Authorization": "Bearer token"},
    )
    turns_response = client.get(
        f"/v1/conversations/{conversation_id}/turns?limit=1",
        headers={"Authorization": "Bearer token"},
    )
    rename_response = client.patch(
        f"/v1/conversations/{conversation_id}",
        headers={"Authorization": "Bearer token"},
        json={"title": "Renamed"},
    )
    delete_response = client.delete(
        f"/v1/conversations/{conversation_id}",
        headers={"Authorization": "Bearer token"},
    )

    assert create_response.status_code == 201
    assert create_response.get_json()["conversation"]["title"] == "Title"
    assert "subject_hash" not in create_response.get_json()["conversation"]
    assert blocked_response.status_code == 400
    assert list_response.get_json()["conversations"][0]["conversation_id"] == conversation_id
    assert read_response.get_json()["turns"] == []
    assert turns_response.get_json()["turns"] == []
    assert rename_response.get_json()["conversation"]["title"] == "Renamed"
    assert delete_response.status_code == 200


def test_conversation_http_api_validates_json_and_query_limits() -> None:
    repository = MemoryConversationRepository()
    app = create_app(
        QueryDispatcher({}),
        (),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(authorization_context(subject="alice", tenant_id="tenant-a")),
        conversation_history=service(repository),
    )
    client = app.test_client()
    created = client.post(
        "/v1/conversations",
        headers={"Authorization": "Bearer token"},
        json={"session_id": "session", "domain": "general", "title": "Title"},
    ).get_json()["conversation"]

    invalid_create = client.post(
        "/v1/conversations",
        headers={"Authorization": "Bearer token"},
    )
    assert invalid_create.status_code == 400
    assert (
        client.patch(
            f"/v1/conversations/{created['conversation_id']}",
            headers={"Authorization": "Bearer token"},
        ).status_code
        == 400
    )
    assert (
        client.get(
            "/v1/conversations?limit=zero",
            headers={"Authorization": "Bearer token"},
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/v1/conversations/{created['conversation_id']}/turns?limit=0",
            headers={"Authorization": "Bearer token"},
        ).status_code
        == 400
    )


def test_conversation_http_api_serializes_turns_from_postgresql_backed_service() -> None:
    repository = MemoryConversationRepository()
    owner = authorization_context(subject="alice", tenant_id="tenant-a")
    history = service(repository)
    conversation = history.create(owner, session_id="session", domain="general", title="Title")
    turn = history.start_turn(
        owner,
        conversation_id=conversation.conversation_id,
        request_id="request-1",
        question_text="Question?",
    )
    history.finalize_turn(
        owner,
        conversation_id=conversation.conversation_id,
        turn_id=turn.turn_id,
        answer_text="Answer.",
        metadata={"citations": 1},
    )
    app = create_app(
        QueryDispatcher({}),
        (),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(owner),
        conversation_history=history,
    )

    response = app.test_client().get(
        f"/v1/conversations/{conversation.conversation_id}/turns",
        headers={"Authorization": "Bearer token"},
    )

    assert response.get_json()["turns"][0]["answer"] == "Answer."
    assert response.get_json()["turns"][0]["metadata"] == {"citations": 1}
