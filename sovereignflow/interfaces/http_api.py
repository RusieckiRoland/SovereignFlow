from __future__ import annotations

import hmac
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException

from sovereignflow.application import (
    AuthenticationPort,
    HealthProbe,
    OperationsService,
    PipelineAuthorizationService,
    PolicyAdministrationService,
    RagQueryService,
)
from sovereignflow.domain import (
    AccessPolicyBundle,
    AuthenticationError,
    CapabilityDescriptor,
    ClaimGroupMapping,
    DomainNotFoundError,
    GroupCapabilityGrant,
    QueryCommand,
    SovereignFlowError,
    ValidationError,
)


class QueryDispatcher:
    def __init__(
        self,
        services: Mapping[str | tuple[str, str], RagQueryService],
        authorization: PipelineAuthorizationService | None = None,
        *,
        default_pipelines: Mapping[str, str] | None = None,
    ) -> None:
        self._services: dict[tuple[str, str], RagQueryService] = {}
        inferred_defaults: dict[str, str] = {}
        for key, service in services.items():
            if isinstance(key, tuple):
                domain, pipeline_name = key
            else:
                domain, pipeline_name = key, "default"
            self._services[(domain, pipeline_name)] = service
            inferred_defaults.setdefault(domain, pipeline_name)
        self._default_pipelines = {**inferred_defaults, **dict(default_pipelines or {})}
        self._authorization = authorization

    @property
    def domains(self) -> tuple[str, ...]:
        return tuple(sorted(self._default_pipelines))

    @property
    def requires_capability(self) -> bool:
        return self._authorization is not None

    def catalog(self, authorization):
        return () if self._authorization is None else self._authorization.catalog(authorization)

    def execute(self, command: QueryCommand, *, capability_id: str | None = None):
        if self._authorization is None:
            pipeline_name = self._default_pipelines.get(command.domain)
            service = self._services.get((command.domain, str(pipeline_name)))
        else:
            decision = self._authorization.authorize(
                request_id=command.request_id,
                capability_id=str(capability_id or ""),
                authorization=command.authorization,
                diagnostics_requested=command.diagnostics_requested,
            )
            capability = cast(CapabilityDescriptor, decision.capability)
            domain = capability.domain
            service = self._services.get((domain, capability.pipeline_name))
            command = QueryCommand(
                request_id=command.request_id,
                query=command.query,
                domain=domain,
                session_id=command.session_id,
                authorization=command.authorization,
                filters=command.filters,
                diagnostics_requested=command.diagnostics_requested,
            )
        if service is None:
            raise DomainNotFoundError("The requested capability is not available")
        return service.execute(command)


@dataclass(frozen=True)
class WebClientConfiguration:
    client_id: str
    authorization_url: str
    token_url: str
    logout_url: str


