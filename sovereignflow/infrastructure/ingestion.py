from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sovereignflow.domain import (
    ConflictError,
    DatasetImportRequest,
    DatasetImportRun,
    DatasetImportStatus,
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

    def replace_relationships(self, command: IngestionCommand) -> None:
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
                        SELECT current_version
                        FROM ingestion.sources
                        WHERE tenant_id = %s AND domain = %s AND source_id = %s
                        """,
                        (command.tenant_id, command.domain, command.source_id),
                    )
                    current = cursor.fetchone()
                    if current is None or str(current[0]) != command.source_version:
                        raise ConflictError(
                            "Relationships can only be published for the active source version"
                        )
                    cursor.execute(
                        """
                        DELETE FROM graph.relationships
                        WHERE tenant_id = %s AND domain = %s
                          AND owner_source_id = %s AND owner_source_version = %s
                        """,
                        (
                            command.tenant_id,
                            command.domain,
                            command.source_id,
                            command.source_version,
                        ),
                    )
                    for relationship in command.relationships:
                        if not self._target_exists(cursor, command, relationship):
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
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
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
                connection.commit()
        except (ConflictError, IngestionError):
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL relationship publication failed") from exc

    def delete_source(
        self,
        *,
        domain: str,
        tenant_id: str,
        source_id: str,
    ) -> None:
        try:
            psycopg = psycopg_module()
            with psycopg.connect(
                self._connection_url,
                connect_timeout=self._timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"{tenant_id}:{domain}:{source_id}",),
                    )
                    cursor.execute(
                        """
                        DELETE FROM graph.relationships
                        WHERE tenant_id = %s AND domain = %s
                          AND (from_source_id = %s OR to_source_id = %s)
                        """,
                        (tenant_id, domain, source_id, source_id),
                    )
                    cursor.execute(
                        """
                        DELETE FROM ingestion.sources
                        WHERE tenant_id = %s AND domain = %s AND source_id = %s
                        """,
                        (tenant_id, domain, source_id),
                    )
                connection.commit()
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL source deletion failed") from exc

    def start_import(self, request: DatasetImportRequest) -> DatasetImportRun:
        try:
            psycopg = psycopg_module()
            with psycopg.connect(
                self._connection_url,
                connect_timeout=self._timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT import_id, domain, tenant_id, dataset_hash, status,
                               source_count, chunk_count, relationship_count, deletion_count,
                               indexed_sources, published_relationships, deleted_sources,
                               error_code, error_message
                        FROM ingestion.import_runs
                        WHERE import_id = %s
                        """,
                        (request.import_id,),
                    )
                    existing = cursor.fetchone()
                    if existing is not None:
                        run = _import_run(existing)
                        if (
                            run.domain != request.domain
                            or run.tenant_id != request.tenant_id
                            or run.dataset_hash != request.dataset_hash
                        ):
                            raise ConflictError(
                                "Import identifier was already used for a different dataset"
                            )
                        return run
                    cursor.execute(
                        """
                        INSERT INTO ingestion.import_runs (
                            import_id, tenant_id, domain, dataset_hash, status,
                            source_count, chunk_count, relationship_count, deletion_count
                        )
                        VALUES (%s, %s, %s, %s, 'staging', %s, %s, %s, %s)
                        """,
                        (
                            request.import_id,
                            request.tenant_id,
                            request.domain,
                            request.dataset_hash,
                            request.source_count,
                            request.chunk_count,
                            request.relationship_count,
                            request.deletion_count,
                        ),
                    )
                connection.commit()
        except (ConflictError, IngestionError):
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL import start failed") from exc
        return DatasetImportRun(
            import_id=request.import_id,
            domain=request.domain,
            tenant_id=request.tenant_id,
            dataset_hash=request.dataset_hash,
            status=DatasetImportStatus.STAGING,
            source_count=request.source_count,
            chunk_count=request.chunk_count,
            relationship_count=request.relationship_count,
            deletion_count=request.deletion_count,
        )

    def load_import(self, import_id: str, *, tenant_id: str) -> DatasetImportRun:
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
                    SELECT import_id, domain, tenant_id, dataset_hash, status,
                           source_count, chunk_count, relationship_count, deletion_count,
                           indexed_sources, published_relationships, deleted_sources,
                           error_code, error_message
                    FROM ingestion.import_runs
                    WHERE import_id = %s AND tenant_id = %s
                    """,
                    (import_id, tenant_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise IngestionError(f"Dataset import does not exist: {import_id}")
                return _import_run(row)
        except IngestionError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL import read failed") from exc

    def update_import(
        self,
        import_id: str,
        *,
        status: DatasetImportStatus,
        indexed_sources: int,
        published_relationships: int,
        deleted_sources: int,
    ) -> None:
        completed = status == DatasetImportStatus.COMPLETED
        self._transition(
            """
            UPDATE ingestion.import_runs
            SET status = %s,
                indexed_sources = %s,
                published_relationships = %s,
                deleted_sources = %s,
                error_code = NULL,
                error_message = NULL,
                updated_at = NOW(),
                completed_at = CASE WHEN %s THEN NOW() ELSE NULL END
            WHERE import_id = %s
            """,
            import_id,
            "Dataset import state cannot be updated",
            parameters=(
                status.value,
                indexed_sources,
                published_relationships,
                deleted_sources,
                completed,
                import_id,
            ),
        )

    def fail_import(self, import_id: str, *, error_code: str, error_message: str) -> None:
        self._transition(
            """
            UPDATE ingestion.import_runs
            SET status = 'failed',
                error_code = %s,
                error_message = %s,
                updated_at = NOW(),
                completed_at = NOW()
            WHERE import_id = %s
            """,
            import_id,
            "Dataset import cannot enter failed state",
            parameters=(error_code[:100], error_message[:2000], import_id),
        )

    def consistency_counts(self, *, domain: str, tenant_id: str) -> dict[str, int]:
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
                    SELECT
                        (SELECT COUNT(*)
                         FROM ingestion.sources
                         WHERE tenant_id = %s AND domain = %s),
                        (SELECT COUNT(*)
                         FROM ingestion.sources source
                         JOIN ingestion.chunks chunk
                           ON chunk.tenant_id = source.tenant_id
                          AND chunk.domain = source.domain
                          AND chunk.source_id = source.source_id
                          AND chunk.source_version = source.current_version
                         WHERE source.tenant_id = %s AND source.domain = %s),
                        (SELECT COUNT(*)
                         FROM graph.relationships relationship
                         JOIN ingestion.sources source
                           ON source.tenant_id = relationship.tenant_id
                          AND source.domain = relationship.domain
                          AND source.source_id = relationship.owner_source_id
                          AND source.current_version = relationship.owner_source_version
                         WHERE relationship.tenant_id = %s AND relationship.domain = %s)
                    """,
                    (tenant_id, domain, tenant_id, domain, tenant_id, domain),
                )
                row = cursor.fetchone()
                if row is None:
                    raise IngestionError("Dataset consistency query returned no result")
                return {
                    "active_sources": int(row[0]),
                    "active_chunks": int(row[1]),
                    "active_relationships": int(row[2]),
                }
        except IngestionError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL consistency check failed") from exc

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


def _import_run(row: tuple[Any, ...]) -> DatasetImportRun:
    return DatasetImportRun(
        import_id=str(row[0]),
        domain=str(row[1]),
        tenant_id=str(row[2]),
        dataset_hash=str(row[3]),
        status=DatasetImportStatus(str(row[4])),
        source_count=int(row[5]),
        chunk_count=int(row[6]),
        relationship_count=int(row[7]),
        deletion_count=int(row[8]),
        indexed_sources=int(row[9]),
        published_relationships=int(row[10]),
        deleted_sources=int(row[11]),
        error_code=row[12],
        error_message=row[13],
    )
