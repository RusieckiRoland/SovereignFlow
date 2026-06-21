from __future__ import annotations

from dataclasses import dataclass

import pytest
from conftest import (
    StubAuthenticator,
    StubModel,
    StubOperations,
    StubPrompts,
    StubRetrieval,
    build_query_service,
)

from sovereignflow.application import (
    PipelineAuthorizationService,
    PolicyAdministrationService,
)
from sovereignflow.domain import (
    CapabilityDescriptor,
    DependencyUnavailableError,
    QueryCommand,
    QueryResult,
    ResolvedAccessPolicy,
    ValidationError,
)
from sovereignflow.interfaces import QueryDispatcher, WebClientConfiguration, create_app


@dataclass
class Probe:
    name: str
    healthy: bool = True

    def check(self) -> None:
        if not self.healthy:
            raise DependencyUnavailableError("down")


def application(domain_profile, search_hit):
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        model=StubModel(answer="API answer."),
        prompts=StubPrompts(),
    )
    dispatcher = QueryDispatcher({"general": service})
    return create_app(
        dispatcher,
        (Probe("postgresql"), Probe("weaviate")),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(),
    )


def test_liveness_readiness_and_query_contract(domain_profile, search_hit) -> None:
    app = application(domain_profile, search_hit)
    client = app.test_client()
    dispatcher = QueryDispatcher({})

    assert client.get("/live").get_json() == {"ok": True}
    assert dispatcher.domains == ()
    assert client.get("/ready").get_json() == {
        "ok": True,
        "components": {"postgresql": "ready", "weaviate": "ready"},
    }
    response = client.post(
        "/v1/query",
        headers={"X-Request-ID": "request-1", "Authorization": "Bearer token"},
        json={
            "query": "question",
            "domain": "general",
            "session_id": "session-1",
            "filters": {"country": "PL"},
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["request_id"] == "request-1"
    assert body["answer"].startswith("API answer.")
    assert body["citations"][0]["score_type"] == "hybrid"
    invalid_conversation = client.post(
        "/v1/query",
        headers={"X-Request-ID": "request-2", "Authorization": "Bearer token"},
        json={
            "query": "question",
            "domain": "general",
            "session_id": "session-1",
            "conversation_id": "",
        },
    )
    accepted_conversation = client.post(
        "/v1/query",
        headers={"X-Request-ID": "request-3", "Authorization": "Bearer token"},
        json={
            "query": "question",
            "domain": "general",
            "session_id": "session-1",
            "conversation_id": " conversation-1 ",
        },
    )
    assert invalid_conversation.status_code == 400
    assert accepted_conversation.status_code == 200


def test_optional_web_client_exposes_oidc_console_and_security_headers(
    domain_profile,
    search_hit,
) -> None:
    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        model=StubModel(answer="API answer."),
        prompts=StubPrompts(),
    )
    app = create_app(
        QueryDispatcher({"general": service}),
        (),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(),
        WebClientConfiguration(
            client_id="web-client",
            authorization_url="https://identity.test/authorize",
            token_url="https://identity.test/token",
            logout_url="https://identity.test/logout",
        ),
    )
    client = app.test_client()

    assert client.get("/").headers["Location"] == "/app/"
    assert client.get("/app").headers["Location"] == "/app/"
    index = client.get("/app/")
    configuration = client.get("/app/config.json")
    script = client.get("/app/assets/app.js")

    assert index.status_code == 200
    assert "SovereignFlow Test Console" in index.get_data(as_text=True)
    assert configuration.get_json() == {
        "api_url": "/v1/query",
        "client_id": "web-client",
        "authorization_url": "https://identity.test/authorize",
        "token_url": "https://identity.test/token",
        "logout_url": "https://identity.test/logout",
        "domains": ["general"],
    }
    assert configuration.headers["Cache-Control"] == "no-store"
    assert "connect-src 'self' https://identity.test" in index.headers["Content-Security-Policy"]
    assert index.headers["X-Frame-Options"] == "DENY"
    assert script.status_code == 200
    assert "code_challenge_method" in script.get_data(as_text=True)


def test_web_client_routes_are_absent_without_configuration() -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret", StubAuthenticator())

    assert app.test_client().get("/app/").status_code == 404


def test_catalog_and_query_use_same_fail_closed_capability_policy(
    domain_profile,
    search_hit,
) -> None:
    selected = CapabilityDescriptor(
        "general-query",
        "General query",
        "",
        "general",
        "default",
        True,
        False,
        1,
    )

    class Policies:
        def resolve(self, authorization):
            return ResolvedAccessPolicy(
                authorization.subject,
                authorization.tenant_id,
                authorization.groups,
                ("general-query",),
                ("default",),
                1,
            )

        def capabilities(self, policy):
            return (selected,)

        def capability(self, capability_id, *, policy):
            return selected if capability_id == selected.capability_id else None

    class DecisionAudit:
        def record(self, **values):
            return None

    service = build_query_service(
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        model=StubModel(answer="API answer."),
        prompts=StubPrompts(),
    )
    app = create_app(
        QueryDispatcher(
            {"general": service},
            PipelineAuthorizationService(Policies(), DecisionAudit()),
        ),
        (),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(),
    )
    client = app.test_client()

    catalog = client.get("/v1/catalog", headers={"Authorization": "Bearer token"})
    allowed = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer token"},
        json={
            "capability_id": "general-query",
            "query": "question",
            "session_id": "session",
        },
    )
    manipulated = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer token"},
        json={
            "capability_id": "general-query",
            "domain": "general",
            "query": "question",
            "session_id": "session",
        },
    )

    assert catalog.get_json()["capabilities"][0]["capability_id"] == "general-query"
    assert allowed.status_code == 200
    assert manipulated.status_code == 400


