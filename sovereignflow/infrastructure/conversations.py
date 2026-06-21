from __future__ import annotations

import json
from typing import Any

from sovereignflow.domain import (
    Conversation,
    ConversationStatus,
    ConversationTurn,
    ConversationTurnStatus,
    DependencyUnavailableError,
)

from .postgres_support import psycopg_module


class PostgreSQLConversationHistory:
    def __init__(self, connection_url: str, *, timeout_seconds: int) -> None:
        self._connection_url = connection_url
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "conversation_history"

    def check(self) -> None:
        self._execute_scalar("SELECT 1")

    def create_conversation(self, conversation: Conversation) -> Conversation:
        row = self._fetchone(
            """
            INSERT INTO sf.conversations (
                conversation_id, tenant_id, subject_hash, session_id, domain,
                title, status, created_at, updated_at, deleted_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING conversation_id, tenant_id, subject_hash, session_id, domain,
                      title, status, created_at, updated_at, deleted_at
            """,
            (
                conversation.conversation_id,
                conversation.tenant_id,
                conversation.subject_hash,
                conversation.session_id,
                conversation.domain,
                conversation.title,
                conversation.status.value,
                conversation.created_at,
                conversation.updated_at,
                conversation.deleted_at,
            ),
            failure_message="PostgreSQL conversation create failed",
        )
        return _conversation_from_row(row)

    def list_conversations(
        self,
        *,
        tenant_id: str,
        subject_hash: str,
        limit: int,
    ) -> tuple[Conversation, ...]:
        rows = self._fetchall(
            """
            SELECT conversation_id, tenant_id, subject_hash, session_id, domain,
                   title, status, created_at, updated_at, deleted_at
            FROM sf.conversations
            WHERE tenant_id = %s AND subject_hash = %s AND status = 'active'
            ORDER BY updated_at DESC, created_at DESC
            LIMIT %s
            """,
            (tenant_id, subject_hash, limit),
            failure_message="PostgreSQL conversation list failed",
        )
        return tuple(_conversation_from_row(row) for row in rows)

    def get_conversation(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
    ) -> Conversation | None:
        row = self._fetchone_or_none(
            """
            SELECT conversation_id, tenant_id, subject_hash, session_id, domain,
                   title, status, created_at, updated_at, deleted_at
            FROM sf.conversations
            WHERE conversation_id = %s
              AND tenant_id = %s
              AND subject_hash = %s
              AND status = 'active'
            """,
            (conversation_id, tenant_id, subject_hash),
            failure_message="PostgreSQL conversation read failed",
        )
        return None if row is None else _conversation_from_row(row)

    def rename_conversation(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
        title: str,
    ) -> Conversation | None:
        row = self._fetchone_or_none(
            """
            UPDATE sf.conversations
            SET title = %s, updated_at = NOW()
            WHERE conversation_id = %s
              AND tenant_id = %s
              AND subject_hash = %s
              AND status = 'active'
            RETURNING conversation_id, tenant_id, subject_hash, session_id, domain,
                      title, status, created_at, updated_at, deleted_at
            """,
            (title, conversation_id, tenant_id, subject_hash),
            failure_message="PostgreSQL conversation rename failed",
            commit=True,
        )
        return None if row is None else _conversation_from_row(row)

    def delete_conversation(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
    ) -> bool:
        row = self._fetchone_or_none(
            """
            UPDATE sf.conversations
            SET status = 'deleted', deleted_at = NOW(), updated_at = NOW()
            WHERE conversation_id = %s
              AND tenant_id = %s
              AND subject_hash = %s
              AND status = 'active'
            RETURNING conversation_id
            """,
            (conversation_id, tenant_id, subject_hash),
            failure_message="PostgreSQL conversation delete failed",
            commit=True,
        )
        return row is not None

    def start_turn(self, turn: ConversationTurn) -> ConversationTurn:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(sequence_number), 0) + 1
                    FROM sf.conversation_turns
                    WHERE conversation_id = %s
                    """,
                    (turn.conversation_id,),
                )
                next_sequence = int(cursor.fetchone()[0])
                cursor.execute(
                    """
                    INSERT INTO sf.conversation_turns (
                        turn_id, conversation_id, request_id, sequence_number,
                        question_text, answer_text, status, error_code,
                        created_at, finalized_at, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, NULL, 'started', NULL, %s, NULL, '{}'::jsonb)
                    ON CONFLICT (conversation_id, request_id) DO UPDATE
                    SET request_id = EXCLUDED.request_id
                    RETURNING turn_id, conversation_id, request_id, sequence_number,
                              question_text, answer_text, status, error_code,
                              created_at, finalized_at, metadata
                    """,
                    (
                        turn.turn_id,
                        turn.conversation_id,
                        turn.request_id,
                        next_sequence,
                        turn.question_text,
                        turn.created_at,
                    ),
                )
                row = cursor.fetchone()
                connection.commit()
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL conversation turn start failed") from exc
        return _turn_from_row(row)

    def finalize_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        tenant_id: str,
        subject_hash: str,
        answer_text: str,
        metadata: dict[str, Any],
    ) -> ConversationTurn | None:
        row = self._fetchone_or_none(
            """
            UPDATE sf.conversation_turns turn
            SET status = 'finalized', answer_text = %s, finalized_at = NOW(), metadata = %s::jsonb
            FROM sf.conversations conversation
            WHERE turn.conversation_id = conversation.conversation_id
              AND turn.conversation_id = %s
              AND turn.turn_id = %s
              AND turn.status = 'started'
              AND conversation.tenant_id = %s
              AND conversation.subject_hash = %s
              AND conversation.status = 'active'
            RETURNING turn.turn_id, turn.conversation_id, turn.request_id, turn.sequence_number,
                      turn.question_text, turn.answer_text, turn.status, turn.error_code,
                      turn.created_at, turn.finalized_at, turn.metadata
            """,
            (answer_text, json.dumps(metadata), conversation_id, turn_id, tenant_id, subject_hash),
            failure_message="PostgreSQL conversation turn finalize failed",
            commit=True,
        )
        return None if row is None else _turn_from_row(row)

    def fail_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        tenant_id: str,
        subject_hash: str,
        error_code: str,
        metadata: dict[str, Any],
    ) -> ConversationTurn | None:
        row = self._fetchone_or_none(
            """
            UPDATE sf.conversation_turns turn
            SET status = 'failed', error_code = %s, finalized_at = NOW(), metadata = %s::jsonb
            FROM sf.conversations conversation
            WHERE turn.conversation_id = conversation.conversation_id
              AND turn.conversation_id = %s
              AND turn.turn_id = %s
              AND turn.status = 'started'
              AND conversation.tenant_id = %s
              AND conversation.subject_hash = %s
              AND conversation.status = 'active'
            RETURNING turn.turn_id, turn.conversation_id, turn.request_id, turn.sequence_number,
                      turn.question_text, turn.answer_text, turn.status, turn.error_code,
                      turn.created_at, turn.finalized_at, turn.metadata
            """,
            (
                error_code[:100],
                json.dumps(metadata),
                conversation_id,
                turn_id,
                tenant_id,
                subject_hash,
            ),
            failure_message="PostgreSQL conversation turn fail failed",
            commit=True,
        )
        return None if row is None else _turn_from_row(row)

    def list_turns(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]:
        rows = self._turn_rows(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            subject_hash=subject_hash,
            limit=limit,
            finalized_only=False,
        )
        return tuple(_turn_from_row(row) for row in rows)

    def recent_finalized_turns(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
        limit: int,
    ) -> tuple[ConversationTurn, ...]:
        rows = self._turn_rows(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            subject_hash=subject_hash,
            limit=limit,
            finalized_only=True,
        )
        return tuple(_turn_from_row(row) for row in rows)

    def _turn_rows(
        self,
        *,
        conversation_id: str,
        tenant_id: str,
        subject_hash: str,
        limit: int,
        finalized_only: bool,
    ) -> tuple[Any, ...]:
        status_filter = "AND turn.status = 'finalized'" if finalized_only else ""
        return self._fetchall(
            f"""
            SELECT turn_id, conversation_id, request_id, sequence_number,
                   question_text, answer_text, status, error_code,
                   created_at, finalized_at, metadata
            FROM (
                SELECT turn.turn_id, turn.conversation_id, turn.request_id, turn.sequence_number,
                       turn.question_text, turn.answer_text, turn.status, turn.error_code,
                       turn.created_at, turn.finalized_at, turn.metadata
                FROM sf.conversation_turns turn
                JOIN sf.conversations conversation
                  ON conversation.conversation_id = turn.conversation_id
                WHERE turn.conversation_id = %s
                  AND conversation.tenant_id = %s
                  AND conversation.subject_hash = %s
                  AND conversation.status = 'active'
                  {status_filter}
                ORDER BY turn.sequence_number DESC
                LIMIT %s
            ) recent_turns
            ORDER BY sequence_number ASC
            """,
            (conversation_id, tenant_id, subject_hash, limit),
            failure_message="PostgreSQL conversation turn list failed",
        )

    def _execute_scalar(self, statement: str) -> Any:
        row = self._fetchone(
            statement,
            (),
            failure_message="PostgreSQL conversation health failed",
        )
        return row[0] if row else None

    def _fetchone(
        self,
        statement: str,
        parameters: tuple[Any, ...],
        *,
        failure_message: str,
        commit: bool = False,
    ) -> Any:
        row = self._fetchone_or_none(
            statement,
            parameters,
            failure_message=failure_message,
            commit=commit,
        )
        if row is None:
            raise DependencyUnavailableError(failure_message)
        return row

    def _fetchone_or_none(
        self,
        statement: str,
        parameters: tuple[Any, ...],
        *,
        failure_message: str,
        commit: bool = False,
    ) -> Any | None:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, parameters)
                row = cursor.fetchone()
                if commit:
                    connection.commit()
                return row
        except Exception as exc:
            raise DependencyUnavailableError(failure_message) from exc

    def _fetchall(
        self,
        statement: str,
        parameters: tuple[Any, ...],
        *,
        failure_message: str,
    ) -> tuple[Any, ...]:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, parameters)
                return tuple(cursor.fetchall())
        except Exception as exc:
            raise DependencyUnavailableError(failure_message) from exc


def _conversation_from_row(row: Any) -> Conversation:
    return Conversation(
        conversation_id=str(row[0]),
        tenant_id=str(row[1]),
        subject_hash=str(row[2]),
        session_id=str(row[3]),
        domain=str(row[4]),
        title=str(row[5]),
        status=ConversationStatus(str(row[6])),
        created_at=_timestamp(row[7]),
        updated_at=_timestamp(row[8]),
        deleted_at=None if row[9] is None else _timestamp(row[9]),
    )


def _turn_from_row(row: Any) -> ConversationTurn:
    metadata = row[10]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return ConversationTurn(
        turn_id=str(row[0]),
        conversation_id=str(row[1]),
        request_id=str(row[2]),
        sequence_number=int(row[3]),
        question_text=str(row[4]),
        answer_text=None if row[5] is None else str(row[5]),
        status=ConversationTurnStatus(str(row[6])),
        error_code=None if row[7] is None else str(row[7]),
        created_at=_timestamp(row[8]),
        finalized_at=None if row[9] is None else _timestamp(row[9]),
        metadata=metadata,
    )


def _timestamp(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)
