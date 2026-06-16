from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sovereignflow.bootstrap.config import load_settings
from sovereignflow.domain import ConfigurationError, SearchMode


def valid_files(tmp_path: Path) -> tuple[Path, dict]:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    pipelines = tmp_path / "pipelines"
    pipelines.mkdir()
    (prompts / "answer.txt").write_text("Use evidence.", encoding="utf-8")
    domain = tmp_path / "domain.yaml"
    domain.write_text(
        yaml.safe_dump(
            {
                "name": "general",
                "description": "General",
                "collection": "General",
                "tenant_id": "tenant-a",
                "prompt_name": "answer",
                "pipeline_name": "default",
                "allowed_pipeline_names": ["direct", "graph", "strict"],
                "allow_external_model": False,
                "disclaimer": "Verify.",
                "security": {
                    "acl": {"enabled": True, "allowed_labels": ["public"]},
                    "require_travel_permission": True,
                    "security_model": {
                        "kind": "clearance_level",
                        "clearance_level": {"levels": {"PUBLIC": 0, "INTERNAL": 10}},
                    },
                },
                "retrieval": {
                    "mode": "hybrid",
                    "top_k": 3,
                    "max_context_characters": 1000,
                    "filters": {"status": "active"},
                    "allowed_filter_fields": ["country", "status"],
                },
                "graph": {
                    "enabled": True,
                    "max_depth": 2,
                    "max_nodes": 20,
                    "direction": "both",
                    "relationship_types": ["references"],
                },
            }
        ),
        encoding="utf-8",
    )
    config = {
        "server": {"host": "127.0.0.1", "port": 8000, "threads": 4},
        "postgresql": {
            "connection_url_env": "TEST_POSTGRES_URL",
            "timeout_seconds": 5,
        },
        "model_servers": [
            {
                "id": "default-model",
                "trust_boundary": "internal",
                "base_url": "http://localhost:8080/v1",
                "model": "chat",
                "timeout_seconds": 30,
                "input_cost_per_million": 1.5,
                "output_cost_per_million": 6.0,
                "security_profile": {
                    "kind": "clearance_level",
                    "clearance_label": "INTERNAL",
                },
            }
        ],
        "admin": {"api_key_env": "TEST_ADMIN_KEY"},
        "identity_provider": {
            "issuer": "https://identity.test",
            "audience": "sovereignflow",
            "jwks_url": "https://identity.test/jwks",
            "algorithms": ["RS256"],
            "timeout_seconds": 5,
            "cache_ttl_seconds": 300,
            "tenant_claim": "tenant_id",
            "roles_claim": "roles",
            "groups_claim": "groups",
            "acl_claim": "acl_labels",
            "clearance_claim": "clearance_label",
            "classification_labels_claim": "classification_labels",
            "external_model_claim": "allow_external_model",
            "diagnostic_claim": "sovereignflow_diagnostics",
        },
        "embeddings": {
            "name": "embed",
            "base_url": "http://localhost:8082/v1",
            "model": "vectors",
            "timeout_seconds": 10,
        },
        "weaviate": {
            "host": "127.0.0.1",
            "http_port": 18080,
            "grpc_port": 15005,
            "secure": False,
            "api_key_env": "TEST_WEAVIATE_KEY",
        },
        "prompts_root": "prompts",
        "pipelines_root": "pipelines",
        "domains": ["domain.yaml"],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return config_path, config


@pytest.fixture(autouse=True)
def secrets(monkeypatch) -> None:
    monkeypatch.setenv("TEST_POSTGRES_URL", "postgresql://test")
    monkeypatch.setenv("TEST_WEAVIATE_KEY", "weaviate-secret")
    monkeypatch.setenv("TEST_MODEL_KEY", "model-secret")
    monkeypatch.setenv("TEST_EMBED_KEY", "embed-secret")
    monkeypatch.setenv("TEST_ADMIN_KEY", "admin-secret")


def write(path: Path, raw) -> None:
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_load_settings_resolves_complete_configuration(tmp_path: Path) -> None:
    path, raw = valid_files(tmp_path)
    raw["model_servers"][0]["api_key_env"] = "TEST_MODEL_KEY"
    raw["embeddings"]["api_key_env"] = "TEST_EMBED_KEY"
    raw["web_client"] = {
        "client_id": "web-client",
        "authorization_url": "https://identity.test/authorize",
        "token_url": "https://identity.test/token",
        "logout_url": "https://identity.test/logout",
    }
    write(path, raw)

    settings = load_settings(path)

    assert settings.server.threads == 4
    assert settings.postgresql.connection_url == "postgresql://test"
    assert settings.weaviate.api_key == "weaviate-secret"
    assert settings.model_servers[0].api_key == "model-secret"
    assert settings.embeddings.api_key == "embed-secret"
    assert settings.admin.api_key == "admin-secret"
    assert settings.identity_provider.audience == "sovereignflow"
    assert settings.web_client is not None
    assert settings.web_client.client_id == "web-client"
    assert settings.model_servers[0].input_cost_per_million == 1.5
    assert settings.domains[0].retrieval.mode == SearchMode.HYBRID
    assert settings.domains[0].graph.max_depth == 2
    assert settings.domains[0].allowed_pipeline_names == (
        "default",
        "direct",
        "graph",
        "strict",
    )
    assert settings.prompts_root == tmp_path / "prompts"


def test_load_settings_accepts_none_and_label_model_server_profiles(tmp_path: Path) -> None:
    path, raw = valid_files(tmp_path)
    raw["model_servers"][0]["security_profile"] = {"kind": "none"}
    write(path, raw)
    settings = load_settings(path)
    assert settings.model_servers[0].definition.security_profile.security_model_kind.value == "none"

    second = tmp_path / "labels"
    second.mkdir()
    path, raw = valid_files(second)
    raw["model_servers"][0]["security_profile"] = {
        "kind": "classification_labels",
        "classification_labels": ["US_NOFORN", "US_ORCON"],
    }
    write(path, raw)
    settings = load_settings(path)
    assert settings.model_servers[0].definition.security_profile.classification_labels == (
        "US_NOFORN",
        "US_ORCON",
    )


@pytest.mark.parametrize(
    "key",
    ["server", "postgresql", "weaviate", "embeddings", "admin", "identity_provider"],
)
def test_load_settings_requires_mapping_sections(tmp_path: Path, key: str) -> None:
    invalid_pipeline_root = tmp_path / "invalid-pipeline"
    invalid_pipeline_root.mkdir()
    path, raw = valid_files(invalid_pipeline_root)
    raw[key] = None
    write(path, raw)

    with pytest.raises(ConfigurationError, match=key):
        load_settings(path)


def test_identity_provider_and_allowed_filter_configuration_are_strict(
    tmp_path: Path,
) -> None:
    path, raw = valid_files(tmp_path)
    raw["identity_provider"]["algorithms"] = []
    write(path, raw)
    with pytest.raises(ConfigurationError, match="algorithms"):
        load_settings(path)

    invalid_pipeline_root = tmp_path / "invalid-pipeline"
    invalid_pipeline_root.mkdir()
    path, raw = valid_files(invalid_pipeline_root)
    raw["domains"] = ["domain.yaml"]
    domain_path = invalid_pipeline_root / "domain.yaml"
    domain = yaml.safe_load(domain_path.read_text(encoding="utf-8"))
    domain["allowed_pipeline_names"] = "direct"
    write(domain_path, domain)
    with pytest.raises(ConfigurationError, match="allowed_pipeline_names"):
        load_settings(path)


def test_web_client_configuration_is_optional_and_strict(tmp_path: Path) -> None:
    path, raw = valid_files(tmp_path)

    assert load_settings(path).web_client is None

    raw["web_client"] = "invalid"
    write(path, raw)
    with pytest.raises(ConfigurationError, match="web_client must be a mapping"):
        load_settings(path)

    raw["web_client"] = {
        "client_id": "web-client",
        "authorization_url": "not-a-url",
        "token_url": "https://identity.test/token",
        "logout_url": "https://identity.test/logout",
    }
    write(path, raw)
    with pytest.raises(ConfigurationError, match="absolute HTTP URL"):
        load_settings(path)

    second = tmp_path / "second"
    second.mkdir()
    path, raw = valid_files(second)
    domain_path = second / "domain.yaml"
    domain = yaml.safe_load(domain_path.read_text(encoding="utf-8"))
    domain["retrieval"]["allowed_filter_fields"] = "country"
    write(domain_path, domain)
    with pytest.raises(ConfigurationError, match="allowed_filter_fields"):
        load_settings(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.update(model_servers=[]), "model_servers must"),
        (lambda raw: raw.update(model_servers=["bad"]), "Each model server"),
        (
            lambda raw: raw.update(
                model_servers=[raw["model_servers"][0], raw["model_servers"][0]]
            ),
            "unique",
        ),
        (
            lambda raw: raw["model_servers"][0].update(trust_boundary="invalid"),
            "trust_boundary",
        ),
        (
            lambda raw: raw["model_servers"][0].update(security_profile={"kind": "bad"}),
            "security_profile.kind",
        ),
        (
            lambda raw: raw["model_servers"][0].update(
                security_profile={"kind": "clearance_level"}
            ),
            "security_profile.clearance_label",
        ),
        (
            lambda raw: raw["model_servers"][0].update(
                security_profile={"kind": "classification_labels"}
            ),
            "security_profile.classification_labels",
        ),
        (
            lambda raw: raw["model_servers"][0].update(security_reroute_server_id="missing"),
            "security_reroute_server_id",
        ),
        (
            lambda raw: raw["model_servers"][0].update(input_cost_per_million=-1),
            "input_cost_per_million",
        ),
        (
            lambda raw: raw["model_servers"][0].update(input_cost_per_million="invalid"),
            "input_cost_per_million",
        ),
        (lambda raw: raw.update(domains=[]), "domains must"),
        (
            lambda raw: raw.update(domains=["missing.yaml"]),
            "domain profile does not exist",
        ),
        (
            lambda raw: raw.update(prompts_root="missing"),
            "prompts_root does not exist",
        ),
        (lambda raw: raw["server"].update(port=0), "server.port"),
        (lambda raw: raw["server"].update(threads="bad"), "server.threads"),
        (lambda raw: raw["weaviate"].update(secure="false"), "must be boolean"),
        (
            lambda raw: raw["embeddings"].update(timeout_seconds=0),
            "embeddings.timeout_seconds",
        ),
        (
            lambda raw: raw["embeddings"].update(timeout_seconds="bad"),
            "embeddings.timeout_seconds",
        ),
        (
            lambda raw: raw["server"].update(host=""),
            "server.host",
        ),
    ],
)
def test_load_settings_rejects_invalid_root_configuration(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    path, raw = valid_files(tmp_path)
    mutate(raw)
    write(path, raw)

    with pytest.raises(ConfigurationError, match=message):
        load_settings(path)


def test_legacy_domain_classification_level_is_rejected(tmp_path: Path) -> None:
    path, _ = valid_files(tmp_path)
    domain_path = tmp_path / "domain.yaml"
    domain = yaml.safe_load(domain_path.read_text(encoding="utf-8"))
    domain["max_classification_level"] = 1
    write(domain_path, domain)

    with pytest.raises(ConfigurationError, match="max_classification_level"):
        load_settings(path)


def test_duplicate_domain_names_are_rejected(tmp_path: Path) -> None:
    path, raw = valid_files(tmp_path)
    second = tmp_path / "domain-2.yaml"
    second.write_text((tmp_path / "domain.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    raw["domains"].append("domain-2.yaml")
    write(path, raw)

    with pytest.raises(ConfigurationError, match="domain names"):
        load_settings(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda raw: raw["retrieval"].update(mode="unknown"),
            "retrieval.mode",
        ),
        (
            lambda raw: raw["retrieval"].update(filters=[]),
            "retrieval.filters",
        ),
        (
            lambda raw: raw["security"]["acl"].update(allowed_labels="public"),
            "security.acl.allowed_labels",
        ),
        (
            lambda raw: raw.update(allow_external_model="false"),
            "allow_external_model",
        ),
        (
            lambda raw: raw["retrieval"].update(top_k=0),
            "retrieval.top_k",
        ),
        (
            lambda raw: raw["graph"].update(direction="sideways"),
            "graph.direction",
        ),
        (
            lambda raw: raw["graph"].update(relationship_types="references"),
            "graph.relationship_types",
        ),
        (
            lambda raw: raw["graph"].update(enabled="true"),
            "graph.enabled",
        ),
        (
            lambda raw: raw["graph"].update(max_depth=0),
            "graph.max_depth",
        ),
    ],
)
def test_domain_profile_validation(tmp_path: Path, mutate, message: str) -> None:
    path, config = valid_files(tmp_path)
    domain_path = tmp_path / "domain.yaml"
    domain = yaml.safe_load(domain_path.read_text(encoding="utf-8"))
    mutate(domain)
    write(domain_path, domain)

    with pytest.raises(ConfigurationError, match=message):
        load_settings(path)


def test_yaml_and_secret_errors_are_explicit(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ConfigurationError, match="does not exist"):
        load_settings(missing)

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(":", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Cannot read YAML"):
        load_settings(invalid)

    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("value", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="root must"):
        load_settings(scalar)

    path, _ = valid_files(tmp_path)
    monkeypatch.delenv("TEST_POSTGRES_URL")
    with pytest.raises(ConfigurationError, match="TEST_POSTGRES_URL"):
        load_settings(path)