def create_app(
    dispatcher: QueryDispatcher,
    readiness_probes: Sequence[HealthProbe],
    operations: OperationsService,
    admin_api_key: str,
    authenticator: AuthenticationPort,
    web_client: WebClientConfiguration | None = None,
    policy_administration: PolicyAdministrationService | None = None,
) -> Flask:
    app = Flask(__name__)
    probes = tuple(readiness_probes)
    if not admin_api_key:
        raise ValidationError("admin_api_key is required")
    web_root = Path(__file__).with_name("web")

    def authenticate_admin() -> None:
        supplied = str(request.headers.get("X-SovereignFlow-Admin-Key") or "")
        if not hmac.compare_digest(supplied, admin_api_key):
            raise AuthenticationError("Administrative authentication failed")

    def tenant_id() -> str:
        return str(request.args.get("tenant_id") or "").strip()

    def authenticate_query():
        authorization = str(request.headers.get("Authorization") or "").strip()
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("Bearer access token is required")
        return authenticator.authenticate(token.strip())

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

    if web_client is not None:

        @app.get("/")
        def web_root_redirect() -> Any:
            return redirect("/app/")

        @app.get("/app")
        def web_redirect() -> Any:
            return redirect("/app/")

        @app.get("/app/")
        def web_index() -> Any:
            return _secure_web_response(
                send_from_directory(web_root, "index.html"),
                web_client,
            )

        @app.get("/app/config.json")
        def web_configuration() -> Any:
            response = jsonify(
                {
                    "api_url": "/v1/query",
                    "client_id": web_client.client_id,
                    "authorization_url": web_client.authorization_url,
                    "token_url": web_client.token_url,
                    "logout_url": web_client.logout_url,
                    "domains": list(dispatcher.domains),
                }
            )
            response.headers["Cache-Control"] = "no-store"
            return _secure_web_response(response, web_client)

        @app.get("/app/assets/<path:filename>")
        def web_asset(filename: str) -> Any:
            return _secure_web_response(
                send_from_directory(web_root / "assets", filename),
                web_client,
            )

    @app.post("/v1/query")
    def query() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValidationError("Request body must be a JSON object")
        forbidden_security_fields = {
            "tenant_id",
            "acl_labels",
            "security",
            "clearance_label",
            "classification_labels",
            "roles",
            "groups",
            "allow_external_model",
            "diagnostic_access",
        }.intersection(payload)
        if dispatcher.requires_capability:
            forbidden_security_fields.update(
                {"domain", "pipeline_id", "pipeline_name"}.intersection(payload)
            )
        if forbidden_security_fields:
            raise ValidationError(
                "Security context cannot be supplied in request body: "
                + ", ".join(sorted(forbidden_security_fields))
            )
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
                domain=(
                    "pending-authorization"
                    if dispatcher.requires_capability
                    else str(payload.get("domain") or "")
                ),
                session_id=str(payload.get("session_id") or ""),
                authorization=authenticate_query(),
                filters=filters,
                diagnostics_requested=(
                    str(request.headers.get("X-SovereignFlow-Diagnostics") or "").lower() == "true"
                ),
            ),
            capability_id=(
                str(payload.get("capability_id") or "") if dispatcher.requires_capability else None
            ),
        )
        response = {
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
        if (
            result.diagnostics is not None
            and str(request.headers.get("X-SovereignFlow-Diagnostics") or "").lower() == "true"
        ):
            response["diagnostics"] = _serialize_diagnostics(result.diagnostics)
            response["retrieval_trace"] = _serialize_retrieval_trace(result.diagnostics)
            response["usage"] = {
                "prompt_tokens": result.diagnostics.prompt_tokens,
                "completion_tokens": result.diagnostics.completion_tokens,
                "total_tokens": (
                    result.diagnostics.prompt_tokens + result.diagnostics.completion_tokens
                ),
                "cost": None,
            }
        return jsonify(response)

    @app.get("/v1/catalog")
    def catalog() -> Any:
        return jsonify(
            {
                "ok": True,
                "capabilities": [
                    {
                        "capability_id": item.capability_id,
                        "display_name": item.display_name,
                        "description": item.description,
                        "domain": item.domain,
                        "pipeline_name": item.pipeline_name,
                        "diagnostics_available": item.diagnostics_available,
                        "external_model": item.external_model,
                        "policy_version": item.policy_version,
                    }
                    for item in dispatcher.catalog(authenticate_query())
                ],
            }
        )

    if policy_administration is not None:

        @app.put("/v1/admin/access-policies/<tenant_id>")
        def publish_access_policy(tenant_id: str) -> Any:
            authenticate_admin()
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                raise ValidationError("Request body must be a JSON object")
            bundle, expected_version = _parse_policy_bundle(tenant_id, payload)
            policy_administration.publish(bundle, expected_version=expected_version)
            return jsonify(
                {
                    "ok": True,
                    "tenant_id": bundle.tenant_id,
                    "policy_version": bundle.version,
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

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException) -> Any:
        request_id = str(request.headers.get("X-Request-ID") or "").strip() or str(uuid.uuid4())
        code = str(error.name or "http_error").lower().replace(" ", "_")
        return (
            jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": code,
                        "message": str(error.description),
                        "request_id": request_id,
                    },
                }
            ),
            error.code or 500,
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


def _secure_web_response(response, configuration: WebClientConfiguration):
    identity_origins = sorted(
        {
            f"{parts.scheme}://{parts.netloc}"
            for url in (
                configuration.authorization_url,
                configuration.token_url,
                configuration.logout_url,
            )
            if (parts := urlsplit(url)).scheme in {"http", "https"} and parts.netloc
        }
    )
    connect_sources = " ".join(["'self'", *identity_origins])
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        f"connect-src {connect_sources}; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


