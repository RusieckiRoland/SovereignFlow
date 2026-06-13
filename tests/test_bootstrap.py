from __future__ import annotations

import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import StubAudit, default_pipeline

from sovereignflow.bootstrap.application import (
    _connect_weaviate,
    _GatewayHealthProbe,
    bootstrap,
)
from sovereignflow.bootstrap.config import (
    EmbeddingSettings,
    ModelSettings,
    PostgreSQLSettings,
    ServerSettings,
    SovereignFlowSettings,
    WeaviateSettings,
)
from sovereignflow.domain import (
    DependencyUnavailableError,
    DomainProfile,
    RetrievalProfile,
    SearchMode,
)


class Client:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class Healthy:
    def __init__(self, *args, **kwargs) -> None:
        self.scope = getattr(args[0], "scope", "local") if args else "local"
        self.checked = 0

    def healthcheck(self) -> None:
        self.checked += 1

    def generate(self, **kwargs) -> str:
        return "answer"

    def embed_query(self, text: str):
        return (0.1,)


class Retrieval(Healthy):
    def search(self, request):
        return ()


class Prompts:
    def __init__(self, root) -> None:
        self.root = root

    def load(self, name: str) -> str:
        return "prompt"


class Pipelines:
    def __init__(self, root) -> None:
        self.root = root

    def load(self, name: str):
        return default_pipeline()


class Audit(StubAudit):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__()
        self.migrated = 0
        self.checked = 0

    @property
    def name(self) -> str:
        return "execution_audit"

    def migrate(self) -> None:
        self.migrated += 1

    def check(self) -> None:
        self.checked += 1


def settings(tmp_path: Path) -> SovereignFlowSettings:
    return SovereignFlowSettings(
        config_path=tmp_path / "config.yaml",
        server=ServerSettings("127.0.0.1", 8000, 4),
        postgresql=PostgreSQLSettings("postgresql://test", 5),
        weaviate=WeaviateSettings("localhost", 8080, 50051, False, "secret"),
        embeddings=EmbeddingSettings("embed", "http://embed/v1", "e", "", 5),
        selected_model=ModelSettings(
            "model",
            "local",
            "http://model/v1",
            "m",
            "",
            5,
        ),
        prompts_root=tmp_path,
        pipelines_root=tmp_path,
        domains=(
            DomainProfile(
                "general",
                "",
                "General",
                "tenant",
                "answer",
                False,
                RetrievalProfile(SearchMode.BM25, 1, 100),
            ),
        ),
    )


def test_bootstrap_builds_and_validates_complete_application(monkeypatch, tmp_path) -> None:
    client = Client()
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application._connect_weaviate",
        lambda value: client,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.OpenAIEmbeddingGateway",
        Healthy,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.OpenAIModelGateway",
        Healthy,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.FilePromptRepository",
        Prompts,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.YamlPipelineRepository",
        Pipelines,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLExecutionAudit",
        Audit,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.WeaviateRetrievalAdapter",
        Retrieval,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.WeaviateHealthProbe",
        lambda value: _GatewayHealthProbe("weaviate", Healthy()),
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLHealthProbe",
        lambda *args, **kwargs: _GatewayHealthProbe("postgresql", Healthy()),
    )

    application = bootstrap(settings(tmp_path))

    assert application.app.test_client().get("/live").status_code == 200
    application.close()
    assert client.closed == 1


def test_bootstrap_closes_client_when_construction_fails(monkeypatch, tmp_path) -> None:
    client = Client()
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application._connect_weaviate",
        lambda value: client,
    )

    class Broken(Healthy):
        def healthcheck(self) -> None:
            raise DependencyUnavailableError("down")

    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.OpenAIEmbeddingGateway",
        Healthy,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.OpenAIModelGateway",
        Healthy,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.FilePromptRepository",
        Prompts,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.YamlPipelineRepository",
        Pipelines,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLExecutionAudit",
        Audit,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.WeaviateRetrievalAdapter",
        Broken,
    )

    with pytest.raises(DependencyUnavailableError):
        bootstrap(settings(tmp_path))
    assert client.closed == 1


def test_gateway_health_probe_delegates() -> None:
    gateway = Healthy()
    probe = _GatewayHealthProbe("gateway", gateway)

    probe.check()

    assert probe.name == "gateway"
    assert gateway.checked == 1


def test_connect_weaviate_uses_authenticated_custom_connection(
    monkeypatch,
    tmp_path,
) -> None:
    calls = []
    module = SimpleNamespace(
        connect_to_custom=lambda **kwargs: calls.append(kwargs) or "client",
    )
    init_module = SimpleNamespace(Auth=SimpleNamespace(api_key=lambda value: f"auth:{value}"))
    monkeypatch.setitem(sys.modules, "weaviate", module)
    monkeypatch.setitem(sys.modules, "weaviate.classes.init", init_module)

    result = _connect_weaviate(settings(tmp_path))

    assert result == "client"
    assert calls[0]["auth_credentials"] == "auth:secret"


def test_connect_weaviate_maps_missing_sdk_and_connection_failure(
    monkeypatch,
    tmp_path,
) -> None:
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "weaviate":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "weaviate", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(DependencyUnavailableError, match="not installed"):
        _connect_weaviate(settings(tmp_path))

    monkeypatch.setattr(builtins, "__import__", real_import)
    module = SimpleNamespace(
        connect_to_custom=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("down"))
    )
    monkeypatch.setitem(sys.modules, "weaviate", module)
    monkeypatch.setitem(
        sys.modules,
        "weaviate.classes.init",
        SimpleNamespace(Auth=SimpleNamespace(api_key=lambda value: value)),
    )
    with pytest.raises(DependencyUnavailableError, match="Cannot connect"):
        _connect_weaviate(settings(tmp_path))
