from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sovereignflow.domain import (
    ConfigurationError,
    DomainProfile,
    GraphDirection,
    GraphTraversalProfile,
    RetrievalProfile,
    SearchMode,
)


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    threads: int


@dataclass(frozen=True)
class PostgreSQLSettings:
    connection_url: str
    timeout_seconds: int


@dataclass(frozen=True)
class WeaviateSettings:
    host: str
    http_port: int
    grpc_port: int
    secure: bool
    api_key: str


@dataclass(frozen=True)
class EmbeddingSettings:
    name: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float


@dataclass(frozen=True)
class ModelSettings:
    name: str
    scope: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    input_cost_per_million: float
    output_cost_per_million: float


@dataclass(frozen=True)
class AdminSettings:
    api_key: str


@dataclass(frozen=True)
class IdentityProviderSettings:
    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...]
    timeout_seconds: float
    cache_ttl_seconds: int
    tenant_claim: str
    roles_claim: str
    groups_claim: str
    acl_claim: str
    classification_claim: str
    external_model_claim: str
    diagnostic_claim: str


@dataclass(frozen=True)
class WebClientSettings:
    client_id: str
    authorization_url: str
    token_url: str
    logout_url: str


@dataclass(frozen=True)
class SovereignFlowSettings:
    config_path: Path
    server: ServerSettings
    postgresql: PostgreSQLSettings
    weaviate: WeaviateSettings
    embeddings: EmbeddingSettings
    selected_model: ModelSettings
    admin: AdminSettings
    identity_provider: IdentityProviderSettings
    prompts_root: Path
    pipelines_root: Path
    domains: tuple[DomainProfile, ...]
    web_client: WebClientSettings | None = None


def load_settings(path: str | Path) -> SovereignFlowSettings:
    config_path = Path(path).expanduser().resolve()
    raw = _read_yaml(config_path)
    server = _mapping(raw, "server")
    postgresql = _mapping(raw, "postgresql")
    weaviate = _mapping(raw, "weaviate")
    embeddings = _mapping(raw, "embeddings")
    admin = _mapping(raw, "admin")
    identity_provider = _mapping(raw, "identity_provider")
    web_client = raw.get("web_client")
    if web_client is not None and not isinstance(web_client, dict):
        raise ConfigurationError("web_client must be a mapping")

    models_raw = raw.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        raise ConfigurationError("models must be a non-empty list")
    model_items = tuple(_model_settings(item) for item in models_raw)
    if len({item.name for item in model_items}) != len(model_items):
        raise ConfigurationError("model names must be unique")
    selected_name = _required(raw.get("selected_model"), "selected_model")
    selected = next((item for item in model_items if item.name == selected_name), None)
    if selected is None:
        raise ConfigurationError(f"selected_model does not exist: {selected_name}")

    prompt_root = _resolve_existing_directory(
        config_path.parent,
        _required(raw.get("prompts_root"), "prompts_root"),
        "prompts_root",
    )
    pipelines_root = _resolve_existing_directory(
        config_path.parent,
        _required(raw.get("pipelines_root"), "pipelines_root"),
        "pipelines_root",
    )
    domain_paths = raw.get("domains")
    if not isinstance(domain_paths, list) or not domain_paths:
        raise ConfigurationError("domains must be a non-empty list")
    domains = tuple(
        _load_domain(
            _resolve_existing_file(
                config_path.parent,
                _required(domain_path, "domains[]"),
                "domain profile",
            )
        )
        for domain_path in domain_paths
    )
    if len({domain.name for domain in domains}) != len(domains):
        raise ConfigurationError("domain names must be unique")
    forbidden_domains = [
        domain.name
        for domain in domains
        if selected.scope == "external" and not domain.allow_external_model
    ]
    if forbidden_domains:
        raise ConfigurationError(
            "Selected external model is forbidden for domains: "
            + ", ".join(sorted(forbidden_domains))
        )

    return SovereignFlowSettings(
        config_path=config_path,
        server=ServerSettings(
            host=_required(server.get("host"), "server.host"),
            port=_positive_int(server.get("port"), "server.port"),
            threads=_positive_int(server.get("threads"), "server.threads"),
        ),
        postgresql=PostgreSQLSettings(
            connection_url=_secret(postgresql.get("connection_url_env")),
            timeout_seconds=_positive_int(
                postgresql.get("timeout_seconds"),
                "postgresql.timeout_seconds",
            ),
        ),
        weaviate=WeaviateSettings(
            host=_required(weaviate.get("host"), "weaviate.host"),
            http_port=_positive_int(weaviate.get("http_port"), "weaviate.http_port"),
            grpc_port=_positive_int(weaviate.get("grpc_port"), "weaviate.grpc_port"),
            secure=_required_bool(weaviate.get("secure"), "weaviate.secure"),
            api_key=_secret(weaviate.get("api_key_env")),
        ),
        embeddings=EmbeddingSettings(
            name=_required(embeddings.get("name"), "embeddings.name"),
            base_url=_required(embeddings.get("base_url"), "embeddings.base_url"),
            model=_required(embeddings.get("model"), "embeddings.model"),
            api_key=_optional_secret(embeddings.get("api_key_env")),
            timeout_seconds=_positive_float(
                embeddings.get("timeout_seconds"),
                "embeddings.timeout_seconds",
            ),
        ),
        selected_model=selected,
        admin=AdminSettings(api_key=_secret(admin.get("api_key_env"))),
        identity_provider=_identity_provider_settings(identity_provider),
        web_client=(_web_client_settings(web_client) if isinstance(web_client, dict) else None),
        prompts_root=prompt_root,
        pipelines_root=pipelines_root,
        domains=domains,
    )


