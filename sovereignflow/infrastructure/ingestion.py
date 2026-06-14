from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sovereignflow.domain import (
    ConflictError,
    DependencyUnavailableError,
    DocumentChunk,
    GraphNodeRef,
    GraphRelationship,
    IngestionCommand,
    IngestionError,
    IngestionJob,
    IngestionJobStatus,
)

from .postgres_support import psycopg_module


class PostgreSQLIngestionRepository:
    name = "ingestion_repository"

    def __init__(
        self,
        connection_url: str,
        *,
        timeout_seconds: int,
        job_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self._connection_url = connection_url
        self._timeout_seconds = timeout_seconds
        self._job_id_factory = job_id_factory

    def check(self) -> None:
        self._read_scalar("SELECT 1")

    def stage(self, command: IngestionCommand, *, payload_hash: str) -> IngestionJob:
        try:
            psycopg = psycopg_module()
            with psycopg.connect(
                self._connection_url,
                connect_timeout=self._timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"{command.tenant_id}:{command.domain}:{command.source_id}",),
                    )
                    cursor.execute(
                        """
                        SELECT job_id, payload_hash
                        FROM ingestion.jobs
                        WHERE tenant_id = %s AND domain = %s AND idempotency_key = %s
                        """,
                        (command.tenant_id, command.domain, command.idempotency_key),
                    )
                    existing_job = cursor.fetchone()
                    if existing_job is not None:
                        if existing_job[1] != payload_hash:
                            raise ConflictError(
                                "Idempotency key was already used for a different payload"
                            )
                        job = self._load_with_cursor(cursor, str(existing_job[0]))
                        connection.commit()
                        return job

                    cursor.execute(
                        """
                        SELECT payload_hash
                        FROM ingestion.source_versions
                        WHERE tenant_id = %s AND domain = %s
                          AND source_id = %s AND source_version = %s
                        """,
                        (
                            command.tenant_id,
                            command.domain,
                            command.source_id,
                            command.source_version,
                        ),
                    )
                    existing_version = cursor.fetchone()
                    if existing_version is not None and existing_version[0] != payload_hash:
                        raise ConflictError("Source version already exists with different content")

                    job_id = self._job_id_factory()
                    if existing_version is None:
                        cursor.execute(
                            """
                            INSERT INTO ingestion.source_versions (
                                tenant_id, domain, source_id, source_version,
                                source_uri, payload_hash, metadata_json
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                command.tenant_id,
                                command.domain,
                                command.source_id,
                                command.source_version,
                                command.source_uri,
                                payload_hash,
                                _json(command.metadata),
                            ),
                        )
                        for position, chunk in enumerate(command.chunks):
                            cursor.execute(
                                """
                                INSERT INTO ingestion.chunks (
                                    tenant_id, domain, source_id, source_version,
                                    chunk_id, position, source_uri, text_content,
                                    metadata_json, acl_labels, classification_level
                                )
                                VALUES (
                                    %s, %s, %s, %s, %s, %s, %s, %s,
                                    %s::jsonb, %s, %s
                                )
                                """,
                                (
                                    command.tenant_id,
                                    command.domain,
                                    command.source_id,
                                    command.source_version,
                                    chunk.chunk_id,
                                    position,
                                    chunk.source_uri,
                                    chunk.text,
                                    _json(chunk.metadata),
                                    list(chunk.acl_labels),
                                    chunk.classification_level,
                                ),
                            )
                        for relationship in command.relationships:
                            if (
                                relationship.to_node.source_id != command.source_id
                                and not self._target_exists(cursor, command, relationship)
                            ):
                                raise ConflictError(
                                    "Relationship target does not exist in the current graph"
                                )
                            cursor.execute(
                                """
                                INSERT INTO graph.relationships (
                                    tenant_id, domain, owner_source_id, owner_source_version,
                                    from_source_id, from_chunk_id, to_source_id, to_chunk_id,
                                    relationship_type, metadata_json
                                )
                                VALUES (
                                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                                )
                                """,
                                (
                                    command.tenant_id,
                                    command.domain,
                                    command.source_id,
                                    command.source_version,
                                    relationship.from_node.source_id,
                                    relationship.from_node.chunk_id,
                                    relationship.to_node.source_id,
                                    relationship.to_node.chunk_id,
                                    relationship.relationship_type,
                                    _json(relationship.metadata),
                                ),
                            )
                    cursor.execute(
                        """
                        INSERT INTO ingestion.jobs (
                            job_id, tenant_id, domain, source_id, source_version,
                            idempotency_key, payload_hash, status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'staged')
                        """,
                        (
                            job_id,
                            command.tenant_id,
                            command.domain,
                            command.source_id,
                            command.source_version,
                            command.idempotency_key,
                            payload_hash,
                        ),
                    )
                connection.commit()
        except (ConflictError, IngestionError):
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL ingestion stage failed") from exc
        return IngestionJob(
            job_id=job_id,
            payload_hash=payload_hash,
            status=IngestionJobStatus.STAGED,
            command=command,
        )

    def load(self, job_id: str) -> IngestionJob:
        return self._load(job_id)

    def load_for_tenant(self, job_id: str, *, tenant_id: str) -> IngestionJob:
        return self._load(job_id, tenant_id=tenant_id)

    def _load(self, job_id: str, *, tenant_id: str | None = None) -> IngestionJob:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                return self._load_with_cursor(cursor, job_id, tenant_id=tenant_id)
        except IngestionError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL ingestion read failed") from exc

    def mark_indexing(self, job_id: str) -> None:
        self._transition(
            """
            UPDATE ingestion.jobs
            SET status = 'indexing',
                attempts = attempts + 1,
                error_code = NULL,
                error_message = NULL,
                updated_at = NOW()
            WHERE job_id = %s AND status IN ('staged', 'indexing', 'failed')
            """,
            job_id,
            "Ingestion job cannot enter indexing state",
        )

    def mark_indexed(self, job_id: str) -> None:
        try:
            psycopg = psycopg_module()
            with psycopg.connect(
                self._connection_url,
                connect_timeout=self._timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE ingestion.jobs
                        SET status = 'indexed', updated_at = NOW(), completed_at = NOW()
                        WHERE job_id = %s AND status = 'indexing'
                        RETURNING tenant_id, domain, source_id, source_version
                        """,
                        (job_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise IngestionError("Ingestion job cannot enter indexed state")
                    cursor.execute(
                        """
                        INSERT INTO ingestion.sources (
                            tenant_id, domain, source_id, current_version,
                            current_job_id, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (tenant_id, domain, source_id)
                        DO UPDATE SET
                            current_version = EXCLUDED.current_version,
                            current_job_id = EXCLUDED.current_job_id,
                            updated_at = NOW()
                        """,
                        (*row, job_id),
                    )
                connection.commit()
        except IngestionError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL ingestion completion failed") from exc

    def mark_failed(self, job_id: str, *, error_code: str, error_message: str) -> None:
        self._transition(
            """
            UPDATE ingestion.jobs
            SET status = 'failed',
                error_code = %s,
                error_message = %s,
                updated_at = NOW(),
                completed_at = NOW()
            WHERE job_id = %s AND status = 'indexing'
            """,
            job_id,
            "Ingestion job cannot enter failed state",
            parameters=(error_code[:100], error_message[:2000], job_id),
        )

    def _load_with_cursor(
        self,
        cursor: Any,
        job_id: str,
        *,
        tenant_id: str | None = None,
    ) -> IngestionJob:
        cursor.execute(
            """
            SELECT j.job_id, j.payload_hash, j.status, j.attempts,
                   j.idempotency_key, j.domain, j.tenant_id, j.source_id,
                   j.source_version, v.source_uri, v.metadata_json
            FROM ingestion.jobs j
            JOIN ingestion.source_versions v
              ON v.tenant_id = j.tenant_id
             AND v.domain = j.domain
             AND v.source_id = j.source_id
             AND v.source_version = j.source_version
            WHERE j.job_id = %s
              AND (%s::text IS NULL OR j.tenant_id = %s)
            """,
            (job_id, tenant_id, tenant_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise IngestionError(f"Ingestion job does not exist: {job_id}")
        cursor.execute(
            """
            SELECT chunk_id, source_uri, text_content, metadata_json,
                   acl_labels, classification_level
            FROM ingestion.chunks
            WHERE tenant_id = %s AND domain = %s
              AND source_id = %s AND source_version = %s
            ORDER BY position
            """,
            (row[6], row[5], row[7], row[8]),
        )
        chunks = tuple(
            DocumentChunk(
                chunk_id=str(chunk[0]),
                domain=str(row[5]),
                tenant_id=str(row[6]),
                source_id=str(row[7]),
                source_uri=chunk[1],
                text=str(chunk[2]),
                metadata=_mapping(chunk[3]),
                acl_labels=tuple(chunk[4] or ()),
                classification_level=int(chunk[5]),
            )
            for chunk in cursor.fetchall()
        )
        cursor.execute(
            """
            SELECT from_source_id, from_chunk_id, to_source_id, to_chunk_id,
                   relationship_type, metadata_json
            FROM graph.relationships
            WHERE tenant_id = %s AND domain = %s
              AND owner_source_id = %s AND owner_source_version = %s
            ORDER BY from_source_id, from_chunk_id, to_source_id, to_chunk_id,
                     relationship_type
            """,
            (row[6], row[5], row[7], row[8]),
        )
        relationships = tuple(
            GraphRelationship(
                from_node=GraphNodeRef(str(item[0]), str(item[1])),
                to_node=GraphNodeRef(str(item[2]), str(item[3])),
                relationship_type=str(item[4]),
                metadata=_mapping(item[5]),
            )
            for item in cursor.fetchall()
        )
        return IngestionJob(
            job_id=str(row[0]),
            payload_hash=str(row[1]),
            status=IngestionJobStatus(str(row[2])),
            attempts=int(row[3]),
            command=IngestionCommand(
                idempotency_key=str(row[4]),
                domain=str(row[5]),
                tenant_id=str(row[6]),
                source_id=str(row[7]),
                source_version=str(row[8]),
                source_uri=row[9],
                metadata=_mapping(row[10]),
                chunks=chunks,
                relationships=relationships,
            ),
        )

    @staticmethod
    def _target_exists(cursor: Any, command: IngestionCommand, relationship) -> bool:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM ingestion.sources source
                JOIN ingestion.chunks chunk
                  ON chunk.tenant_id = source.tenant_id
                 AND chunk.domain = source.domain
                 AND chunk.source_id = source.source_id
                 AND chunk.source_version = source.current_version
                WHERE source.tenant_id = %s
                  AND source.domain = %s
                  AND source.source_id = %s
                  AND chunk.chunk_id = %s
            )
            """,
            (
                command.tenant_id,
                command.domain,
                relationship.to_node.source_id,
                relationship.to_node.chunk_id,
            ),
        )
        row = cursor.fetchone()
        return bool(row and row[0])

    def _transition(
        self,
        statement: str,
        job_id: str,
        error_message: str,
        *,
        parameters: tuple[Any, ...] | None = None,
    ) -> None:
        try:
            psycopg = psycopg_module()
            with psycopg.connect(
                self._connection_url,
                connect_timeout=self._timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(statement, parameters or (job_id,))
                    if cursor.rowcount != 1:
                        raise IngestionError(error_message)
                connection.commit()
        except IngestionError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL ingestion transition failed") from exc

    def _read_scalar(self, statement: str) -> Any:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL ingestion health check failed") from exc


def _json(value: Any) -> str:
    serializable = dict(value) if isinstance(value, Mapping) else value
    return json.dumps(serializable, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _mapping(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError) as exc:
        raise IngestionError("Stored ingestion metadata is invalid") from exc
    if not isinstance(decoded, dict):
        raise IngestionError("Stored ingestion metadata is invalid")
    return decoded
