from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sovereignflow.domain import (
    Conversation,
    ConversationStatus,
    ConversationTurn,
    ConversationTurnStatus,
    DependencyUnavailableError,
)
from sovereignflow.infrastructure import PostgreSQLConversationHistory
from sovereignflow.infrastructure import conversations as conversations_module


class Cursor:
    def __init__(self, *, one=None, all_rows=None, error: Exception | None = None) -> None:
        self.one = list(one or [])
        self.all_rows = list(all_rows or [])
        self.error = error
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, statement, parameters=None) -> None:
        if self.error:
            raise self.error
        self.executed.append((str(statement), parameters))

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return self.all_rows.pop(0) if self.all_rows else []


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self._cursor = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def cursor(self) -> Cursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1


def module_for(connection: Connection):
    return SimpleNamespace(connect=lambda *args, **kwargs: connection)


def repository() -> PostgreSQLConversationHistory:
    return PostgreSQLConversationHistory("postgresql://test", timeout_seconds=3)


def timestamp() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def conversation_row(status: str = "active", deleted_at=None):
    return (
        "00000000-0000-0000-0000-000000000001",
        "tenant-a",
        "a" * 64,
        "session",
        "general",
        "Title",
        status,
        timestamp(),
        timestamp(),
        deleted_at,
    )


def turn_row(
    status: str = "started",
    answer=None,
    finalized_at=None,
    error_code=None,
    metadata=None,
):
    return (
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000001",
        "request-1",
        1,
        "Question?",
        answer,
        status,
        error_code,
        timestamp(),
        finalized_at,
        metadata or {},
    )


def conversation() -> Conversation:
    return Conversation(
        "00000000-0000-0000-0000-000000000001",
        "tenant-a",
        "a" * 64,
        "session",
        "general",
        "Title",
        ConversationStatus.ACTIVE,
        timestamp().isoformat(),
        timestamp().isoformat(),
    )


def started_turn() -> ConversationTurn:
    return ConversationTurn(
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000001",
        "request-1",
        1,
        "Question?",
        ConversationTurnStatus.STARTED,
        timestamp().isoformat(),
    )


def test_repository_health_and_conversation_crud(monkeypatch) -> None:
    cursor = Cursor(one=[(1,), conversation_row(), conversation_row(), conversation_row(), ("id",)])
    connection = Connection(cursor)
    monkeypatch.setattr(
        conversations_module,
        "psycopg_module",
        lambda: module_for(connection),
    )
    repo = repository()

    assert repo.name == "conversation_history"
    repo.check()
    created = repo.create_conversation(conversation())
    loaded = repo.get_conversation(
        conversation_id=created.conversation_id,
        tenant_id="tenant-a",
        subject_hash="a" * 64,
    )
    renamed = repo.rename_conversation(
        conversation_id=created.conversation_id,
        tenant_id="tenant-a",
        subject_hash="a" * 64,
        title="Renamed",
    )
    deleted = repo.delete_conversation(
        conversation_id=created.conversation_id,
        tenant_id="tenant-a",
        subject_hash="a" * 64,
    )

    assert created.conversation_id == conversation().conversation_id
    assert loaded is not None
    assert renamed is not None
    assert deleted is True
    assert cursor.executed[2][1] == (created.conversation_id, "tenant-a", "a" * 64)
    assert cursor.executed[3][1][0] == "Renamed"
    assert connection.commits == 2


def test_repository_lists_conversations_and_turns(monkeypatch) -> None:
    cursor = Cursor(
        all_rows=[
            [conversation_row()],
            [turn_row()],
            [turn_row("finalized", "Answer.", timestamp(), None, '{"ok": true}')],
        ]
    )
    monkeypatch.setattr(
        conversations_module,
        "psycopg_module",
        lambda: module_for(Connection(cursor)),
    )
    repo = repository()

    conversations = repo.list_conversations(tenant_id="tenant-a", subject_hash="a" * 64, limit=10)
    turns = repo.list_turns(
        conversation_id=conversation().conversation_id,
        tenant_id="tenant-a",
        subject_hash="a" * 64,
        limit=10,
    )
    recent = repo.recent_finalized_turns(
        conversation_id=conversation().conversation_id,
        tenant_id="tenant-a",
        subject_hash="a" * 64,
        limit=5,
    )

    assert conversations[0].title == "Title"
    assert turns[0].status == ConversationTurnStatus.STARTED
    assert recent[0].metadata["ok"] is True
    assert "turn.status = 'finalized'" in cursor.executed[2][0]


