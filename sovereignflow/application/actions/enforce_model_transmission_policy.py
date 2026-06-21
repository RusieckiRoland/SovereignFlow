from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sovereignflow.domain import (
    ContextSecurityRequirement,
    ExternalTransmissionPolicy,
    ModelTransmissionDiagnostic,
    PipelineDefinitionError,
    PipelineExecutionError,
    PolicyViolationError,
    TrustBoundary,
    context_security_requirement,
    model_server_satisfies_requirement,
)

from ._config import _reject_unknown_config_keys, _required_config_string

if TYPE_CHECKING:
    from sovereignflow.application.pipeline import ModelServerRuntime, PipelineContext

_MODEL_TRANSMISSION_POLICY_ALLOWED_KEYS = frozenset(
    {"selected_model_server_id", "external_transmission"}
)


@dataclass(frozen=True)
class ModelTransmissionPolicyConfig:
    selected_model_server_id: str
    external_transmission: ExternalTransmissionPolicy


@dataclass(frozen=True)
class _Decision:
    allowed: bool
    reason_code: str


def _model_transmission_policy_config(step) -> ModelTransmissionPolicyConfig:
    _reject_unknown_config_keys(
        step,
        _MODEL_TRANSMISSION_POLICY_ALLOWED_KEYS,
        "enforce_model_transmission_policy",
    )
    selected_model_server_id = _required_config_string(
        step,
        "selected_model_server_id",
        "enforce_model_transmission_policy",
    )
    raw_external_transmission = _required_config_string(
        step,
        "external_transmission",
        "enforce_model_transmission_policy",
    )
    try:
        external_transmission = ExternalTransmissionPolicy(raw_external_transmission)
    except ValueError as exc:
        raise PipelineDefinitionError(
            "enforce_model_transmission_policy.external_transmission must be allowed or forbidden"
        ) from exc
    return ModelTransmissionPolicyConfig(
        selected_model_server_id=selected_model_server_id,
        external_transmission=external_transmission,
    )


def _model_server_runtime(context: PipelineContext, server_id: str) -> ModelServerRuntime:
    try:
        return context.model_servers[server_id]
    except KeyError as exc:
        raise PipelineExecutionError(f"Model server is not configured: {server_id}") from exc


def _server_transmission_decision(
    *,
    runtime: ModelServerRuntime,
    requirement: ContextSecurityRequirement,
    context: PipelineContext,
    external_transmission: ExternalTransmissionPolicy,
) -> _Decision:
    if runtime.definition.trust_boundary == TrustBoundary.EXTERNAL:
        if external_transmission == ExternalTransmissionPolicy.FORBIDDEN:
            return _Decision(False, "external_transmission_forbidden_by_pipeline")
        if not context.command.authorization.allow_external_model:
            return _Decision(False, "external_model_not_allowed_for_subject")
    decision = model_server_satisfies_requirement(
        model=context.domain.security_model,
        server=runtime.definition,
        requirement=requirement,
    )
    return _Decision(decision.allowed, decision.reason_code)


def _model_transmission_diagnostic(
    *,
    allowed: bool,
    reason_code: str,
    selected_server_id: str,
    final_server_id: str | None,
    rerouted: bool,
    trust_boundary: TrustBoundary | None,
    external_transmission: ExternalTransmissionPolicy,
    requirement: ContextSecurityRequirement,
    checked_chunk_ids: tuple[str, ...],
    blocked_chunk_ids: tuple[str, ...],
) -> ModelTransmissionDiagnostic:
    return ModelTransmissionDiagnostic(
        checked=True,
        allowed=allowed,
        reason_code=reason_code,
        selected_model_server_id=selected_server_id,
        final_model_server_id=final_server_id,
        rerouted=rerouted,
        trust_boundary=trust_boundary,
        external_transmission=external_transmission,
        context_security_requirement=requirement,
        checked_chunk_ids=checked_chunk_ids,
        blocked_chunk_ids=blocked_chunk_ids,
    )


