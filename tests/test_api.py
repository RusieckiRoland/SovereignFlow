from __future__ import annotations

from dataclasses import dataclass

import pytest
from conftest import (
    StubModel,
    StubOperations,
    StubPrompts,
    StubRetrieval,
    build_query_service,
)

from sovereignflow.domain import DependencyUnavailableError, ValidationError
from sovereignflow.interfaces import QueryDispatcher, create_app


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
        headers={"X-Request-ID": "request-1"},
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


def test_readiness_reports_each_unavailable_component() -> None:
    app = create_app(
        QueryDispatcher({}),
        (Probe("ok"), Probe("down", False)),
        StubOperations(),
        "admin-secret",
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
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret")

    response = app.test_client().post(
        "/v1/query",
        headers={"X-Request-ID": "known-request"},
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
    )

    response = app.test_client().post(
        "/v1/query",
        headers={"X-Request-ID": "unexpected-request"},
        json={"query": "q", "domain": "d", "session_id": "s"},
    )

    assert response.status_code == 500
    assert response.get_json()["error"] == {
        "code": "internal_error",
        "message": "The request could not be completed.",
        "request_id": "unexpected-request",
    }
    assert "secret" not in response.get_data(as_text=True)


def test_request_id_is_generated_when_header_is_missing() -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret")

    response = app.test_client().post(
        "/v1/query",
        json={"query": "q", "domain": "missing", "session_id": "s"},
    )

    assert response.get_json()["error"]["request_id"]


def test_create_app_requires_admin_secret() -> None:
    with pytest.raises(ValidationError, match="admin_api_key"):
        create_app(QueryDispatcher({}), (), StubOperations(), "")


def test_admin_endpoints_require_authentication_and_tenant() -> None:
    class ValidatingOperations(StubOperations):
        def metrics(self, *, tenant_id: str, hours: int):
            if not tenant_id:
                raise ValidationError("tenant_id is required")
            return super().metrics(tenant_id=tenant_id, hours=hours)

    app = create_app(QueryDispatcher({}), (), ValidatingOperations(), "admin-secret")
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

    app = create_app(QueryDispatcher({}), (), Operations(), "admin-secret")
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
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret")
    response = app.test_client().get(
        "/v1/admin/metrics?tenant_id=tenant-a&hours=invalid",
        headers={"X-SovereignFlow-Admin-Key": "admin-secret"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_admin_execution_returns_explicit_missing_result() -> None:
    app = create_app(QueryDispatcher({}), (), StubOperations(), "admin-secret")
    response = app.test_client().get(
        "/v1/admin/executions/missing?tenant_id=tenant-a",
        headers={"X-SovereignFlow-Admin-Key": "admin-secret"},
    )

    assert response.get_json() == {"ok": True, "execution": None}