def test_authorized_capability_selects_pipeline_service_within_same_domain() -> None:
    selected = CapabilityDescriptor(
        "graph-query",
        "Graph query",
        "",
        "general",
        "graph",
        True,
        False,
        1,
    )

    class Policies:
        def resolve(self, authorization):
            return ResolvedAccessPolicy(
                authorization.subject,
                authorization.tenant_id,
                authorization.groups,
                ("graph-query",),
                ("graph",),
                1,
            )

        def capability(self, capability_id, *, policy):
            return selected if capability_id == selected.capability_id else None

    class DecisionAudit:
        def record(self, **values):
            return None

    class Service:
        def __init__(self, pipeline_name: str) -> None:
            self.pipeline_name = pipeline_name

        def execute(self, command):
            return QueryResult(
                command.request_id,
                self.pipeline_name,
                command.domain,
                command.session_id,
                (),
                (self.pipeline_name,),
            )

    authorization = StubAuthenticator().context
    dispatcher = QueryDispatcher(
        {
            ("general", "direct"): Service("direct"),
            ("general", "graph"): Service("graph"),
        },
        PipelineAuthorizationService(Policies(), DecisionAudit()),
        default_pipelines={"general": "direct"},
    )

    result = dispatcher.execute(
        QueryCommand("request", "question", "ignored", "session", authorization),
        capability_id="graph-query",
    )

    assert dispatcher.domains == ("general",)
    assert result.answer == "graph"
    assert result.pipeline_trace == ("graph",)


def test_readiness_reports_each_unavailable_component() -> None:
    app = create_app(
        QueryDispatcher({}),
        (Probe("ok"), Probe("down", False)),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(),
    )

    response = app.test_client().get("/ready")

    assert response.status_code == 503
    assert response.get_json() == {
        "ok": False,
        "components": {"ok": "ready", "down": "unavailable"},
    }


@pytest.mark.parametrize(
    ("kwargs", "code", "status"),
    [
        ({"data": "not-json", "content_type": "text/plain"}, "validation_error", 400),
        ({"json": []}, "validation_error", 400),
        (
            {
                "json": {
                    "query": "q",
                    "domain": "general",
                    "session_id": "s",
                    "filters": [],
                }
            },
            "validation_error",
            400,
        ),
        (
            {"json": {"query": "q", "domain": "missing", "session_id": "s"}},
            "domain_not_found",
            404,
        ),
    ],
)
def test_api_returns_stable_known_errors(kwargs, code: str, status: int) -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret", StubAuthenticator())

    response = app.test_client().post(
        "/v1/query",
        headers={"X-Request-ID": "known-request", "Authorization": "Bearer token"},
        **kwargs,
    )

    assert response.status_code == status
    assert response.get_json()["error"] == {
        "code": code,
        "message": response.get_json()["error"]["message"],
        "request_id": "known-request",
    }


class BrokenDispatcher:
    domains = ()

    def execute(self, command):
        raise RuntimeError("secret internal detail")