def _model_transmission_decision(
    config: ModelTransmissionPolicyConfig,
    context: PipelineContext,
) -> ModelTransmissionDiagnostic:
    checked_chunk_ids = tuple(hit.chunk.chunk_id for hit in context.hits)
    requirement = context_security_requirement(
        model=context.domain.security_model,
        hits=context.hits,
    )
    selected = _model_server_runtime(context, config.selected_model_server_id)
    selected_decision = _server_transmission_decision(
        runtime=selected,
        requirement=requirement,
        context=context,
        external_transmission=config.external_transmission,
    )
    if selected_decision.allowed:
        context.model = selected.gateway
        return _model_transmission_diagnostic(
            allowed=True,
            reason_code="model_server_allowed",
            selected_server_id=selected.definition.server_id,
            final_server_id=selected.definition.server_id,
            rerouted=False,
            trust_boundary=selected.definition.trust_boundary,
            external_transmission=config.external_transmission,
            requirement=requirement,
            checked_chunk_ids=checked_chunk_ids,
            blocked_chunk_ids=(),
        )
    reroute_id = selected.definition.security_reroute_server_id
    if reroute_id is None:
        return _model_transmission_diagnostic(
            allowed=False,
            reason_code=selected_decision.reason_code,
            selected_server_id=selected.definition.server_id,
            final_server_id=None,
            rerouted=False,
            trust_boundary=selected.definition.trust_boundary,
            external_transmission=config.external_transmission,
            requirement=requirement,
            checked_chunk_ids=checked_chunk_ids,
            blocked_chunk_ids=checked_chunk_ids,
        )
    reroute = _model_server_runtime(context, reroute_id)
    reroute_decision = _server_transmission_decision(
        runtime=reroute,
        requirement=requirement,
        context=context,
        external_transmission=config.external_transmission,
    )
    if not reroute_decision.allowed:
        return _model_transmission_diagnostic(
            allowed=False,
            reason_code=reroute_decision.reason_code,
            selected_server_id=selected.definition.server_id,
            final_server_id=reroute.definition.server_id,
            rerouted=True,
            trust_boundary=reroute.definition.trust_boundary,
            external_transmission=config.external_transmission,
            requirement=requirement,
            checked_chunk_ids=checked_chunk_ids,
            blocked_chunk_ids=checked_chunk_ids,
        )
    context.model = reroute.gateway
    return _model_transmission_diagnostic(
        allowed=True,
        reason_code="model_server_security_rerouted",
        selected_server_id=selected.definition.server_id,
        final_server_id=reroute.definition.server_id,
        rerouted=True,
        trust_boundary=reroute.definition.trust_boundary,
        external_transmission=config.external_transmission,
        requirement=requirement,
        checked_chunk_ids=checked_chunk_ids,
        blocked_chunk_ids=(),
    )


class EnforceModelTransmissionPolicyAction:
    action_id = "enforce_model_transmission_policy"
    behavior_version = "2.0"
    requires = frozenset({"hits", "evidence", "citations"})
    provides = frozenset({"model_transmission_policy"})

    def validate_config(self, step) -> None:
        _model_transmission_policy_config(step)

    def execute(self, step, context: PipelineContext) -> str | None:
        diagnostic = _model_transmission_decision(
            _model_transmission_policy_config(step),
            context,
        )
        context.model_transmission_checked = diagnostic.checked
        context.model_transmission_allowed = diagnostic.allowed
        context.model_transmission_reason_code = diagnostic.reason_code
        context.model_transmission_selected_server_id = diagnostic.selected_model_server_id
        context.model_transmission_final_server_id = diagnostic.final_model_server_id
        context.model_transmission_rerouted = diagnostic.rerouted
        context.model_transmission_trust_boundary = diagnostic.trust_boundary
        context.model_transmission_external_policy = diagnostic.external_transmission
        context.model_transmission_context_requirement = diagnostic.context_security_requirement
        context.model_transmission_checked_chunk_ids = diagnostic.checked_chunk_ids
        context.model_transmission_blocked_chunk_ids = diagnostic.blocked_chunk_ids
        if not diagnostic.allowed:
            raise PolicyViolationError(diagnostic.reason_code)
        return None
