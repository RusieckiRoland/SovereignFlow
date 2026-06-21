from __future__ import annotations

import hashlib

from sovereignflow.domain import (
    AccessPolicyBundle,
    AuthorizationContext,
    CapabilityDescriptor,
    ConflictError,
    DependencyUnavailableError,
    ResolvedAccessPolicy,
    SovereignFlowError,
)

from .postgres_support import psycopg_module


class PostgreSQLAccessPolicyRepository:
    name = "access_policies"

    def __init__(self, connection_url: str, *, timeout_seconds: int) -> None:
        self._connection_url = connection_url
        self._timeout_seconds = timeout_seconds

    def resolve(self, authorization: AuthorizationContext) -> ResolvedAccessPolicy:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    SELECT version
                    FROM sf.policy_versions
                    WHERE tenant_id = %s AND active = TRUE
                    """,
                    (authorization.tenant_id,),
                )
                version_row = cursor.fetchone()
                if version_row is None:
                    return _empty_policy(authorization, policy_version=1)
                policy_version = int(version_row[0])
                cursor.execute(
                    """
                    SELECT DISTINCT mapping.group_id
                    FROM sf.claim_group_mappings mapping
                    JOIN sf.security_groups security_group
                      ON security_group.tenant_id = mapping.tenant_id
                     AND security_group.group_id = mapping.group_id
                    WHERE mapping.tenant_id = %s
                      AND security_group.active = TRUE
                      AND (
                        (mapping.claim_name = 'groups' AND mapping.claim_value = ANY(%s))
                        OR
                        (mapping.claim_name = 'roles' AND mapping.claim_value = ANY(%s))
                      )
                    ORDER BY mapping.group_id
                    """,
                    (
                        authorization.tenant_id,
                        list(authorization.groups),
                        list(authorization.roles),
                    ),
                )
                groups = tuple(row[0] for row in cursor.fetchall())
                if not groups:
                    return _empty_policy(
                        authorization,
                        policy_version=policy_version,
                    )
                cursor.execute(
                    """
                    SELECT DISTINCT assignment.capability_id, capability.pipeline_name
                    FROM sf.group_capabilities assignment
                    JOIN sf.capabilities capability
                      ON capability.tenant_id = assignment.tenant_id
                     AND capability.capability_id = assignment.capability_id
                    WHERE assignment.tenant_id = %s
                      AND assignment.group_id = ANY(%s)
                      AND capability.active = TRUE
                    ORDER BY assignment.capability_id, capability.pipeline_name
                    """,
                    (authorization.tenant_id, list(groups)),
                )
                rows = cursor.fetchall()
            return ResolvedAccessPolicy(
                subject=authorization.subject,
                tenant_id=authorization.tenant_id,
                group_ids=groups,
                capability_ids=tuple(row[0] for row in rows),
                pipeline_names=tuple(row[1] for row in rows),
                policy_version=policy_version,
            )
        except SovereignFlowError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("Access policy repository is unavailable") from exc

    def capabilities(
        self,
        policy: ResolvedAccessPolicy,
    ) -> tuple[CapabilityDescriptor, ...]:
        if not policy.capability_ids:
            return ()
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    SELECT capability_id, display_name, description, domain,
                           pipeline_name, diagnostics_available, external_model
                    FROM sf.capabilities
                    WHERE tenant_id = %s
                      AND capability_id = ANY(%s)
                      AND active = TRUE
                    ORDER BY capability_id
                    """,
                    (policy.tenant_id, list(policy.capability_ids)),
                )
                rows = cursor.fetchall()
            return tuple(
                CapabilityDescriptor(*row, policy.policy_version)
                for row in rows
                if row[0] in policy.capability_ids and row[4] in policy.pipeline_names
            )
        except Exception as exc:
            raise DependencyUnavailableError("Access policy repository is unavailable") from exc

    def capability(
        self,
        capability_id: str,
        *,
        policy: ResolvedAccessPolicy,
    ) -> CapabilityDescriptor | None:
        if capability_id not in policy.capability_ids:
            return None
        return next(
            (
                capability
                for capability in self.capabilities(policy)
                if capability.capability_id == capability_id
            ),
            None,
        )

    def publish(
        self,
        bundle: AccessPolicyBundle,
        *,
        expected_version: int | None,
    ) -> None:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    SELECT version
                    FROM sf.policy_versions
                    WHERE tenant_id = %s
                    FOR UPDATE
                    """,
                    (bundle.tenant_id,),
                )
                version_row = cursor.fetchone()
                current_version = None if version_row is None else int(version_row[0])
                if expected_version is not None and current_version != expected_version:
                    raise ConflictError("Access policy version changed before publication")
                cursor.execute(
                    """
                    INSERT INTO sf.policy_versions (
                        tenant_id, version, active, updated_at
                    ) VALUES (%s, %s, TRUE, NOW())
                    ON CONFLICT (tenant_id) DO UPDATE
                    SET version = EXCLUDED.version,
                        active = TRUE,
                        updated_at = NOW()
                    """,
                    (bundle.tenant_id, bundle.version),
                )
                cursor.execute(
                    "DELETE FROM sf.claim_group_mappings WHERE tenant_id = %s",
                    (bundle.tenant_id,),
                )
                cursor.execute(
                    "DELETE FROM sf.group_capabilities WHERE tenant_id = %s",
                    (bundle.tenant_id,),
                )
                cursor.execute(
                    "DELETE FROM sf.capabilities WHERE tenant_id = %s",
                    (bundle.tenant_id,),
                )
                cursor.execute(
                    "DELETE FROM sf.security_groups WHERE tenant_id = %s",
                    (bundle.tenant_id,),
                )
                cursor.executemany(
                    """
                    INSERT INTO sf.security_groups (
                        tenant_id, group_id, active
                    ) VALUES (%s, %s, TRUE)
                    """,
                    [(bundle.tenant_id, group_id) for group_id in bundle.group_ids],
                )
                cursor.executemany(
                    """
                    INSERT INTO sf.claim_group_mappings (
                        tenant_id, claim_name, claim_value, group_id
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    [
                        (
                            bundle.tenant_id,
                            mapping.claim_name,
                            mapping.claim_value,
                            mapping.group_id,
                        )
                        for mapping in bundle.claim_mappings
                    ],
                )
                cursor.executemany(
                    """
                    INSERT INTO sf.capabilities (
                        tenant_id, capability_id, display_name, description,
                        domain, pipeline_name, diagnostics_available,
                        external_model, active
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                    """,
                    [
                        (
                            bundle.tenant_id,
                            capability.capability_id,
                            capability.display_name,
                            capability.description,
                            capability.domain,
                            capability.pipeline_name,
                            capability.diagnostics_available,
                            capability.external_model,
                        )
                        for capability in bundle.capabilities
                    ],
                )
                cursor.executemany(
                    """
                    INSERT INTO sf.group_capabilities (
                        tenant_id, group_id, capability_id
                    ) VALUES (%s, %s, %s)
                    """,
                    [
                        (bundle.tenant_id, grant.group_id, grant.capability_id)
                        for grant in bundle.grants
                    ],
                )
                cursor.execute(
                    """
                    INSERT INTO sf.policy_changes (
                        tenant_id, previous_version, published_version
                    ) VALUES (%s, %s, %s)
                    """,
                    (bundle.tenant_id, current_version, bundle.version),
                )
                connection.commit()
        except SovereignFlowError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("Access policy repository is unavailable") from exc

    def check(self) -> None:
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT 1 FROM sf.policy_versions LIMIT 1")
                cursor.fetchone()
        except Exception as exc:
            raise DependencyUnavailableError("Access policy repository is unavailable") from exc


