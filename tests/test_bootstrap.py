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
    AdminSettings,
    EmbeddingSettings,
    IdentityProviderSettings,
    ModelSettings,
    PostgreSQLSettings,
    ServerSettings,
    SovereignFlowSettings,
    WeaviateSettings,
    WebClientSettings,
)
from sovereignflow.bootstrap.import_application import bootstrap_import
from sovereignflow.domain import (
    DependencyUnavailableError,
    DomainNotFoundError,
    DomainProfile,
    GraphDirection,
    GraphTraversalProfile,
    ModelGeneration,
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

    def generate(self, **kwargs) -> ModelGeneration:
        return ModelGeneration("answer", 1, 1, 0.0)

    def embed_query(self, text: str):
        return (0.1,)

    def embed_documents(self, texts):
        return tuple((0.1,) for _ in texts)


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
        self.checked = 0

    @property
    def name(self) -> str:
        return "execution_audit"

    def check(self) -> None:
        self.checked += 1


class MigrationRunner:
    def __init__(self, *args, **kwargs) -> None:
        self.migrated = 0

    def migrate(self) -> None:
        self.migrated += 1


class IngestionRepository(Healthy):
    name = "ingestion_repository"

    def check(self) -> None:
        self.checked += 1


class GraphTraversal(IngestionRepository):
    name = "graph_traversal"


class AccessPolicies(IngestionRepository):
    name = "access_policies"

    def resolve(self, authorization):
        raise AssertionError("not called")

    def capabilities(self, policy):
        return ()

    def capability(self, capability_id, *, policy):
        return None

    def publish(self, bundle, *, expected_version) -> None:
        return None


class SecurityDecisionAudit:
    def __init__(self, *args, **kwargs) -> None:
        return None

    def record(self, **values) -> None:
        return None


class CollectionMigrator:
    def __init__(self, client) -> None:
        self.client = client

    def ensure(self, collection_name: str) -> None:
        return None


class VectorIndex:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


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
            0.0,
            0.0,
        ),
        admin=AdminSettings("admin-secret"),
        identity_provider=IdentityProviderSettings(
            issuer="https://identity.test",
            audience="sovereignflow",
            jwks_url="https://identity.test/jwks",
            algorithms=("RS256",),
            timeout_seconds=5,
            cache_ttl_seconds=300,
            tenant_claim="tenant_id",
            roles_claim="roles",
            groups_claim="groups",
            acl_claim="acl_labels",
            classification_claim="max_classification_level",
            external_model_claim="allow_external_model",
            diagnostic_claim="sovereignflow_diagnostics",
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
                GraphTraversalProfile(False, 1, 1, GraphDirection.BOTH),
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
        "sovereignflow.bootstrap.application.PostgreSQLMigrationRunner",
        MigrationRunner,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLIngestionRepository",
        IngestionRepository,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLGraphTraversal",
        GraphTraversal,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLAccessPolicyRepository",
        AccessPolicies,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLSecurityDecisionAudit",
        SecurityDecisionAudit,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.WeaviateCollectionMigrator",
        CollectionMigrator,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.WeaviateVectorIndex",
        VectorIndex,
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

    configured = settings(tmp_path)
    configured = SovereignFlowSettings(
        **{
            **configured.__dict__,
            "web_client": WebClientSettings(
                client_id="web-client",
                authorization_url="https://identity.test/authorize",
                token_url="https://identity.test/token",
                logout_url="https://identity.test/logout",
            ),
        }
    )
    application = bootstrap(configured)

    assert application.app.test_client().get("/live").status_code == 200
    assert application.app.test_client().get("/app/").status_code == 200
    assert set(application.ingestion_services) == {"general"}
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
        "sovereignflow.bootstrap.application.PostgreSQLMigrationRunner",
        MigrationRunner,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLIngestionRepository",
        IngestionRepository,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLGraphTraversal",
        GraphTraversal,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLAccessPolicyRepository",
        AccessPolicies,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.PostgreSQLSecurityDecisionAudit",
        SecurityDecisionAudit,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.WeaviateCollectionMigrator",
        CollectionMigrator,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.application.WeaviateVectorIndex",
        VectorIndex,
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


def test_import_bootstrap_builds_neutral_import_service(monkeypatch, tmp_path) -> None:
    client = Client()
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application._connect_weaviate",
        lambda value: client,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.PostgreSQLMigrationRunner",
        MigrationRunner,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.PostgreSQLIngestionRepository",
        IngestionRepository,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.OpenAIEmbeddingGateway",
        Healthy,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.WeaviateCollectionMigrator",
        CollectionMigrator,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.WeaviateVectorIndex",
        VectorIndex,
    )

    application = bootstrap_import(settings(tmp_path), domain_name="general")

    assert application.service is not None
    application.close()
    assert client.closed == 1


def test_import_bootstrap_rejects_unknown_domain_and_closes_on_failure(
    monkeypatch,
    tmp_path,
) -> None:
    with pytest.raises(DomainNotFoundError):
        bootstrap_import(settings(tmp_path), domain_name="missing")

    client = Client()
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application._connect_weaviate",
        lambda value: client,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.PostgreSQLMigrationRunner",
        MigrationRunner,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.PostgreSQLIngestionRepository",
        IngestionRepository,
    )
    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.OpenAIEmbeddingGateway",
        Healthy,
    )

    class BrokenMigrator(CollectionMigrator):
        def ensure(self, collection_name: str) -> None:
            raise RuntimeError("broken")

    monkeypatch.setattr(
        "sovereignflow.bootstrap.import_application.WeaviateCollectionMigrator",
        BrokenMigrator,
    )
    with pytest.raises(RuntimeError, match="broken"):
        bootstrap_import(settings(tmp_path), domain_name="general")
    assert client.closed == 1


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
