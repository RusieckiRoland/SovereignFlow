from __future__ import annotations

import os
import uuid

import pytest

from sovereignflow.domain import PipelineRun, PipelineStepAudit
from sovereignflow.infrastructure import PostgreSQLExecutionAudit


@pytest.mark.integration
def test_postgresql_execution_audit_round_trip() -> None:
    connection_url = os.getenv("SOVEREIGNFLOW_TEST_POSTGRES_URL")
    if not connection_url:
        pytest.skip("SOVEREIGNFLOW_TEST_POSTGRES_URL is not configured")
    repository = PostgreSQLExecutionAudit(connection_url, timeout_seconds=5)
    repository.migrate()
    repository.migrate()
    repository.check()

    successful_run_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    repository.start(
        PipelineRun(
            successful_run_id,
            request_id,
            "session",
            "general",
            "tenant-a",
            "default-rag",
            "1.0",
            "a" * 64,
            "question",
        )
    )
    repository.record_step(
        PipelineStepAudit(
            successful_run_id,
            1,
            "normalize_query",
            "normalize_query",
            "1.0",
            2,
            None,
        )
    )
    repository.succeed(successful_run_id, answer="answer", citation_count=1)

    result = repository.fetch(request_id, tenant_id="tenant-a")
    assert result is not None
    assert result["status"] == "succeeded"
    assert result["answer"] == "answer"
    assert result["steps"][0]["step_id"] == "normalize_query"
    assert repository.fetch(request_id, tenant_id="tenant-b") is None

    failed_run_id = str(uuid.uuid4())
    failed_request_id = str(uuid.uuid4())
    repository.start(
        PipelineRun(
            failed_run_id,
            failed_request_id,
            "session",
            "general",
            "tenant-a",
            "default-rag",
            "1.0",
            "a" * 64,
            "question",
        )
    )
    repository.fail(
        failed_run_id,
        error_code="provider_protocol_error",
        error_message="provider failed",
    )
    failed = repository.fetch(failed_request_id, tenant_id="tenant-a")
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error_code"] == "provider_protocol_error"