def test_repository_turn_lifecycle(monkeypatch) -> None:
    cursor = Cursor(
        one=[
            (1,),
            turn_row(),
            turn_row("finalized", "Answer.", timestamp(), None, {"citations": 1}),
            turn_row("failed", None, timestamp(), "provider_error", {"error": True}),
        ]
    )
    connection = Connection(cursor)
    monkeypatch.setattr(
        conversations_module,
        "psycopg_module",
        lambda: module_for(connection),
    )
    repo = repository()

    started = repo.start_turn(started_turn())
    finalized = repo.finalize_turn(
        conversation_id=conversation().conversation_id,
        turn_id=started.turn_id,
        tenant_id="tenant-a",
        subject_hash="a" * 64,
        answer_text="Answer.",
        metadata={"citations": 1},
    )
    failed = repo.fail_turn(
        conversation_id=conversation().conversation_id,
        turn_id=started.turn_id,
        tenant_id="tenant-a",
        subject_hash="a" * 64,
        error_code="provider_error" * 20,
        metadata={"error": True},
    )

    assert started.sequence_number == 1
    assert finalized is not None
    assert finalized.answer_text == "Answer."
    assert failed is not None
    assert failed.error_code == "provider_error"
    assert connection.commits == 3
    assert cursor.executed[0][1] == (started.conversation_id,)
    assert cursor.executed[1][1][3] == 1


def test_repository_turn_start_and_required_row_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        conversations_module,
        "psycopg_module",
        lambda: module_for(Connection(Cursor(error=RuntimeError("down")))),
    )
    with pytest.raises(DependencyUnavailableError, match="turn start"):
        repository().start_turn(started_turn())

    monkeypatch.setattr(
        conversations_module,
        "psycopg_module",
        lambda: module_for(Connection(Cursor(one=[None]))),
    )
    with pytest.raises(DependencyUnavailableError, match="health"):
        repository().check()


def test_repository_missing_rows_and_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        conversations_module,
        "psycopg_module",
        lambda: module_for(Connection(Cursor(one=[None, None, None]))),
    )
    repo = repository()

    assert (
        repo.get_conversation(
            conversation_id="missing",
            tenant_id="tenant-a",
            subject_hash="a" * 64,
        )
        is None
    )
    assert (
        repo.rename_conversation(
            conversation_id="missing",
            tenant_id="tenant-a",
            subject_hash="a" * 64,
            title="Renamed",
        )
        is None
    )
    assert (
        repo.delete_conversation(
            conversation_id="missing",
            tenant_id="tenant-a",
            subject_hash="a" * 64,
        )
        is False
    )

    monkeypatch.setattr(
        conversations_module,
        "psycopg_module",
        lambda: module_for(Connection(Cursor(error=RuntimeError("down")))),
    )
    with pytest.raises(DependencyUnavailableError, match="read"):
        repo.get_conversation(
            conversation_id="missing",
            tenant_id="tenant-a",
            subject_hash="a" * 64,
        )
    with pytest.raises(DependencyUnavailableError, match="conversation"):
        repo.list_conversations(tenant_id="tenant-a", subject_hash="a" * 64, limit=10)


def test_repository_maps_plain_text_timestamps(monkeypatch) -> None:
    cursor = Cursor(all_rows=[[turn_row(metadata={})]])
    cursor.all_rows[0][0] = tuple(
        "plain-created" if index == 8 else value
        for index, value in enumerate(cursor.all_rows[0][0])
    )
    monkeypatch.setattr(
        conversations_module,
        "psycopg_module",
        lambda: module_for(Connection(cursor)),
    )

    turn = repository().list_turns(
        conversation_id=conversation().conversation_id,
        tenant_id="tenant-a",
        subject_hash="a" * 64,
        limit=10,
    )[0]

    assert turn.created_at == "plain-created"
