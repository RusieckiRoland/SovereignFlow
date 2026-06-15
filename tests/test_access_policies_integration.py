from __future__ import annotations

import os
import uuid

import pytest

from sovereignflow.application import PipelineAuthorizationService
from sovereignflow.domain import (
    AccessPolicyBundle,
    AuthorizationContext,
    CapabilityDescriptor,
    ClaimGroupMapping,
    GroupCapabilityGrant,
    PolicyViolationError,
)
from sovereignflow.infrastructure import (
    PostgreSQLAccessPolicyRepository,
    PostgreSQLMigrationRunner,
    PostgreSQLSecurityDecisionAudit,
)


@pytest.mark.integration
def test_postgresql_policies_map_claims_refresh_without_restart_and_audit() -> None:
    connection_url = os.getenv("SOVEREIGNFLOW_TEST_POSTGRES_URL")
    if not connection_url:
        pytest.skip("PostgreSQL integration service is not configured")
    import psycopg

    tenant_id = f"policy-{uuid.uuid4().hex}"
    repository = PostgreSQLAccessPolicyRepository(connection_url, timeout_seconds=5)
    audit = PostgreSQLSecurityDecisionAudit(connection_url, timeout_seconds=5)
    service = PipelineAuthorizationService(repository, audit)
    PostgreSQLMigrationRunner(connection_url, timeout_seconds=5).migrate()
    version_one = policy_bundle(tenant_id, version=1, include_advanced=False)
    version_two = policy_bundle(tenant_id, version=2, include_advanced=True)
    authorization = AuthorizationContext(
        subject="integration-subject",
        tenant_id=tenant_id,
        groups=("keycloak-readers",),
        roles=("realm-advanced",),
    )
    request_id = f"request-{uuid.uuid4().hex}"
    try:
        repository.publish(version_one, expected_version=None)
        assert [item.capability_id for item in service.catalog(authorization)] == ["basic-query"]
        with pytest.raises(PolicyViolationError):
            service.authorize(
                request_id=request_id,
                capability_id="advanced-query",
                authorization=authorization,
            )

        repository.publish(version_two, expected_version=1)
        assert [item.capability_id for item in service.catalog(authorization)] == [
            "advanced-query",
            "basic-query",
        ]
        decision = service.authorize(
            request_id=f"{request_id}-allowed",
            capability_id="advanced-query",
            authorization=authorization,
        )
        assert decision.policy.group_ids == ("advanced", "readers")
        assert decision.policy.policy_version == 2

        with psycopg.connect(connection_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT allowed, reason_code, subject_hash
                FROM public.sovereignflow_security_decisions
                WHERE request_id IN (%s, %s)
                ORDER BY request_id
                """,
                (request_id, f"{request_id}-allowed"),
            )
            rows = cursor.fetchall()
        assert {row[0] for row in rows} == {False, True}
        assert all(row[2] != "integration-subject" for row in rows)
    finally:
        with psycopg.connect(connection_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM public.sovereignflow_security_decisions WHERE tenant_id = %s",
                (tenant_id,),
            )
            cursor.execute(
                "DELETE FROM public.sovereignflow_policy_versions WHERE tenant_id = %s",
                (tenant_id,),
            )
            connection.commit()


def policy_bundle(
    tenant_id: str,
    *,
    version: int,
    include_advanced: bool,
) -> AccessPolicyBundle:
    capabilities = [
        CapabilityDescriptor(
            "basic-query",
            "Basic query",
            "Basic RAG",
            "general",
            "default",
            False,
            False,
            version,
        )
    ]
    groups = ["readers"]
    mappings = [ClaimGroupMapping("groups", "keycloak-readers", "readers")]
    grants = [GroupCapabilityGrant("readers", "basic-query")]
    if include_advanced:
        groups.append("advanced")
        mappings.append(ClaimGroupMapping("roles", "realm-advanced", "advanced"))
        capabilities.append(
            CapabilityDescriptor(
                "advanced-query",
                "Advanced query",
                "Advanced RAG",
                "general",
                "default",
                True,
                False,
                version,
            )
        )
        grants.append(GroupCapabilityGrant("advanced", "advanced-query"))
    return AccessPolicyBundle(
        tenant_id,
        version,
        tuple(groups),
        tuple(mappings),
        tuple(capabilities),
        tuple(grants),
    )
