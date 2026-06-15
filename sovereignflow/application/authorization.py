from __future__ import annotations

from sovereignflow.domain import (
    AuthorizationContext,
    CapabilityDescriptor,
    PipelineAccessDecision,
    PolicyViolationError,
)

from .ports import AccessPolicyRepositoryPort, SecurityDecisionAuditPort


class PipelineAuthorizationService:
    def __init__(
        self,
        repository: AccessPolicyRepositoryPort,
        audit: SecurityDecisionAuditPort,
    ) -> None:
        self._repository = repository
        self._audit = audit

    def catalog(
        self,
        authorization: AuthorizationContext,
    ) -> tuple[CapabilityDescriptor, ...]:
        policy = self._repository.resolve(authorization)
        return tuple(self._repository.capabilities(policy))

    def authorize(
        self,
        *,
        request_id: str,
        capability_id: str,
        authorization: AuthorizationContext,
        diagnostics_requested: bool = False,
    ) -> PipelineAccessDecision:
        policy = self._repository.resolve(authorization)
        capability = self._repository.capability(capability_id, policy=policy)
        allowed = (
            capability is not None
            and capability.capability_id in policy.capability_ids
            and capability.pipeline_name in policy.pipeline_names
            and (not diagnostics_requested or capability.diagnostics_available)
        )
        if allowed:
            reason_code = "pipeline_access_allowed"
        elif (
            capability is not None
            and diagnostics_requested
            and not capability.diagnostics_available
        ):
            reason_code = "capability_diagnostics_denied"
        else:
            reason_code = "pipeline_access_denied"
        self._audit.record(
            request_id=request_id,
            subject=authorization.subject,
            tenant_id=authorization.tenant_id,
            capability_id=capability_id,
            pipeline_name=capability.pipeline_name if capability is not None else None,
            allowed=bool(allowed),
            reason_code=reason_code,
            policy_version=policy.policy_version,
        )
        decision = PipelineAccessDecision(
            allowed=bool(allowed),
            reason_code=reason_code,
            capability=capability if allowed else None,
            policy=policy,
        )
        if not decision.allowed:
            raise PolicyViolationError("The requested capability is not available")
        return decision
