from __future__ import annotations

import hmac
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from flask import Flask, jsonify, request

from sovereignflow.application import HealthProbe, OperationsService, RagQueryService
from sovereignflow.domain import (
    AuthenticationError,
    DomainNotFoundError,
    QueryCommand,
    SovereignFlowError,
    ValidationError,
)


class QueryDispatcher:
    def __init__(self, services: Mapping[str, RagQueryService]) -> None:
        self._services = dict(services)

    @property
    def domains(self) -> tuple[str, ...]:
        return tuple(sorted(self._services))

    def execute(self, command: QueryCommand):
        service = self._services.get(command.domain)
        if service is None:
            raise DomainNotFoundError(f"Unknown domain: {command.domain}")
        return service.execute(command)


def create_app(
    dispatcher: QueryDispatcher,
    readiness_probes: Sequence[HealthProbe],
    operations: OperationsService,
    admin_api_key: str,
) -> Flask:
    app = Flask(__name__)
    probes = tuple(readiness_probes)
    if not admin_api_key:
        raise ValidationError("admin_api_key is required")

    def authenticate_admin() -> None:
        supplied = str(request.headers.get("X-SovereignFlow-Admin-Key") or "")
        if not hmac.compare_digest(supplied, admin_api_key):
            raise AuthenticationError("Administrative authentication failed")

    def tenant_id() -> str:
        return str(request.args.get("tenant_id") or "").strip()

    @app.get("/live")
    def live() -> Any:
        return jsonify({"ok": True})

    @app.get("/ready")
    def ready() -> Any:
        components: dict[str, str] = {}
        healthy = True
        for probe in probes:
            try:
                probe.check()
                components[probe.name] = "ready"
            except SovereignFlowError:
                components[probe.name] = "unavailable"
                healthy = False
        return jsonify({"ok": healthy, "components": components}), 200 if healthy else 503

    @app.post("/v1/query")
    def query() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValidationError("Request body must be a JSON object")
        request_id = str(request.headers.get("X-Request-ID") or "").strip() or str(uuid.uuid4())
        filters = payload.get("filters")
        if filters is None:
            filters = {}
        if not isinstance(filters, dict):
            raise ValidationError("filters must be a JSON object")
        result = dispatcher.execute(
            QueryCommand(
                request_id=request_id,
                query=str(payload.get("query") or ""),
                domain=str(payload.get("domain") or ""),
                session_id=str(payload.get("session_id") or ""),
                filters=filters,
            )
        )
        return jsonify(
            {
                "ok": True,
                "request_id": result.request_id,
                "answer": result.answer,
                "domain": result.domain,
                "session_id": result.session_id,
                "citations": [
                    {
                        "source_id": citation.source_id,
                        "chunk_id": citation.chunk_id,
                        "source_uri": citation.source_uri,
                        "score": citation.score,
                        "score_type": citation.score_type,
                        "metadata": dict(citation.metadata),
                    }
                    for citation in result.citations
                ],
                "pipeline_trace": list(result.pipeline_trace),
            }
        )

    @app.get("/v1/admin/executions/<request_id>")
    def execution(request_id: str) -> Any:
        authenticate_admin()
        payload = operations.execution(request_id, tenant_id=tenant_id())
        if payload is None:
            return jsonify({"ok": True, "execution": None})
        return jsonify({"ok": True, "execution": payload})

    @app.get("/v1/admin/metrics")
    def metrics() -> Any:
        authenticate_admin()
        try:
            hours = int(request.args.get("hours", "24"))
        except ValueError as exc:
            raise ValidationError("hours must be an integer") from exc
        return jsonify(
            {
                "ok": True,
                "metrics": operations.metrics(tenant_id=tenant_id(), hours=hours),
            }
        )

    @app.get("/v1/admin/ingestion/jobs/<job_id>")
    def ingestion_job(job_id: str) -> Any:
        authenticate_admin()
        return jsonify(
            {
                "ok": True,
                "job": operations.ingestion_job(job_id, tenant_id=tenant_id()),
            }
        )

    @app.post("/v1/admin/ingestion/jobs/<job_id>/retry")
    def retry_ingestion(job_id: str) -> Any:
        authenticate_admin()
        return jsonify(
            {
                "ok": True,
                "job": operations.retry_ingestion(job_id, tenant_id=tenant_id()),
            }
        )

    @app.errorhandler(SovereignFlowError)
    def handle_known_error(error: SovereignFlowError) -> Any:
        request_id = str(request.headers.get("X-Request-ID") or "").strip() or str(uuid.uuid4())
        return (
            jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": error.code,
                        "message": error.safe_message,
                        "request_id": request_id,
                    },
                }
            ),
            error.http_status,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception) -> Any:
        app.logger.exception("Unhandled request error", exc_info=error)
        request_id = str(request.headers.get("X-Request-ID") or "").strip() or str(uuid.uuid4())
        return (
            jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "internal_error",
                        "message": "The request could not be completed.",
                        "request_id": request_id,
                    },
                }
            ),
            500,
        )

    return app
