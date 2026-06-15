from __future__ import annotations

import pytest

from sovereignflow.application import PolicyAdministrationService
from sovereignflow.domain import (
    AccessPolicyBundle,
    CapabilityDescriptor,
    ClaimGroupMapping,
    GroupCapabilityGrant,
    ValidationError,
)


class Repository:
    def __init__(self) -> None:
        self.published = []

    def publish(self, bundle, *, expected_version) -> None:
        self.published.append((bundle, expected_version))


def bundle(*, domain: str = "general", pipeline: str = "default") -> AccessPolicyBundle:
    return AccessPolicyBundle(
        tenant_id="tenant-a",
        version=2,
        group_ids=("readers",),
        claim_mappings=(ClaimGroupMapping("groups", "identity-readers", "readers"),),
        capabilities=(
            CapabilityDescriptor(
                "general-query",
                "General query",
                "General RAG",
                domain,
                pipeline,
                True,
                False,
                2,
            ),
        ),
        grants=(GroupCapabilityGrant("readers", "general-query"),),
    )


def test_policy_administration_validates_and_publishes_complete_bundle() -> None:
    repository = Repository()
    service = PolicyAdministrationService(
        repository,
        domain_pipelines={"general": "default"},
    )
    policy = bundle()

    service.publish(policy, expected_version=1)

    assert repository.published == [(policy, 1)]


def test_policy_administration_accepts_each_configured_pipeline() -> None:
    repository = Repository()
    service = PolicyAdministrationService(
        repository,
        domain_pipelines={"general": ("direct", "graph", "strict")},
    )
    policy = bundle(pipeline="strict")

    service.publish(policy, expected_version=None)

    assert repository.published == [(policy, None)]


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        (bundle(domain="missing"), "unknown domain"),
        (bundle(pipeline="other"), "not configured"),
    ],
)
def test_policy_administration_rejects_invalid_catalog_references(
    policy,
    message: str,
) -> None:
    service = PolicyAdministrationService(
        Repository(),
        domain_pipelines={"general": "default"},
    )

    with pytest.raises(ValidationError, match=message):
        service.publish(policy, expected_version=None)
