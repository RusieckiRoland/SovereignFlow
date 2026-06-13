from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sovereignflow.bootstrap.config import load_settings
from sovereignflow.domain import ConfigurationError, SearchMode


def valid_files(tmp_path: Path) -> tuple[Path, dict]:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
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
                "allow_external_model": False,
                "disclaimer": "Verify.",
                "allowed_acl_labels": ["public"],
                "max_classification_level": 1,
                "retrieval": {
                    "mode": "hybrid",
                    "top_k": 3,
                    "max_context_characters": 1000,
                    "filters": {"status": "active"},
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
        "selected_model": "local",
        "models": [
            {
                "name": "local",
                "scope": "local",
                "base_url": "http://localhost:8080/v1",
                "model": "chat",
                "timeout_seconds": 30,
            }
        ],
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


def write(path: Path, raw) -> None:
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_load_settings_resolves_complete_configuration(tmp_path: Path) -> None:
    path, raw = valid_files(tmp_path)
    raw["models"][0]["api_key_env"] = "TEST_MODEL_KEY"
    raw["embeddings"]["api_key_env"] = "TEST_EMBED_KEY"
    write(path, raw)

    settings = load_settings(path)

    assert settings.server.threads == 4
    assert settings.postgresql.connection_url == "postgresql://test"
    assert settings.weaviate.api_key == "weaviate-secret"
    assert settings.selected_model.api_key == "model-secret"
    assert settings.embeddings.api_key == "embed-secret"
    assert settings.domains[0].retrieval.mode == SearchMode.HYBRID
    assert settings.prompts_root == tmp_path / "prompts"


@pytest.mark.parametrize("key", ["server", "postgresql", "weaviate", "embeddings"])
def test_load_settings_requires_mapping_sections(tmp_path: Path, key: str) -> None:
    path, raw = valid_files(tmp_path)
    raw[key] = None
    write(path, raw)

    with pytest.raises(ConfigurationError, match=key):
        load_settings(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.update(models=[]), "models must"),
        (lambda raw: raw.update(models=["bad"]), "Each model"),
        (
            lambda raw: raw.update(models=[raw["models"][0], raw["models"][0]]),
            "unique",
        ),
        (lambda raw: raw.update(selected_model="missing"), "does not exist"),
        (
            lambda raw: raw["models"][0].update(scope="invalid"),
            "scope",
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


def test_external_model_must_be_allowed_by_every_domain(tmp_path: Path) -> None:
    path, raw = valid_files(tmp_path)
    raw["models"][0]["scope"] = "external"
    write(path, raw)

    with pytest.raises(ConfigurationError, match="forbidden"):
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
            lambda raw: raw.update(allowed_acl_labels="public"),
            "allowed_acl_labels",
        ),
        (
            lambda raw: raw.update(allow_external_model="false"),
            "allow_external_model",
        ),
        (
            lambda raw: raw["retrieval"].update(top_k=0),
            "retrieval.top_k",
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