class PostgreSQLSecurityDecisionAudit:
    def __init__(self, connection_url: str, *, timeout_seconds: int) -> None:
        self._connection_url = connection_url
        self._timeout_seconds = timeout_seconds

    def record(
        self,
        *,
        request_id: str,
        subject: str,
        tenant_id: str,
        capability_id: str,
        pipeline_name: str | None,
        allowed: bool,
        reason_code: str,
        policy_version: int,
    ) -> None:
        subject_hash = hashlib.sha256(subject.encode("utf-8")).hexdigest()
        try:
            psycopg = psycopg_module()
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    INSERT INTO sf.security_decisions (
                        request_id, subject_hash, tenant_id, capability_id,
                        pipeline_name, allowed, reason_code, policy_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request_id,
                        subject_hash,
                        tenant_id,
                        capability_id,
                        pipeline_name,
                        allowed,
                        reason_code,
                        policy_version,
                    ),
                )
                connection.commit()
        except Exception as exc:
            raise DependencyUnavailableError("Security decision audit is unavailable") from exc


def _empty_policy(
    authorization: AuthorizationContext,
    *,
    policy_version: int,
) -> ResolvedAccessPolicy:
    return ResolvedAccessPolicy(
        subject=authorization.subject,
        tenant_id=authorization.tenant_id,
        group_ids=(),
        capability_ids=(),
        pipeline_names=(),
        policy_version=policy_version,
    )
