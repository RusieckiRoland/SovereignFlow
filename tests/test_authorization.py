from __future__ import annotations

import pytest
from conftest import authorization_context

from sovereignflow.application import PipelineAuthorizationService
from sovereignflow.domain import (
    AccessPolicyBundle,
    CapabilityDescriptor,
    ClaimGroupMapping,
    GroupCapabilityGrant,
    PipelineAccessDecision,
    PolicyViolationError,
    ResolvedAccessPolicy,
    ValidationError,
)


class Repository:
    def __init__(self, capability=None) -> None:
        self.capability_value = capability
        self.policy = ResolvedAccessPolicy(
            "subject",
            "tenant-a",
            ("group-a",),
            (capability.capability_id,) if capability else (),
            (capability.pipeline_name,) if capability else (),
            2,
        )

    def resolve(self, authorization):
        return self.policy

    def capabilities(self, policy):
        return () if self.capability_value is None else (self.capability_value,)

    def capability(self, capability_id, *, policy):
        if self.capability_value and capability_id == self.capability_value.capability_id:
            return self.capability_value
        return None


class Audit:
    def __init__(self) -> None:
        self.records = []

    def record(self, **values) -> None:
        self.records.append(values)


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


def test_pipeline_authorization_lists_and_allows_capability() -> None:
    selected = capability()
    audit = Audit()
    service = PipelineAuthorizationService(Repository(selected), audit)

    authorization = authorization_context()
    assert service.catalog(authorization) == (selected,)
    decision = service.authorize(
        request_id="request-1",
        capability_id="general-query",
        authorization=authorization,
    )

    assert decision.allowed is True
    assert decision.capability == selected
    assert audit.records[0]["reason_code"] == "pipeline_access_allowed"


def test_pipeline_authorization_is_fail_closed_and_audited() -> None:
    audit = Audit()
    service = PipelineAuthorizationService(Repository(), audit)

    with pytest.raises(PolicyViolationError, match="not available"):
        service.authorize(
            request_id="request-2",
            capability_id="missing",
            authorization=authorization_context(),
        )

    assert audit.records[0]["allowed"] is False
    assert audit.records[0]["pipeline_name"] is None


def test_access_models_validate_invariants() -> None:
    selected = capability()
    with pytest.raises(ValidationError, match="policy_version"):
        ResolvedAccessPolicy("subject", "tenant", (), (), (), 0)
    with pytest.raises(ValidationError, match="policy_version"):
        CapabilityDescriptor("id", "name", "", "domain", "pipeline", False, False, 0)
    with pytest.raises(ValidationError, match="requires capability"):
        PipelineAccessDecision(
            True,
            "allowed",
            None,
            ResolvedAccessPolicy("subject", "tenant", (), (), (), 1),
        )
    with pytest.raises(ValidationError, match="groups or roles"):
        ClaimGroupMapping("unknown", "value", "group")
    with pytest.raises(ValidationError, match="unknown group"):
        AccessPolicyBundle(
            "tenant",
            1,
            (),
            (ClaimGroupMapping("groups", "source", "missing"),),
            (),
            (),
        )
    with pytest.raises(ValidationError, match="unknown capability"):
        AccessPolicyBundle(
            "tenant",
            1,
            ("group",),
            (),
            (),
            (GroupCapabilityGrant("group", "missing"),),
        )
    with pytest.raises(ValidationError, match="version must be positive"):
        AccessPolicyBundle("tenant", 0, (), (), (), ())
    with pytest.raises(ValidationError, match="capabilities must be unique"):
        AccessPolicyBundle("tenant", 2, (), (), (selected, selected), ())
    with pytest.raises(ValidationError, match="must match bundle version"):
        AccessPolicyBundle("tenant", 3, (), (), (selected,), ())
    with pytest.raises(ValidationError, match="unknown group"):
        AccessPolicyBundle(
            "tenant",
            2,
            (),
            (),
            (selected,),
            (GroupCapabilityGrant("missing", selected.capability_id),),
        )
    mapping = ClaimGroupMapping("groups", "identity", "group")
    grant = GroupCapabilityGrant("group", selected.capability_id)
    with pytest.raises(ValidationError, match="claim mappings must be unique"):
        AccessPolicyBundle(
            "tenant",
            2,
            ("group",),
            (mapping, mapping),
            (selected,),
            (grant,),
        )
    with pytest.raises(ValidationError, match="grants must be unique"):
        AccessPolicyBundle(
            "tenant",
            2,
            ("group",),
            (mapping,),
            (selected,),
            (grant, grant),
        )


def test_pipeline_authorization_rejects_diagnostics_not_exposed_by_capability() -> None:
    selected = CapabilityDescriptor(
        "general-query",
        "General query",
        "General RAG",
        "general",
        "default",
        False,
        False,
        2,
    )
    audit = Audit()
    service = PipelineAuthorizationService(Repository(selected), audit)

    with pytest.raises(PolicyViolationError, match="not available"):
        service.authorize(
            request_id="request-diagnostics",
            capability_id=selected.capability_id,
            authorization=authorization_context(),
            diagnostics_requested=True,
        )

    assert audit.records == [
        {
            "request_id": "request-diagnostics",
            "subject": "user-1",
            "tenant_id": "tenant-a",
            "capability_id": "general-query",
            "pipeline_name": "default",
            "allowed": False,
            "reason_code": "capability_diagnostics_denied",
            "policy_version": 2,
        }
    ]
