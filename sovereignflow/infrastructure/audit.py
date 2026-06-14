from __future__ import annotations

from typing import Any

from sovereignflow.domain import (
    DependencyUnavailableError,
    PipelineRun,
    PipelineStepAudit,
)

from .postgres_support import psycopg_module


class PostgreSQLExecutionAudit:
    def __init__(self, connection_url: str, *, timeout_seconds: int) -> None:
        self._connection_url = connection_url
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "execution_audit"

    def check(self) -> None:
        self._execute_scalar("SELECT 1")

    def start(self, run: PipelineRun) -> None:
        self._execute(
            """
            INSERT INTO execution.pipeline_runs (
                run_id, request_id, session_id, domain, tenant_id,
                pipeline_name, pipeline_version, pipeline_checksum,
                status, query_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running', %s)
            """,
            (
                run.run_id,
                run.request_id,
                run.session_id,
                run.domain,
                run.tenant_id,
                run.pipeline_name,
                run.pipeline_version,
                run.pipeline_checksum,
                run.query,
            ),
        )

    def record_step(self, step: PipelineStepAudit) -> None:
        self._execute(
            """
            INSERT INTO execution.pipeline_steps (
                run_id, sequence_number, step_id, action_id,
                action_version, duration_ms, next_step_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                step.run_id,
                step.sequence_number,
                step.step_id,
                step.action,
                step.action_version,
                step.duration_ms,
                step.next_step_id,
            ),
        )

    def succeed(self, run_id: str, *, answer: str, citation_count: int) -> None:
        self._complete(
            run_id,
            status="succeeded",
            answer=answer,
            citation_count=citation_count,
            error_code=None,
            error_message=None,
        )

    def fail(self, run_id: str, *, error_code: str, error_message: str) -> None:
        self._complete(
            run_id,
            status="failed",
            answer=None,
            citation_count=0,
            error_code=error_code[:100],
            error_message=error_message[:2000],
        )

    def fetch(self, request_id: str, *, tenant_id: str) -> dict[str, Any] | None:
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
                        SELECT run_id, request_id, session_id, domain, tenant_id,
                               pipeline_name, pipeline_version, pipeline_checksum,
                               status, query_text, answer_text, citation_count,
                               error_code, error_message, started_at, completed_at
                        FROM execution.pipeline_runs
                        WHERE request_id = %s AND tenant_id = %s
                        ORDER BY started_at DESC
                        LIMIT 1
                        """,
                    (request_id, tenant_id),
                )
                run = cursor.fetchone()
                if run is None:
                    return None
                cursor.execute(
                    """
                        SELECT sequence_number, step_id, action_id, action_version,
                               duration_ms, next_step_id, completed_at
                        FROM execution.pipeline_steps
                        WHERE run_id = %s
                        ORDER BY sequence_number
                        """,
                    (run[0],),
                )
                steps = cursor.fetchall()
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL audit read failed") from exc
        keys = (
            "run_id",
            "request_id",
            "session_id",
            "domain",
            "tenant_id",
            "pipeline_name",
            "pipeline_version",
            "pipeline_checksum",
            "status",
            "query",
            "answer",
            "citation_count",
            "error_code",
            "error_message",
            "started_at",
            "completed_at",
        )
        payload = dict(zip(keys, run, strict=True))
        payload["steps"] = [
            {
                "sequence_number": item[0],
                "step_id": item[1],
                "action": item[2],
                "action_version": item[3],
                "duration_ms": item[4],
                "next_step_id": item[5],
                "completed_at": item[6],
            }
            for item in steps
        ]
        return payload

    def _complete(
        self,
        run_id: str,
        *,
        status: str,
        answer: str | None,
        citation_count: int,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        self._execute(
            """
            UPDATE execution.pipeline_runs
            SET status = %s,
                answer_text = %s,
                citation_count = %s,
                error_code = %s,
                error_message = %s,
                completed_at = NOW()
            WHERE run_id = %s AND status = 'running'
            """,
            (status, answer, citation_count, error_code, error_message, run_id),
        )

    def _execute(self, statement: str, parameters: tuple[Any, ...]) -> None:
        try:
            psycopg = psycopg_module()
            with psycopg.connect(
                self._connection_url,
                connect_timeout=self._timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(statement, parameters)
                connection.commit()
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL audit write failed") from exc

    def _execute_scalar(self, statement: str) -> Any:
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
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL audit health check failed") from exc
        return row[0] if row else None