def test_api_hides_unexpected_error_details() -> None:
    app = create_app(
        BrokenDispatcher(),  # type: ignore[arg-type]
        (),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(),
    )

    response = app.test_client().post(
        "/v1/query",
        headers={"X-Request-ID": "unexpected-request", "Authorization": "Bearer token"},
        json={"query": "q", "domain": "d", "session_id": "s"},
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == {
        "code": "internal_error",
        "message": "The request could not be completed.",
        "request_id": "unexpected-request",
    }
    assert "secret" not in response.get_data(as_text=True)


def test_api_preserves_standard_http_errors_without_internal_trace() -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret", StubAuthenticator())

    method_not_allowed = app.test_client().get(
        "/v1/query",
        headers={"X-Request-ID": "method-request"},
    )
    missing_route = app.test_client().get(
        "/missing",
        headers={"X-Request-ID": "missing-request"},
    )

    assert method_not_allowed.status_code == 405
    assert method_not_allowed.get_json()["error"]["code"] == "method_not_allowed"
    assert method_not_allowed.get_json()["error"]["request_id"] == "method-request"
    assert missing_route.status_code == 404
    assert missing_route.get_json()["error"]["code"] == "not_found"
    assert missing_route.get_json()["error"]["request_id"] == "missing-request"


def test_request_id_is_generated_when_header_is_missing() -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret", StubAuthenticator())

    response = app.test_client().post(
        "/v1/query",
        headers={"Authorization": "Bearer token"},
        json={"query": "q", "domain": "missing", "session_id": "s"},
    )

    assert response.get_json()["error"]["request_id"]


def test_query_requires_bearer_token_and_rejects_body_security_context() -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret", StubAuthenticator())
    client = app.test_client()
    missing = client.post(
        "/v1/query",
        json={"query": "q", "domain": "missing", "session_id": "s"},
    )
    manipulated = client.post(
        "/v1/query",
        headers={"Authorization": "Bearer token"},
        json={
            "query": "q",
            "domain": "missing",
            "session_id": "s",
            "tenant_id": "tenant-b",
        },
    )

    assert missing.status_code == 401
    assert missing.get_json()["error"]["code"] == "authentication_error"
    assert manipulated.status_code == 400
    assert manipulated.get_json()["error"]["code"] == "validation_error"


def test_protected_diagnostics_are_serialized(domain_profile, search_hit) -> None:
    app = application(domain_profile, search_hit)
    response = app.test_client().post(
        "/v1/query",
        headers={
            "Authorization": "Bearer token",
            "X-SovereignFlow-Diagnostics": "true",
        },
        json={"query": "q", "domain": "general", "session_id": "s"},
    )

    payload = response.get_json()
    diagnostics = payload["diagnostics"]
    assert diagnostics["contract_version"] == "1.0"
    assert diagnostics["tenant_id"] == "tenant-a"
    assert diagnostics["retrieval"][0]["chunk_id"] == "chunk-1"
    assert diagnostics["context_chunk_ids"] == ["chunk-1"]
    assert diagnostics["provider"] == "stub-provider"
    assert diagnostics["model"] == "stub-model"
    assert diagnostics["prompt_key"] == "answer"
    assert diagnostics["model_transmission"] == {
        "checked": True,
        "allowed": True,
        "reason_code": "model_server_allowed",
        "selected_model_server_id": "default-model",
        "final_model_server_id": "default-model",
        "rerouted": False,
        "trust_boundary": "internal",
        "external_transmission": "allowed",
        "context_security_requirement": {
            "security_model_kind": "clearance_level",
            "clearance_label": "PUBLIC",
            "classification_labels": [],
        },
        "checked_chunk_ids": ["chunk-1"],
        "blocked_chunk_ids": [],
    }
    assert diagnostics["pipeline_trace"][-1] == "finalize"
    assert payload["retrieval_trace"]["seed_nodes"][0]["chunk_id"] == "chunk-1"
    assert payload["retrieval_trace"]["graph_nodes"] == []
    assert payload["usage"]["total_tokens"] == 15


def test_create_app_requires_admin_secret() -> None:
    with pytest.raises(ValidationError, match="admin_api_key"):
        create_app(QueryDispatcher({}), (), StubOperations(), "", StubAuthenticator())


def test_admin_endpoints_require_authentication_and_tenant() -> None:
    class ValidatingOperations(StubOperations):
        def metrics(self, *, tenant_id: str, hours: int):
            if not tenant_id:
                raise ValidationError("tenant_id is required")
            return super().metrics(tenant_id=tenant_id, hours=hours)

    app = create_app(
        QueryDispatcher({}),
        (),
        ValidatingOperations(),
        "admin-secret",
        StubAuthenticator(),
    )
    client = app.test_client()

    unauthorized = client.get("/v1/admin/metrics?tenant_id=tenant-a")
    missing_tenant = client.get(
        "/v1/admin/metrics",
        headers={"X-SovereignFlow-Admin-Key": "admin-secret"},
    )

    assert unauthorized.status_code == 401
    assert unauthorized.get_json()["error"]["code"] == "authentication_error"
    assert missing_tenant.status_code == 400
    assert missing_tenant.get_json()["error"]["code"] == "validation_error"


def test_admin_endpoints_expose_operations_contract() -> None:
    class Operations(StubOperations):
        def execution(self, request_id: str, *, tenant_id: str):
            return {"request_id": request_id, "tenant_id": tenant_id}

    app = create_app(QueryDispatcher({}), (), Operations(), "admin-secret", StubAuthenticator())
    client = app.test_client()
    headers = {"X-SovereignFlow-Admin-Key": "admin-secret"}

    execution = client.get(
        "/v1/admin/executions/request-1?tenant_id=tenant-a",
        headers=headers,
    )
    metrics = client.get(
        "/v1/admin/metrics?tenant_id=tenant-a&hours=12",
        headers=headers,
    )
    job = client.get(
        "/v1/admin/ingestion/jobs/job-1?tenant_id=tenant-a",
        headers=headers,
    )
    retry = client.post(
        "/v1/admin/ingestion/jobs/job-1/retry?tenant_id=tenant-a",
        headers=headers,
    )

    assert execution.get_json()["execution"]["request_id"] == "request-1"
    assert metrics.get_json()["metrics"]["window_hours"] == 12
    assert job.get_json()["job"]["job_id"] == "job-1"
    assert retry.get_json()["job"]["status"] == "completed"


def test_admin_metrics_rejects_non_integer_window() -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret", StubAuthenticator())
    response = app.test_client().get(
        "/v1/admin/metrics?tenant_id=tenant-a&hours=invalid",
        headers={"X-SovereignFlow-Admin-Key": "admin-secret"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_admin_execution_returns_explicit_missing_result() -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret", StubAuthenticator())
    response = app.test_client().get(
        "/v1/admin/executions/missing?tenant_id=tenant-a",
        headers={"X-SovereignFlow-Admin-Key": "admin-secret"},
    )

    assert response.get_json() == {"ok": True, "execution": None}


def test_admin_can_publish_transactional_access_policy() -> None:
    class Repository:
        def __init__(self) -> None:
            self.calls = []

        def publish(self, bundle, *, expected_version) -> None:
            self.calls.append((bundle, expected_version))

    repository = Repository()
    administration = PolicyAdministrationService(
        repository,
        domain_pipelines={"general": "default"},
    )
    app = create_app(
        QueryDispatcher({}),
        (),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(),
        policy_administration=administration,
    )
    response = app.test_client().put(
        "/v1/admin/access-policies/tenant-a",
        headers={"X-SovereignFlow-Admin-Key": "admin-secret"},
        json={
            "expected_version": 1,
            "version": 2,
            "groups": ["readers"],
            "claim_mappings": [
                {
                    "claim_name": "groups",
                    "claim_value": "identity-readers",
                    "group_id": "readers",
                }
            ],
            "capabilities": [
                {
                    "capability_id": "general-query",
                    "display_name": "General query",
                    "description": "General RAG",
                    "domain": "general",
                    "pipeline_name": "default",
                    "diagnostics_available": True,
                    "external_model": False,
                }
            ],
            "grants": [
                {
                    "group_id": "readers",
                    "capability_id": "general-query",
                }
            ],
        },
    )

    assert response.get_json() == {
        "ok": True,
        "tenant_id": "tenant-a",
        "policy_version": 2,
    }
    published, expected_version = repository.calls[0]
    assert published.capabilities[0].capability_id == "general-query"
    assert expected_version == 1


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "JSON object"),
        ({"expected_version": True}, "expected_version"),
        ({"version": 0}, "version"),
        ({"version": 1, "groups": "readers"}, "groups"),
        (
            {"version": 1, "groups": [], "claim_mappings": "invalid"},
            "claim_mappings",
        ),
        (
            {
                "version": 1,
                "groups": [],
                "claim_mappings": [],
                "capabilities": [],
                "grants": "invalid",
            },
            "grants",
        ),
        (
            {
                "version": 1,
                "groups": [],
                "claim_mappings": [],
                "capabilities": [
                    {
                        "capability_id": "id",
                        "display_name": "name",
                        "domain": "general",
                        "pipeline_name": "default",
                        "diagnostics_available": "yes",
                        "external_model": False,
                    }
                ],
                "grants": [],
            },
            "diagnostics_available",
        ),
    ],
)
def test_policy_publication_rejects_invalid_contract(payload, message: str) -> None:
    administration = PolicyAdministrationService(
        type("Repository", (), {"publish": lambda *args, **kwargs: None})(),
        domain_pipelines={"general": "default"},
    )
    app = create_app(
        QueryDispatcher({}),
        (),
        StubOperations(),
        "admin-secret",
        StubAuthenticator(),
        policy_administration=administration,
    )

    response = app.test_client().put(
        "/v1/admin/access-policies/tenant-a",
        headers={"X-SovereignFlow-Admin-Key": "admin-secret"},
        json=payload,
    )

    assert response.status_code == 400
    assert message in response.get_json()["error"]["message"]