def _serialize_diagnostics(diagnostics) -> dict[str, Any]:
    return {
        "contract_version": diagnostics.contract_version,
        "subject_hash": diagnostics.subject_hash,
        "tenant_id": diagnostics.tenant_id,
        "allowed_acl_labels": list(diagnostics.allowed_acl_labels),
        "security_model_kind": diagnostics.security_model_kind.value,
        "search_mode": diagnostics.search_mode.value,
        "retrieval": [
            {
                "chunk_id": item.chunk_id,
                "source_id": item.source_id,
                "score": item.score,
                "score_type": item.score_type,
                "rank": item.rank,
                "origin": item.origin,
                "graph_depth": item.graph_depth,
                "graph_path": list(item.graph_path),
            }
            for item in diagnostics.retrieval
        ],
        "omitted_chunk_ids": list(diagnostics.omitted_chunk_ids),
        "context_chunk_ids": list(diagnostics.context_chunk_ids),
        "context_characters": diagnostics.context_characters,
        "provider": diagnostics.provider,
        "model": diagnostics.model,
        "prompt_key": diagnostics.prompt_key,
        "model_transmission": {
            "checked": diagnostics.model_transmission.checked,
            "allowed": diagnostics.model_transmission.allowed,
            "reason_code": diagnostics.model_transmission.reason_code,
            "selected_model_server_id": (diagnostics.model_transmission.selected_model_server_id),
            "final_model_server_id": diagnostics.model_transmission.final_model_server_id,
            "rerouted": diagnostics.model_transmission.rerouted,
            "trust_boundary": (
                diagnostics.model_transmission.trust_boundary.value
                if diagnostics.model_transmission.trust_boundary is not None
                else None
            ),
            "external_transmission": (
                diagnostics.model_transmission.external_transmission.value
                if diagnostics.model_transmission.external_transmission is not None
                else None
            ),
            "context_security_requirement": _serialize_context_security_requirement(
                diagnostics.model_transmission.context_security_requirement
            ),
            "checked_chunk_ids": list(diagnostics.model_transmission.checked_chunk_ids),
            "blocked_chunk_ids": list(diagnostics.model_transmission.blocked_chunk_ids),
        },
        "system_prompt_hash": diagnostics.system_prompt_hash,
        "prompt_tokens": diagnostics.prompt_tokens,
        "completion_tokens": diagnostics.completion_tokens,
        "model_duration_ms": diagnostics.model_duration_ms,
        "pipeline_trace": list(diagnostics.pipeline_trace),
    }


def _serialize_context_security_requirement(requirement) -> dict[str, Any]:
    return {
        "security_model_kind": requirement.security_model_kind.value,
        "clearance_label": requirement.clearance_label,
        "classification_labels": list(requirement.classification_labels),
    }


def _serialize_retrieval_trace(diagnostics) -> dict[str, Any]:
    return {
        "contract_version": diagnostics.contract_version,
        "seed_nodes": [
            {
                "chunk_id": item.chunk_id,
                "source_id": item.source_id,
                "rank": item.rank,
                "metadata": {"origin": item.origin},
            }
            for item in diagnostics.retrieval
            if item.origin == "seed"
        ],
        "graph_nodes": [
            {
                "chunk_id": item.chunk_id,
                "source_id": item.source_id,
                "rank": item.rank,
                "metadata": {
                    "origin": item.origin,
                    "graph_depth": item.graph_depth,
                    "graph_path": list(item.graph_path),
                },
            }
            for item in diagnostics.retrieval
            if item.origin == "graph"
        ],
        "relationship_types": sorted(
            {
                relationship_type
                for item in diagnostics.retrieval
                for relationship_type in item.graph_path
            }
        ),
    }


def _parse_policy_bundle(
    tenant_id: str,
    payload: dict[str, Any],
) -> tuple[AccessPolicyBundle, int | None]:
    expected_version_value = payload.get("expected_version")
    if expected_version_value is not None and (
        isinstance(expected_version_value, bool) or not isinstance(expected_version_value, int)
    ):
        raise ValidationError("expected_version must be an integer or null")
    version = _required_positive_integer(payload, "version")
    groups = _required_string_list(payload, "groups")
    mappings = _required_object_list(payload, "claim_mappings")
    capabilities = _required_object_list(payload, "capabilities")
    grants = _required_object_list(payload, "grants")
    return (
        AccessPolicyBundle(
            tenant_id=tenant_id,
            version=version,
            group_ids=tuple(groups),
            claim_mappings=tuple(
                ClaimGroupMapping(
                    claim_name=str(item.get("claim_name") or ""),
                    claim_value=str(item.get("claim_value") or ""),
                    group_id=str(item.get("group_id") or ""),
                )
                for item in mappings
            ),
            capabilities=tuple(
                CapabilityDescriptor(
                    capability_id=str(item.get("capability_id") or ""),
                    display_name=str(item.get("display_name") or ""),
                    description=str(item.get("description") or ""),
                    domain=str(item.get("domain") or ""),
                    pipeline_name=str(item.get("pipeline_name") or ""),
                    diagnostics_available=_required_boolean(item, "diagnostics_available"),
                    external_model=_required_boolean(item, "external_model"),
                    policy_version=version,
                )
                for item in capabilities
            ),
            grants=tuple(
                GroupCapabilityGrant(
                    group_id=str(item.get("group_id") or ""),
                    capability_id=str(item.get("capability_id") or ""),
                )
                for item in grants
            ),
        ),
        expected_version_value,
    )


def _required_positive_integer(payload: dict[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{field} must be a positive integer")
    return value


def _required_string_list(payload: dict[str, Any], field: str) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValidationError(f"{field} must be a list of strings")
    return value


def _required_object_list(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = payload.get(field)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValidationError(f"{field} must be a list of objects")
    return value


def _required_boolean(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValidationError(f"{field} must be a boolean")
    return value
