from __future__ import annotations

import pytest

from sovereignflow.domain import (
    DependencyUnavailableError,
    ProviderProtocolError,
    ValidationError,
)
from sovereignflow.infrastructure.http_gateways import (
    EmbeddingEndpoint,
    ModelEndpoint,
    OpenAIEmbeddingGateway,
    OpenAIModelGateway,
    _JsonHttpClient,
)


def base_url(server) -> str:
    return f"http://127.0.0.1:{server.server_port}/v1"


@pytest.mark.integration
def test_gateways_use_real_openai_compatible_http_protocol(http_server) -> None:
    http_server.responses[("GET", "/v1/models")] = (
        200,
        {"data": [{"id": "model"}]},
        "application/json",
    )
    http_server.responses[("POST", "/v1/chat/completions")] = (
        200,
        {"choices": [{"message": {"content": " grounded "}}]},
        "application/json",
    )
    http_server.responses[("POST", "/v1/embeddings")] = (
        200,
        {"data": [{"embedding": [1, 2.5]}]},
        "application/json",
    )
    model = OpenAIModelGateway(
        ModelEndpoint("model", "local", base_url(http_server), "chat", "secret", 2)
    )
    embeddings = OpenAIEmbeddingGateway(
        EmbeddingEndpoint("embed", base_url(http_server), "vectors", "secret", 2)
    )

    assert model.scope == "local"
    model.healthcheck()
    embeddings.healthcheck()
    answer = model.generate(system_prompt="system", user_prompt="question")
    vector = embeddings.embed_query("query")

    assert answer == "grounded"
    assert vector == (1.0, 2.5)
    posts = [item for item in http_server.requests if item[0] == "POST"]
    assert posts[0][2]["Authorization"] == "Bearer secret"
    assert posts[0][3]["model"] == "chat"
    assert posts[1][3] == {"model": "vectors", "input": "query"}


def test_endpoints_validate_scope_and_timeout() -> None:
    with pytest.raises(ValidationError, match="scope"):
        ModelEndpoint("model", "unknown", "http://x", "m", "", 1)
    with pytest.raises(ValidationError, match="timeout"):
        ModelEndpoint("model", "local", "http://x", "m", "", 0)
    with pytest.raises(ValidationError, match="timeout"):
        EmbeddingEndpoint("embed", "http://x", "m", "", 0)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("response", "error"),
    [
        ((503, {"error": "down"}, "application/json"), DependencyUnavailableError),
        ((200, b"not-json", "text/plain"), ProviderProtocolError),
        ((200, [1, 2], "application/json"), ProviderProtocolError),
    ],
)
def test_json_http_client_maps_transport_and_protocol_errors(
    http_server,
    response,
    error,
) -> None:
    http_server.responses[("GET", "/test")] = response

    with pytest.raises(error):
        _JsonHttpClient().get(
            url=f"http://127.0.0.1:{http_server.server_port}/test",
            api_key="",
            timeout_seconds=1,
        )


def test_json_http_client_maps_unreachable_endpoint() -> None:
    with pytest.raises(DependencyUnavailableError, match="unavailable"):
        _JsonHttpClient().get(
            url="http://127.0.0.1:1/unreachable",
            api_key="",
            timeout_seconds=0.01,
        )


class TimeoutClient:
    def get(self, **kwargs):
        raise TimeoutError


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "body", "method", "error"),
    [
        (
            "/v1/chat/completions",
            {"choices": []},
            "model",
            "invalid schema",
        ),
        (
            "/v1/chat/completions",
            {"choices": [{"message": {"content": " "}}]},
            "model",
            "empty",
        ),
        (
            "/v1/embeddings",
            {"data": []},
            "embedding",
            "invalid schema",
        ),
        (
            "/v1/embeddings",
            {"data": [{"embedding": []}]},
            "embedding",
            "no vector",
        ),
        (
            "/v1/embeddings",
            {"data": [{"embedding": ["bad"]}]},
            "embedding",
            "must contain numbers",
        ),
    ],
)
def test_gateways_reject_invalid_provider_schemas(
    http_server,
    path: str,
    body,
    method: str,
    error: str,
) -> None:
    http_server.responses[("POST", path)] = (200, body, "application/json")
    if method == "model":
        gateway = OpenAIModelGateway(ModelEndpoint("m", "local", base_url(http_server), "m", "", 1))
        with pytest.raises(ProviderProtocolError, match=error):
            gateway.generate(system_prompt="s", user_prompt="u")
    else:
        gateway = OpenAIEmbeddingGateway(EmbeddingEndpoint("e", base_url(http_server), "e", "", 1))
        with pytest.raises(ProviderProtocolError, match=error):
            gateway.embed_query("q")