def _model_settings(raw: Any) -> ModelSettings:
    if not isinstance(raw, dict):
        raise ConfigurationError("Each model must be a mapping")
    scope = _required(raw.get("scope"), "models[].scope").lower()
    if scope not in {"local", "external"}:
        raise ConfigurationError("models[].scope must be 'local' or 'external'")
    return ModelSettings(
        name=_required(raw.get("name"), "models[].name"),
        scope=scope,
        base_url=_required(raw.get("base_url"), "models[].base_url"),
        model=_required(raw.get("model"), "models[].model"),
        api_key=_optional_secret(raw.get("api_key_env")),
        timeout_seconds=_positive_float(
            raw.get("timeout_seconds"),
            "models[].timeout_seconds",
        ),
        input_cost_per_million=_non_negative_float(
            raw.get("input_cost_per_million"),
            "models[].input_cost_per_million",
        ),
        output_cost_per_million=_non_negative_float(
            raw.get("output_cost_per_million"),
            "models[].output_cost_per_million",
        ),
    )


def _identity_provider_settings(raw: dict[str, Any]) -> IdentityProviderSettings:
    algorithms = raw.get("algorithms")
    if not isinstance(algorithms, list) or not algorithms:
        raise ConfigurationError("identity_provider.algorithms must be a non-empty list")
    return IdentityProviderSettings(
        issuer=_required(raw.get("issuer"), "identity_provider.issuer"),
        audience=_required(raw.get("audience"), "identity_provider.audience"),
        jwks_url=_required(raw.get("jwks_url"), "identity_provider.jwks_url"),
        algorithms=tuple(
            _required(value, "identity_provider.algorithms[]") for value in algorithms
        ),
        timeout_seconds=_positive_float(
            raw.get("timeout_seconds"),
            "identity_provider.timeout_seconds",
        ),
        cache_ttl_seconds=_positive_int(
            raw.get("cache_ttl_seconds"),
            "identity_provider.cache_ttl_seconds",
        ),
        tenant_claim=_required(
            raw.get("tenant_claim"),
            "identity_provider.tenant_claim",
        ),
        roles_claim=_required(raw.get("roles_claim"), "identity_provider.roles_claim"),
        groups_claim=_required(raw.get("groups_claim"), "identity_provider.groups_claim"),
        acl_claim=_required(raw.get("acl_claim"), "identity_provider.acl_claim"),
        classification_claim=_required(
            raw.get("classification_claim"),
            "identity_provider.classification_claim",
        ),
        external_model_claim=_required(
            raw.get("external_model_claim"),
            "identity_provider.external_model_claim",
        ),
        diagnostic_claim=_required(
            raw.get("diagnostic_claim"),
            "identity_provider.diagnostic_claim",
        ),
    )


def _web_client_settings(raw: dict[str, Any]) -> WebClientSettings:
    return WebClientSettings(
        client_id=_required(raw.get("client_id"), "web_client.client_id"),
        authorization_url=_absolute_http_url(
            raw.get("authorization_url"),
            "web_client.authorization_url",
        ),
        token_url=_absolute_http_url(raw.get("token_url"), "web_client.token_url"),
        logout_url=_absolute_http_url(raw.get("logout_url"), "web_client.logout_url"),
    )


