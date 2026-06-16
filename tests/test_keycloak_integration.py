from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from werkzeug.serving import make_server

from sovereignflow.bootstrap import bootstrap, load_settings
from sovereignflow.domain import (
    AccessPolicyBundle,
    CapabilityDescriptor,
    ClaimGroupMapping,
    DocumentChunk,
    DocumentSecurity,
    GroupCapabilityGrant,
    IngestionCommand,
)
from sovereignflow.infrastructure import PostgreSQLAccessPolicyRepository


@contextmanager
def running_http_app(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def access_token(keycloak_url: str, username: str) -> str:
    payload = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "sovereignflow-integration-client",
            "username": username,
            "password": "stage2-test-password",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{keycloak_url}/realms/sovereignflow/protocol/openid-connect/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    return str(result["access_token"])


def post_query(
    api_url: str,
    token: str,
    capability_id: str,
    *,
    diagnostics: bool,
) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{api_url}/v1/query",
        data=json.dumps(
            {
                "query": "Where is the permitted record stored?",
                "capability_id": capability_id,
                "session_id": "keycloak-integration",
                "filters": {},
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-SovereignFlow-Diagnostics": str(diagnostics).lower(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def get_catalog(api_url: str, token: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{api_url}/v1/catalog",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


@pytest.mark.integration
def test_real_keycloak_token_authorizes_full_neutral_rag_flow(
    tmp_path,
    http_server,
    monkeypatch,
) -> None:
    postgres_url = os.getenv("SOVEREIGNFLOW_TEST_POSTGRES_URL")
    weaviate_host = os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_HOST")
    weaviate_api_key = os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_API_KEY")
    keycloak_url = os.getenv("SOVEREIGNFLOW_TEST_KEYCLOAK_URL")
    if not postgres_url or not weaviate_host or not weaviate_api_key or not keycloak_url:
        pytest.skip("Real Keycloak integration services are not configured")

    http_server.responses[("GET", "/v1/models")] = (
        200,
        {"data": [{"id": "controlled-model"}]},
        "application/json",
    )
    http_server.responses[("POST", "/v1/embeddings")] = (
        200,
        {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]},
        "application/json",
    )
    http_server.responses[("POST", "/v1/chat/completions")] = (
        200,
        {
            "choices": [{"message": {"content": "Keycloak-authorized answer."}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5},
        },
        "application/json",
    )
    provider_url = f"http://127.0.0.1:{http_server.server_port}"
    identity = uuid.uuid4().hex
    domain_name = f"keycloak-{identity}"
    collection_name = f"KeycloakIntegration{identity}"
    prompts = tmp_path / "prompts"
    pipelines = tmp_path / "pipelines"
    domains = tmp_path / "domains"
    prompts.mkdir()
    pipelines.mkdir()
    domains.mkdir()
    (prompts / "answer.txt").write_text("Use only supplied evidence.", encoding="utf-8")
    source_pipeline = Path(__file__).resolve().parents[1] / "pipelines/default.yaml"
    (pipelines / "default.yaml").write_text(
        source_pipeline.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (domains / "keycloak.yaml").write_text(
        yaml.safe_dump(
            {
                "name": domain_name,
                "description": "Real Keycloak integration",
                "collection": collection_name,
                "tenant_id": "tenant_0001",
                "prompt_name": "answer",
                "pipeline_name": "default",
                "allow_external_model": True,
                "disclaimer": "",
                "security": {
                    "acl": {
                        "enabled": True,
                        "allowed_labels": ["public", "internal", "restricted"],
                    },
                    "require_travel_permission": True,
                    "security_model": {"kind": "none"},
                },
                "retrieval": {
                    "mode": "hybrid",
                    "top_k": 5,
                    "max_context_characters": 2000,
                    "filters": {},
                    "allowed_filter_fields": [],
                },
                "graph": {
                    "enabled": False,
                    "max_depth": 1,
                    "max_nodes": 1,
                    "direction": "both",
                    "relationship_types": [],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KEYCLOAK_STAGE2_POSTGRES_URL", postgres_url)
    monkeypatch.setenv("KEYCLOAK_STAGE2_WEAVIATE_KEY", weaviate_api_key)
    monkeypatch.setenv("KEYCLOAK_STAGE2_ADMIN_KEY", "keycloak-stage2-admin")
    config_path = tmp_path / "sovereignflow.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "server": {"host": "127.0.0.1", "port": 8000, "threads": 2},
                "postgresql": {
                    "connection_url_env": "KEYCLOAK_STAGE2_POSTGRES_URL",
                    "timeout_seconds": 5,
                },
                "admin": {"api_key_env": "KEYCLOAK_STAGE2_ADMIN_KEY"},
                "identity_provider": {
                    "issuer": f"{keycloak_url}/realms/sovereignflow",
                    "audience": "sovereignflow-api",
                    "jwks_url": (
                        f"{keycloak_url}/realms/sovereignflow/protocol/openid-connect/certs"
                    ),
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
                "model_servers": [
                    {
                        "id": "default-model",
                        "trust_boundary": "external",
                        "base_url": f"{provider_url}/v1",
                        "model": "controlled-model",
                        "timeout_seconds": 2,
                        "input_cost_per_million": 0,
                        "output_cost_per_million": 0,
                        "security_profile": {"kind": "none"},
                    }
                ],
                "embeddings": {
                    "name": "controlled-embeddings",
                    "base_url": f"{provider_url}/v1",
                    "model": "controlled-embedding-model",
                    "timeout_seconds": 2,
                },
                "weaviate": {
                    "host": weaviate_host,
                    "http_port": int(os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_HTTP_PORT", "8080")),
                    "grpc_port": int(os.getenv("SOVEREIGNFLOW_TEST_WEAVIATE_GRPC_PORT", "50051")),
                    "secure": False,
                    "api_key_env": "KEYCLOAK_STAGE2_WEAVIATE_KEY",
                },
                "prompts_root": "prompts",
                "pipelines_root": "pipelines",
                "domains": ["domains/keycloak.yaml"],
            }
        ),
        encoding="utf-8",
    )

    application = bootstrap(load_settings(config_path))
    try:
        application.ingestion_services[domain_name].ingest(
            IngestionCommand(
                idempotency_key=f"keycloak-{identity}",
                domain=domain_name,
                tenant_id="tenant_0001",
                source_id="permitted-source",
                source_version="v1",
                chunks=(
                    DocumentChunk(
                        chunk_id="permitted-chunk",
                        domain=domain_name,
                        tenant_id="tenant_0001",
                        source_id="permitted-source",
                        text="The permitted record is stored in the primary table.",
                        acl_labels=("internal",),
                        security=DocumentSecurity(clearance_label="INTERNAL"),
                    ),
                ),
            )
        )
        PostgreSQLAccessPolicyRepository(postgres_url, timeout_seconds=5).publish(
            AccessPolicyBundle(
                tenant_id="tenant_0001",
                version=1,
                group_ids=("integration-readers",),
                claim_mappings=(
                    ClaimGroupMapping(
                        "groups",
                        "integration",
                        "integration-readers",
                    ),
                ),
                capabilities=(
                    CapabilityDescriptor(
                        "keycloak-query",
                        "Keycloak query",
                        "Real Identity Provider integration",
                        domain_name,
                        "default",
                        True,
                        True,
                        1,
                    ),
                ),
                grants=(
                    GroupCapabilityGrant(
                        "integration-readers",
                        "keycloak-query",
                    ),
                ),
            ),
            expected_version=None,
        )
        permitted_token = access_token(keycloak_url, "integration-user")
        restricted_token = access_token(keycloak_url, "restricted-user")
        with running_http_app(application.app) as api_url:
            assert [
                item["capability_id"]
                for item in get_catalog(api_url, permitted_token)[1]["capabilities"]
            ] == ["keycloak-query"]
            assert get_catalog(api_url, restricted_token)[1]["capabilities"] == []
            status, result = post_query(
                api_url,
                permitted_token,
                "keycloak-query",
                diagnostics=True,
            )
            assert status == 200
            assert result["ok"] is True
            assert result["answer"] == "Keycloak-authorized answer."
            assert result["diagnostics"]["tenant_id"] == "tenant_0001"
            assert result["diagnostics"]["allowed_acl_labels"] == [
                "internal",
                "public",
                "restricted",
            ]
            assert result["diagnostics"]["context_chunk_ids"] == ["permitted-chunk"]
            assert result["diagnostics"]["provider"] == "controlled-external"

            status, rejected = post_query(
                api_url,
                restricted_token,
                "keycloak-query",
                diagnostics=False,
            )
            assert status == 403
            assert rejected["error"]["code"] == "policy_violation"
            assert "not available" in rejected["error"]["message"]
        model_requests = [
            item for item in http_server.requests if item[0:2] == ("POST", "/v1/chat/completions")
        ]
        assert len(model_requests) == 1
        assert "primary table" in model_requests[0][3]["messages"][1]["content"]
    finally:
        if application.weaviate_client.collections.exists(collection_name):
            application.weaviate_client.collections.delete(collection_name)
        application.close()
