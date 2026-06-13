from __future__ import annotations

from dataclasses import dataclass

import pytest
from conftest import StubModel, StubPrompts, StubRetrieval

from sovereignflow.application import RagQueryService
from sovereignflow.domain import DependencyUnavailableError
from sovereignflow.interfaces import QueryDispatcher, create_app


@dataclass
class Probe:
    name: str
    healthy: bool = True

    def check(self) -> None:
        if not self.healthy:
            raise DependencyUnavailableError("down")


def application(domain_profile, search_hit):
    service = RagQueryService(
        domain=domain_profile,
        retrieval=StubRetrieval((search_hit,)),
        model=StubModel(answer="API answer."),
        prompts=StubPrompts(),
    )
    dispatcher = QueryDispatcher({"general": service})
    return create_app(dispatcher, (Probe("postgresql"), Probe("weaviate")))


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
    app = create_app(QueryDispatcher({}), (Probe("ok"), Probe("down", False)))

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
    app = create_app(QueryDispatcher({}), ())

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
    app = create_app(BrokenDispatcher(), ())  # type: ignore[arg-type]

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
    app = create_app(QueryDispatcher({}), ())

    response = app.test_client().post(
        "/v1/query",
        json={"query": "q", "domain": "missing", "session_id": "s"},
    )

    assert response.get_json()["error"]["request_id"]