def _load_domain(path: Path) -> DomainProfile:
    raw = _read_yaml(path)
    retrieval = _mapping(raw, "retrieval")
    graph = _mapping(raw, "graph")
    try:
        mode = SearchMode(_required(retrieval.get("mode"), "retrieval.mode").lower())
    except ValueError as exc:
        raise ConfigurationError("retrieval.mode must be semantic, bm25 or hybrid") from exc
    filters = retrieval.get("filters")
    if not isinstance(filters, dict):
        raise ConfigurationError("retrieval.filters must be a mapping")
    allowed_filter_fields = retrieval.get("allowed_filter_fields")
    if not isinstance(allowed_filter_fields, list):
        raise ConfigurationError("retrieval.allowed_filter_fields must be a list")
    labels = raw.get("allowed_acl_labels")
    if not isinstance(labels, list):
        raise ConfigurationError("allowed_acl_labels must be a list")
    maximum = raw.get("max_classification_level")
    allowed_pipeline_names = raw.get("allowed_pipeline_names", [])
    if not isinstance(allowed_pipeline_names, list):
        raise ConfigurationError("allowed_pipeline_names must be a list")
    relationship_types = graph.get("relationship_types")
    if not isinstance(relationship_types, list):
        raise ConfigurationError("graph.relationship_types must be a list")
    try:
        graph_direction = GraphDirection(
            _required(graph.get("direction"), "graph.direction").lower()
        )
    except ValueError as exc:
        raise ConfigurationError("graph.direction must be outgoing, incoming or both") from exc
    return DomainProfile(
        name=_required(raw.get("name"), "name"),
        description=str(raw.get("description") or "").strip(),
        collection=_required(raw.get("collection"), "collection"),
        tenant_id=_required(raw.get("tenant_id"), "tenant_id"),
        prompt_name=_required(raw.get("prompt_name"), "prompt_name"),
        pipeline_name=_required(raw.get("pipeline_name"), "pipeline_name"),
        allowed_pipeline_names=tuple(str(name) for name in allowed_pipeline_names),
        allow_external_model=_required_bool(
            raw.get("allow_external_model"),
            "allow_external_model",
        ),
        disclaimer=str(raw.get("disclaimer") or "").strip(),
        allowed_acl_labels=tuple(str(label) for label in labels),
        max_classification_level=(int(maximum) if maximum is not None else None),
        retrieval=RetrievalProfile(
            mode=mode,
            top_k=_positive_int(retrieval.get("top_k"), "retrieval.top_k"),
            max_context_characters=_positive_int(
                retrieval.get("max_context_characters"),
                "retrieval.max_context_characters",
            ),
            filters=filters,
            allowed_filter_fields=tuple(str(item) for item in allowed_filter_fields),
        ),
        graph=GraphTraversalProfile(
            enabled=_required_bool(graph.get("enabled"), "graph.enabled"),
            max_depth=_positive_int(graph.get("max_depth"), "graph.max_depth"),
            max_nodes=_positive_int(graph.get("max_nodes"), "graph.max_nodes"),
            direction=graph_direction,
            relationship_types=tuple(str(item) for item in relationship_types),
        ),
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"Configuration file does not exist: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Cannot read YAML file: {path}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"YAML root must be a mapping: {path}")
    return raw


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"{key} must be a mapping")
    return value


def _required(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ConfigurationError(f"{field_name} is required")
    return normalized


def _absolute_http_url(value: Any, field_name: str) -> str:
    normalized = _required(value, field_name)
    from urllib.parse import urlsplit

    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"{field_name} must be an absolute HTTP URL")
    return normalized


def _required_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{field_name} must be boolean")
    return value


def _positive_int(value: Any, field_name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name} must be an integer") from exc
    if result <= 0:
        raise ConfigurationError(f"{field_name} must be greater than zero")
    return result


def _positive_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name} must be numeric") from exc
    if result <= 0:
        raise ConfigurationError(f"{field_name} must be greater than zero")
    return result


def _non_negative_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name} must be numeric") from exc
    if result < 0:
        raise ConfigurationError(f"{field_name} cannot be negative")
    return result


def _secret(environment_name: Any) -> str:
    name = _required(environment_name, "secret environment variable")
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise ConfigurationError(f"Required environment variable is empty: {name}")
    return value


def _optional_secret(environment_name: Any) -> str:
    name = str(environment_name or "").strip()
    return _secret(name) if name else ""


def _resolve_existing_file(base: Path, value: str, field_name: str) -> Path:
    path = _resolve(base, value)
    if not path.is_file():
        raise ConfigurationError(f"{field_name} does not exist: {path}")
    return path


def _resolve_existing_directory(base: Path, value: str, field_name: str) -> Path:
    path = _resolve(base, value)
    if not path.is_dir():
        raise ConfigurationError(f"{field_name} does not exist: {path}")
    return path


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()
