from __future__ import annotations

from collections.abc import Collection, Mapping

from sovereignflow.domain import AccessPolicyBundle, ValidationError

from .ports import AccessPolicyRepositoryPort


class PolicyAdministrationService:
    def __init__(
        self,
        repository: AccessPolicyRepositoryPort,
        *,
        domain_pipelines: Mapping[str, str | Collection[str]],
    ) -> None:
        self._repository = repository
        self._domain_pipelines = {
            domain: frozenset((pipelines,) if isinstance(pipelines, str) else pipelines)
            for domain, pipelines in domain_pipelines.items()
        }

    def publish(
        self,
        bundle: AccessPolicyBundle,
        *,
        expected_version: int | None,
    ) -> None:
        for capability in bundle.capabilities:
            configured_pipelines = self._domain_pipelines.get(capability.domain)
            if configured_pipelines is None:
                raise ValidationError("Policy capability references an unknown domain")
            if capability.pipeline_name not in configured_pipelines:
                raise ValidationError(
                    "Policy capability references a pipeline not configured for its domain"
                )
        self._repository.publish(bundle, expected_version=expected_version)
