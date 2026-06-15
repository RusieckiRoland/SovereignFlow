from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import pytest
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from sovereignflow.application import OperationsService, PipelineAuthorizationService
from sovereignflow.domain import (
    AccessPolicyBundle,
    CapabilityDescriptor,
    ClaimGroupMapping,
    GroupCapabilityGrant,
    QueryResult,
)
from sovereignflow.infrastructure import (
    JwksCache,
    OidcJwtAuthenticator,
    OidcSettings,
    PostgreSQLAccessPolicyRepository,
    PostgreSQLMigrationRunner,
    PostgreSQLSecurityDecisionAudit,
)
from sovereignflow.interfaces import QueryDispatcher, WebClientConfiguration, create_app


class QueryService:
    def __init__(self, pipeline_name: str) -> None:
        self.pipeline_name = pipeline_name

    def execute(self, command):
        return QueryResult(
            request_id=command.request_id,
            answer=f"Response from {self.pipeline_name}.",
            domain=command.domain,
            session_id=command.session_id,
            citations=(),
            pipeline_trace=("authorize", self.pipeline_name, "finalize"),
        )


class Operations:
    def execution(self, request_id: str, *, tenant_id: str):
        return None

    def metrics(self, *, tenant_id: str, hours: int):
        return {}

    def ingestion_job(self, job_id: str, *, tenant_id: str):
        return None

    def retry_ingestion(self, job_id: str, *, tenant_id: str):
        return None


@contextmanager
def running_browser_app(app):
    server = make_server("127.0.0.1", 8000, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.e2e
def test_browser_uses_keycloak_pkce_and_receives_user_specific_catalog() -> None:
    connection_url = os.getenv("SOVEREIGNFLOW_TEST_POSTGRES_URL")
    keycloak_url = os.getenv("SOVEREIGNFLOW_TEST_KEYCLOAK_URL")
    if not connection_url or not keycloak_url:
        pytest.skip("Browser E2E services are not configured")
    tenant_id = "tenant_0001"
    repository = PostgreSQLAccessPolicyRepository(connection_url, timeout_seconds=5)
    PostgreSQLMigrationRunner(connection_url, timeout_seconds=5).migrate()
    repository.publish(browser_policy(tenant_id), expected_version=None)
    issuer = f"{keycloak_url}/realms/sovereignflow"
    authenticator = OidcJwtAuthenticator(
        OidcSettings(
            issuer=issuer,
            audience="sovereignflow-api",
            jwks_url=f"{issuer}/protocol/openid-connect/certs",
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
        cache=JwksCache(
            url=f"{issuer}/protocol/openid-connect/certs",
            timeout_seconds=5,
            ttl_seconds=300,
        ),
    )
    authorization = PipelineAuthorizationService(
        repository,
        PostgreSQLSecurityDecisionAudit(connection_url, timeout_seconds=5),
    )
    app = create_app(
        QueryDispatcher(
            {
                ("general", pipeline_name): QueryService(pipeline_name)
                for pipeline_name in ("direct", "graph", "strict")
            },
            authorization,
            default_pipelines={"general": "direct"},
        ),
        (),
        OperationsService(
            audit=Operations(),
            ingestion_repository=Operations(),
            ingestion_services={},
        ),
        "browser-admin-key",
        authenticator,
        WebClientConfiguration(
            client_id="sovereignflow-web-client",
            authorization_url=f"{issuer}/protocol/openid-connect/auth",
            token_url=f"{issuer}/protocol/openid-connect/token",
            logout_url=f"{issuer}/protocol/openid-connect/logout",
        ),
    )

    with running_browser_app(app), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for username, display_name, pipeline_name in (
                ("direct-user", "Direct retrieval", "direct"),
                ("graph-user", "Graph-expanded retrieval", "graph"),
                ("strict-user", "Strict evidence retrieval", "strict"),
            ):
                assert_user_pipeline(
                    browser,
                    username=username,
                    display_name=display_name,
                    pipeline_name=pipeline_name,
                )
        finally:
            browser.close()


def login(page, username: str) -> None:
    page.goto("http://127.0.0.1:8000/app/")
    page.locator("#sign-in").click()
    page.locator("#username").fill(username)
    page.locator("#password").fill("stage2-test-password")
    page.locator("#kc-login").click()
    page.wait_for_url("http://127.0.0.1:8000/app/")
    page.locator("#session-status").get_by_text("Authenticated through OIDC").wait_for()


def assert_user_pipeline(browser, *, username: str, display_name: str, pipeline_name: str) -> None:
    context = browser.new_context()
    try:
        page = context.new_page()
        login(page, username)
        page.locator("#domain option").wait_for(state="attached")
        assert page.locator("#domain option").all_text_contents() == [
            f"{display_name} · {pipeline_name}"
        ]
        assert "code=" not in page.url
        assert "access_token" not in page.url
        assert page.evaluate("localStorage.length") == 0
        page.locator("#query").fill("Run a neutral query")
        page.locator("#submit-query").click()
        page.locator("#request-status").get_by_text("Query completed.").wait_for()
        assert page.locator("#answer").text_content() == f"Response from {pipeline_name}."
        assert f'"{pipeline_name}"' in page.locator("#pipeline-trace").text_content()
    finally:
        context.close()


def browser_policy(tenant_id: str) -> AccessPolicyBundle:
    return AccessPolicyBundle(
        tenant_id=tenant_id,
        version=100,
        group_ids=("direct-readers", "graph-readers", "strict-readers"),
        claim_mappings=(
            ClaimGroupMapping("groups", "direct-users", "direct-readers"),
            ClaimGroupMapping("groups", "graph-users", "graph-readers"),
            ClaimGroupMapping("groups", "strict-users", "strict-readers"),
        ),
        capabilities=(
            CapabilityDescriptor(
                "direct-query",
                "Direct retrieval",
                "Retrieval without graph expansion",
                "general",
                "direct",
                True,
                False,
                100,
            ),
            CapabilityDescriptor(
                "graph-query",
                "Graph-expanded retrieval",
                "Retrieval followed by graph expansion",
                "general",
                "graph",
                True,
                False,
                100,
            ),
            CapabilityDescriptor(
                "strict-query",
                "Strict evidence retrieval",
                "Graph retrieval that requires evidence",
                "general",
                "strict",
                True,
                False,
                100,
            ),
        ),
        grants=(
            GroupCapabilityGrant("direct-readers", "direct-query"),
            GroupCapabilityGrant("graph-readers", "graph-query"),
            GroupCapabilityGrant("strict-readers", "strict-query"),
        ),
    )
