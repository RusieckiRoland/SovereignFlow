from __future__ import annotations

from types import SimpleNamespace

import pytest

from sovereignflow.domain import (
    AccessPolicyBundle,
    AuthorizationContext,
    CapabilityDescriptor,
    ClaimGroupMapping,
    ConflictError,
    DependencyUnavailableError,
    GroupCapabilityGrant,
)
from sovereignflow.infrastructure import (
    PostgreSQLAccessPolicyRepository,
    PostgreSQLSecurityDecisionAudit,
)
from sovereignflow.infrastructure import access_policies as policies_module


class Cursor:
    def __init__(self, *, one=(), all_rows=(), error: Exception | None = None) -> None:
        self.one = list(one)
        self.all_rows = list(all_rows)
        self.error = error
        self.executed = []
        self.executed_many = []

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def execute(self, statement, parameters=None) -> None:
        if self.error is not None:
            raise self.error
        self.executed.append((str(statement), parameters))

    def executemany(self, statement, parameters) -> None:
        if self.error is not None:
            raise self.error
        self.executed_many.append((str(statement), list(parameters)))

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return self.all_rows.pop(0) if self.all_rows else []


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self.cursor_value = cursor
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1


class Database:
    def __init__(self, *connections: Connection) -> None:
        self.connections = list(connections)

    def connect(self, *args, **kwargs):
        return self.connections.pop(0)


def install(monkeypatch, *cursors: Cursor) -> list[Connection]:
    connections = [Connection(cursor) for cursor in cursors]
    database = Database(*connections)
    monkeypatch.setattr(
        policies_module,
        "psycopg_module",
        lambda: SimpleNamespace(connect=database.connect),
    )
    return connections


def repository() -> PostgreSQLAccessPolicyRepository:
    return PostgreSQLAccessPolicyRepository("postgresql://test", timeout_seconds=3)


def authorization() -> AuthorizationContext:
    return AuthorizationContext(
        "subject",
        "tenant-a",
        roles=("realm-reader",),
        groups=("identity-readers",),
    )


def capability() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        "general-query",
        "General query",
        "General RAG",
        "general",
        "default",
        True,
        False,
        2,
    )


def bundle() -> AccessPolicyBundle:
    return AccessPolicyBundle(
        "tenant-a",
        2,
        ("readers",),
        (ClaimGroupMapping("groups", "identity-readers", "readers"),),
        (capability(),),
        (GroupCapabilityGrant("readers", "general-query"),),
    )


def test_resolve_maps_identity_claims_to_internal_groups(monkeypatch) -> None:
    cursor = Cursor(
        one=[(2,)],
        all_rows=[[("readers",)], [("general-query", "default")]],
    )
    install(monkeypatch, cursor)

    policy = repository().resolve(authorization())

    assert policy.group_ids == ("readers",)
    assert policy.capability_ids == ("general-query",)
    assert cursor.executed[1][1] == (
        "tenant-a",
        ["identity-readers"],
        ["realm-reader"],
    )


def test_resolve_is_fail_closed_without_policy_or_mapping(monkeypatch) -> None:
    install(monkeypatch, Cursor(one=[None]))
    assert repository().resolve(authorization()).capability_ids == ()

    install(monkeypatch, Cursor(one=[(3,)], all_rows=[[]]))
    policy = repository().resolve(authorization())
    assert policy.group_ids == ()
    assert policy.policy_version == 3


def test_capability_catalog_is_exact_and_loaded_in_one_query(monkeypatch) -> None:
    policy = repository_policy()
    row = (
        "general-query",
        "General query",
        "General RAG",
        "general",
        "default",
        True,
        False,
    )
    install(monkeypatch, Cursor(all_rows=[[row]]), Cursor(all_rows=[[row]]))
    store = repository()

    assert store.capabilities(policy) == (capability(),)
    assert store.capability("general-query", policy=policy) == capability()
    assert store.capability("missing", policy=policy) is None
    empty_policy = policies_module.ResolvedAccessPolicy(
        "subject",
        "tenant-a",
        (),
        (),
        (),
        2,
    )
    assert store.capabilities(empty_policy) == ()


def test_publish_replaces_policy_atomically_and_checks_version(monkeypatch) -> None:
    cursor = Cursor(one=[(1,)])
    connections = install(monkeypatch, cursor)

    repository().publish(bundle(), expected_version=1)

    assert connections[0].commits == 1
    assert len(cursor.executed_many) == 4
    assert cursor.executed_many[1][1] == [("tenant-a", "groups", "identity-readers", "readers")]

    install(monkeypatch, Cursor(one=[(3,)]))
    with pytest.raises(ConflictError, match="version changed"):
        repository().publish(bundle(), expected_version=1)


def test_policy_health_audit_and_database_failures(monkeypatch) -> None:
    check_cursor = Cursor(one=[(1,)])
    audit_cursor = Cursor()
    connections = install(monkeypatch, check_cursor, audit_cursor)
    repository().check()
    PostgreSQLSecurityDecisionAudit(
        "postgresql://test",
        timeout_seconds=3,
    ).record(
        request_id="request-1",
        subject="private-subject",
        tenant_id="tenant-a",
        capability_id="general-query",
        pipeline_name="default",
        allowed=True,
        reason_code="pipeline_access_allowed",
        policy_version=2,
    )

    parameters = audit_cursor.executed[0][1]
    assert parameters[0] == "request-1"
    assert parameters[1] != "private-subject"
    assert len(parameters[1]) == 64
    assert connections[1].commits == 1

    for operation in (
        lambda: repository().resolve(authorization()),
        lambda: repository().capabilities(repository_policy()),
        lambda: repository().publish(bundle(), expected_version=None),
        lambda: repository().check(),
        lambda: PostgreSQLSecurityDecisionAudit(
            "postgresql://test",
            timeout_seconds=3,
        ).record(
            request_id="request",
            subject="subject",
            tenant_id="tenant",
            capability_id="capability",
            pipeline_name=None,
            allowed=False,
            reason_code="denied",
            policy_version=1,
        ),
    ):
        install(monkeypatch, Cursor(error=RuntimeError("down")))
        with pytest.raises(DependencyUnavailableError):
            operation()

    monkeypatch.setattr(
        policies_module,
        "psycopg_module",
        lambda: (_ for _ in ()).throw(DependencyUnavailableError("controlled")),
    )
    with pytest.raises(DependencyUnavailableError, match="controlled"):
        repository().resolve(authorization())


def repository_policy():
    return policies_module.ResolvedAccessPolicy(
        "subject",
        "tenant-a",
        ("readers",),
        ("general-query",),
        ("default",),
        2,
    )
