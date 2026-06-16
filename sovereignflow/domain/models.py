from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .errors import ValidationError


def _required(value: str, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValidationError(f"{field_name} is required")
    return normalized


def _immutable_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def _normalized_values(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    return tuple(sorted({_required(value, f"{field_name}[]") for value in values}))


class SearchMode(StrEnum):
    SEMANTIC = "semantic"
    BM25 = "bm25"
    HYBRID = "hybrid"


class GraphDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


class SecurityModelKind(StrEnum):
    NONE = "none"
    CLEARANCE_LEVEL = "clearance_level"
    CLASSIFICATION_LABELS = "classification_labels"


class TrustBoundary(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class ExternalTransmissionPolicy(StrEnum):
    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class ClearanceLevelModel:
    levels: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.levels:
            raise ValidationError("ClearanceLevelModel.levels cannot be empty")
        normalized: dict[str, int] = {}
        for label, value in self.levels.items():
            normalized_label = _required(label, "ClearanceLevelModel.levels[]")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValidationError("ClearanceLevelModel levels must be non-negative integers")
            normalized[normalized_label] = value
        if len(normalized) != len(self.levels):
            raise ValidationError("ClearanceLevelModel levels must be unique")
        object.__setattr__(self, "levels", MappingProxyType(normalized))

    def value(self, label: str, field_name: str) -> int:
        normalized = _required(label, field_name)
        try:
            return int(self.levels[normalized])
        except KeyError as exc:
            raise ValidationError(f"{field_name} is not allowed by the clearance model") from exc

    def allowed_document_labels(self, subject_label: str) -> tuple[str, ...]:
        subject_value = self.value(subject_label, "SubjectSecurity.clearance_label")
        return tuple(
            sorted(label for label, value in self.levels.items() if value <= subject_value)
        )


@dataclass(frozen=True)
class ClassificationLabelsModel:
    labels_universe_subset: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "labels_universe_subset",
            _normalized_values(
                self.labels_universe_subset,
                "ClassificationLabelsModel.labels_universe_subset",
            ),
        )
        if not self.labels_universe_subset:
            raise ValidationError(
                "ClassificationLabelsModel.labels_universe_subset cannot be empty"
            )

    def validate_labels(self, labels: tuple[str, ...], field_name: str) -> tuple[str, ...]:
        normalized = _normalized_values(labels, field_name)
        unknown = set(normalized) - set(self.labels_universe_subset)
        if unknown:
            raise ValidationError(
                f"{field_name} contains labels outside the configured universe: "
                + ", ".join(sorted(unknown))
            )
        return normalized


@dataclass(frozen=True)
class SecurityModel:
    kind: SecurityModelKind
    clearance_level: ClearanceLevelModel | None = None
    classification_labels: ClassificationLabelsModel | None = None

    @staticmethod
    def none() -> SecurityModel:
        return SecurityModel(SecurityModelKind.NONE)

    def __post_init__(self) -> None:
        if self.kind == SecurityModelKind.NONE:
            if self.clearance_level is not None or self.classification_labels is not None:
                raise ValidationError("SecurityModel none must not define security details")
            return
        if self.kind == SecurityModelKind.CLEARANCE_LEVEL:
            if self.clearance_level is None or self.classification_labels is not None:
                raise ValidationError("SecurityModel clearance_level requires levels only")
            return
        if self.kind == SecurityModelKind.CLASSIFICATION_LABELS:
            if self.classification_labels is None or self.clearance_level is not None:
                raise ValidationError(
                    "SecurityModel classification_labels requires labels universe only"
                )
            return
        raise ValidationError("SecurityModel.kind is invalid")


@dataclass(frozen=True)
class DocumentSecurity:
    clearance_label: str | None = None
    classification_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.clearance_label is not None:
            object.__setattr__(
                self,
                "clearance_label",
                _required(self.clearance_label, "DocumentSecurity.clearance_label"),
            )
        object.__setattr__(
            self,
            "classification_labels",
            _normalized_values(
                self.classification_labels,
                "DocumentSecurity.classification_labels",
            ),
        )


@dataclass(frozen=True)
class SubjectSecurity:
    clearance_label: str | None = None
    classification_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.clearance_label is not None:
            object.__setattr__(
                self,
                "clearance_label",
                _required(self.clearance_label, "SubjectSecurity.clearance_label"),
            )
        object.__setattr__(
            self,
            "classification_labels",
            _normalized_values(
                self.classification_labels,
                "SubjectSecurity.classification_labels",
            ),
        )


@dataclass(frozen=True)
class SecurityDecision:
    allowed: bool
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_code", _required(self.reason_code, "reason_code"))


def document_visible_to_subject(
    *,
    model: SecurityModel,
    document: DocumentSecurity,
    subject: SubjectSecurity,
) -> SecurityDecision:
    if model.kind == SecurityModelKind.NONE:
        return SecurityDecision(True, "security_model_none")
    if model.kind == SecurityModelKind.CLEARANCE_LEVEL:
        if document.clearance_label is None:
            return SecurityDecision(False, "document_clearance_missing")
        if subject.clearance_label is None:
            return SecurityDecision(False, "subject_clearance_missing")
        clearance = _required_model(model.clearance_level, "clearance_level")
        document_value = clearance.value(
            document.clearance_label,
            "DocumentSecurity.clearance_label",
        )
        subject_value = clearance.value(
            subject.clearance_label,
            "SubjectSecurity.clearance_label",
        )
        return SecurityDecision(
            subject_value >= document_value,
            "clearance_allowed" if subject_value >= document_value else "clearance_denied",
        )
    labels_model = _required_model(model.classification_labels, "classification_labels")
    document_labels = labels_model.validate_labels(
        document.classification_labels,
        "DocumentSecurity.classification_labels",
    )
    subject_labels = set(
        labels_model.validate_labels(
            subject.classification_labels,
            "SubjectSecurity.classification_labels",
        )
    )
    allowed = set(document_labels).issubset(subject_labels)
    return SecurityDecision(
        allowed,
        "classification_labels_allowed" if allowed else "classification_labels_denied",
    )


def acl_visible_to_subject(
    *,
    document_acl_labels: tuple[str, ...],
    subject_acl_labels: tuple[str, ...],
) -> SecurityDecision:
    document_labels = set(_normalized_values(document_acl_labels, "document_acl_labels"))
    if not document_labels:
        return SecurityDecision(True, "acl_public")
    subject_labels = set(_normalized_values(subject_acl_labels, "subject_acl_labels"))
    allowed = bool(document_labels.intersection(subject_labels))
    return SecurityDecision(allowed, "acl_allowed" if allowed else "acl_denied")


def _required_model(value, name: str):
    if value is None:
        raise ValidationError(f"SecurityModel.{name} is required")
    return value


@dataclass(frozen=True)
class ModelServerSecurityProfile:
    security_model_kind: SecurityModelKind
    clearance_label: str | None = None
    classification_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.security_model_kind == SecurityModelKind.NONE:
            if self.clearance_label is not None or self.classification_labels:
                raise ValidationError("ModelServerSecurityProfile none must not define labels")
            return
        if self.security_model_kind == SecurityModelKind.CLEARANCE_LEVEL:
            if self.clearance_label is None or self.classification_labels:
                raise ValidationError(
                    "ModelServerSecurityProfile clearance_level requires clearance_label only"
                )
            object.__setattr__(
                self,
                "clearance_label",
                _required(self.clearance_label, "ModelServerSecurityProfile.clearance_label"),
            )
            return
        if self.security_model_kind == SecurityModelKind.CLASSIFICATION_LABELS:
            if self.clearance_label is not None:
                raise ValidationError(
                    "ModelServerSecurityProfile classification_labels cannot define clearance_label"
                )
            object.__setattr__(
                self,
                "classification_labels",
                _normalized_values(
                    self.classification_labels,
                    "ModelServerSecurityProfile.classification_labels",
                ),
            )
            return
        raise ValidationError("ModelServerSecurityProfile.security_model_kind is invalid")


@dataclass(frozen=True)
class ModelServerDefinition:
    server_id: str
    trust_boundary: TrustBoundary
    security_profile: ModelServerSecurityProfile
    security_reroute_server_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "server_id",
            _required(self.server_id, "ModelServerDefinition.server_id"),
        )
        if self.security_reroute_server_id is not None:
            object.__setattr__(
                self,
                "security_reroute_server_id",
                _required(
                    self.security_reroute_server_id,
                    "ModelServerDefinition.security_reroute_server_id",
                ),
            )


@dataclass(frozen=True)
class ContextSecurityRequirement:
    security_model_kind: SecurityModelKind
    clearance_label: str | None = None
    classification_labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.security_model_kind == SecurityModelKind.CLEARANCE_LEVEL:
            if self.clearance_label is not None:
                object.__setattr__(
                    self,
                    "clearance_label",
                    _required(
                        self.clearance_label,
                        "ContextSecurityRequirement.clearance_label",
                    ),
                )
            if self.classification_labels:
                raise ValidationError(
                    "ContextSecurityRequirement clearance_level cannot define labels"
                )
            return
        if self.security_model_kind == SecurityModelKind.CLASSIFICATION_LABELS:
            if self.clearance_label is not None:
                raise ValidationError(
                    "ContextSecurityRequirement classification_labels cannot define clearance"
                )
            object.__setattr__(
                self,
                "classification_labels",
                _normalized_values(
                    self.classification_labels,
                    "ContextSecurityRequirement.classification_labels",
                ),
            )
            return
        if self.security_model_kind == SecurityModelKind.NONE:
            if self.clearance_label is not None or self.classification_labels:
                raise ValidationError("ContextSecurityRequirement none must not define labels")
            return
        raise ValidationError("ContextSecurityRequirement.security_model_kind is invalid")


@dataclass(frozen=True)
class ModelServerSelection:
    allowed: bool
    reason_code: str
    selected_server_id: str
    final_server_id: str | None
    rerouted: bool
    trust_boundary: TrustBoundary | None
    requirement: ContextSecurityRequirement

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason_code",
            _required(self.reason_code, "ModelServerSelection.reason_code"),
        )
        object.__setattr__(
            self,
            "selected_server_id",
            _required(self.selected_server_id, "ModelServerSelection.selected_server_id"),
        )
        if self.final_server_id is not None:
            object.__setattr__(
                self,
                "final_server_id",
                _required(self.final_server_id, "ModelServerSelection.final_server_id"),
            )


def context_security_requirement(
    *,
    model: SecurityModel,
    hits: tuple[SearchHit, ...],
) -> ContextSecurityRequirement:
    if model.kind == SecurityModelKind.NONE:
        return ContextSecurityRequirement(SecurityModelKind.NONE)
    if model.kind == SecurityModelKind.CLEARANCE_LEVEL:
        clearance = _required_model(model.clearance_level, "clearance_level")
        highest_label: str | None = None
        highest_value = -1
        for hit in hits:
            label = hit.chunk.security.clearance_label
            if label is None:
                raise ValidationError("Retrieved context contains missing clearance label")
            value = clearance.value(label, "DocumentSecurity.clearance_label")
            if value > highest_value:
                highest_label = label
                highest_value = value
        return ContextSecurityRequirement(
            SecurityModelKind.CLEARANCE_LEVEL,
            clearance_label=highest_label,
        )
    labels_model = _required_model(model.classification_labels, "classification_labels")
    required: set[str] = set()
    for hit in hits:
        required.update(
            labels_model.validate_labels(
                hit.chunk.security.classification_labels,
                "DocumentSecurity.classification_labels",
            )
        )
    return ContextSecurityRequirement(
        SecurityModelKind.CLASSIFICATION_LABELS,
        classification_labels=tuple(sorted(required)),
    )


def model_server_satisfies_requirement(
    *,
    model: SecurityModel,
    server: ModelServerDefinition,
    requirement: ContextSecurityRequirement,
) -> SecurityDecision:
    if server.security_profile.security_model_kind != model.kind:
        return SecurityDecision(False, "model_server_security_model_mismatch")
    if model.kind == SecurityModelKind.NONE:
        return SecurityDecision(True, "model_server_allowed")
    if model.kind == SecurityModelKind.CLEARANCE_LEVEL:
        if requirement.clearance_label is None:
            return SecurityDecision(True, "model_server_allowed_empty_context")
        clearance = _required_model(model.clearance_level, "clearance_level")
        server_label = server.security_profile.clearance_label
        if server_label is None:
            return SecurityDecision(False, "model_server_clearance_missing")
        allowed = clearance.value(
            server_label,
            "ModelServerSecurityProfile.clearance_label",
        ) >= clearance.value(
            requirement.clearance_label,
            "ContextSecurityRequirement.clearance_label",
        )
        return SecurityDecision(
            allowed,
            "model_server_allowed" if allowed else "model_server_clearance_too_low",
        )
    labels_model = _required_model(model.classification_labels, "classification_labels")
    server_labels = set(
        labels_model.validate_labels(
            server.security_profile.classification_labels,
            "ModelServerSecurityProfile.classification_labels",
        )
    )
    required_labels = set(
        labels_model.validate_labels(
            requirement.classification_labels,
            "ContextSecurityRequirement.classification_labels",
        )
    )
    allowed = required_labels.issubset(server_labels)
    return SecurityDecision(
        allowed,
        "model_server_allowed" if allowed else "model_server_labels_missing",
    )


@dataclass(frozen=True)
class GraphNodeRef:
    source_id: str
    chunk_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _required(self.source_id, "GraphNodeRef.source_id"))
        object.__setattr__(self, "chunk_id", _required(self.chunk_id, "GraphNodeRef.chunk_id"))


@dataclass(frozen=True)
class GraphRelationship:
    from_node: GraphNodeRef
    to_node: GraphNodeRef
    relationship_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "relationship_type",
            _required(self.relationship_type, "GraphRelationship.relationship_type"),
        )
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    domain: str
    tenant_id: str
    source_id: str
    text: str
    source_uri: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    acl_labels: tuple[str, ...] = ()
    security: DocumentSecurity = field(default_factory=DocumentSecurity)

    def __post_init__(self) -> None:
        for field_name in ("chunk_id", "domain", "tenant_id", "source_id", "text"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"DocumentChunk.{field_name}"),
            )
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))
        object.__setattr__(
            self,
            "acl_labels",
            tuple(
                sorted(
                    {_required(label, "DocumentChunk.acl_labels[]") for label in self.acl_labels}
                )
            ),
        )


@dataclass(frozen=True)
class RetrievalProfile:
    mode: SearchMode
    top_k: int
    max_context_characters: int
    filters: Mapping[str, Any] = field(default_factory=dict)
    allowed_filter_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ValidationError("RetrievalProfile.top_k must be greater than zero")
        if self.max_context_characters < 1:
            raise ValidationError(
                "RetrievalProfile.max_context_characters must be greater than zero"
            )
        object.__setattr__(self, "filters", _immutable_mapping(self.filters))
        object.__setattr__(
            self,
            "allowed_filter_fields",
            _normalized_values(
                self.allowed_filter_fields,
                "RetrievalProfile.allowed_filter_fields",
            ),
        )


@dataclass(frozen=True)
class GraphTraversalProfile:
    enabled: bool
    max_depth: int
    max_nodes: int
    direction: GraphDirection
    relationship_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_depth < 1:
            raise ValidationError("GraphTraversalProfile.max_depth must be greater than zero")
        if self.max_nodes < 1:
            raise ValidationError("GraphTraversalProfile.max_nodes must be greater than zero")
        object.__setattr__(
            self,
            "relationship_types",
            tuple(
                sorted(
                    {
                        _required(
                            relationship_type,
                            "GraphTraversalProfile.relationship_types[]",
                        )
                        for relationship_type in self.relationship_types
                    }
                )
            ),
        )


@dataclass(frozen=True)
class DomainProfile:
    name: str
    description: str
    collection: str
    tenant_id: str
    prompt_name: str
    allow_external_model: bool
    retrieval: RetrievalProfile
    graph: GraphTraversalProfile
    pipeline_name: str = "default"
    allowed_pipeline_names: tuple[str, ...] = ()
    disclaimer: str = ""
    allowed_acl_labels: tuple[str, ...] = ()
    security_model: SecurityModel = field(default_factory=SecurityModel.none)
    require_travel_permission: bool = True

    def __post_init__(self) -> None:
        for field_name in ("name", "collection", "tenant_id", "prompt_name", "pipeline_name"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"DomainProfile.{field_name}"),
            )
        allowed_pipeline_names = tuple(
            sorted(
                {
                    _required(name, "DomainProfile.allowed_pipeline_names[]")
                    for name in self.allowed_pipeline_names
                }
                | {self.pipeline_name}
            )
        )
        object.__setattr__(self, "allowed_pipeline_names", allowed_pipeline_names)
        object.__setattr__(
            self,
            "allowed_acl_labels",
            tuple(
                sorted(
                    {
                        _required(label, "DomainProfile.allowed_acl_labels[]")
                        for label in self.allowed_acl_labels
                    }
                )
            ),
        )
        if not isinstance(self.security_model, SecurityModel):
            raise ValidationError("DomainProfile.security_model must be a SecurityModel")
        if not isinstance(self.require_travel_permission, bool):
            raise ValidationError("DomainProfile.require_travel_permission must be boolean")


@dataclass(frozen=True)
class SearchRequest:
    query: str
    domain: str
    tenant_id: str
    top_k: int
    mode: SearchMode
    filters: Mapping[str, Any]
    allowed_acl_labels: tuple[str, ...]
    security_model: SecurityModel
    subject_security: SubjectSecurity

    def __post_init__(self) -> None:
        for field_name in ("query", "domain", "tenant_id"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"SearchRequest.{field_name}"),
            )
        if self.top_k < 1:
            raise ValidationError("SearchRequest.top_k must be greater than zero")
        object.__setattr__(self, "filters", _immutable_mapping(self.filters))
        object.__setattr__(
            self,
            "allowed_acl_labels",
            _normalized_values(self.allowed_acl_labels, "SearchRequest.allowed_acl_labels"),
        )


@dataclass(frozen=True)
class SearchHit:
    chunk: DocumentChunk
    score: float
    score_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.score, (int, float)):
            raise ValidationError("SearchHit.score must be numeric")
        object.__setattr__(self, "score", float(self.score))
        object.__setattr__(
            self,
            "score_type",
            _required(self.score_type, "SearchHit.score_type"),
        )


@dataclass(frozen=True)
class GraphTraversalRequest:
    seeds: tuple[SearchHit, ...]
    domain: str
    tenant_id: str
    max_depth: int
    max_nodes: int
    direction: GraphDirection
    relationship_types: tuple[str, ...]
    allowed_acl_labels: tuple[str, ...]
    security_model: SecurityModel
    subject_security: SubjectSecurity

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _required(self.domain, "GraphTraversalRequest.domain"))
        object.__setattr__(
            self,
            "tenant_id",
            _required(self.tenant_id, "GraphTraversalRequest.tenant_id"),
        )
        if not self.seeds:
            raise ValidationError("GraphTraversalRequest.seeds cannot be empty")
        if self.max_depth < 1:
            raise ValidationError("GraphTraversalRequest.max_depth must be greater than zero")
        if self.max_nodes < 1:
            raise ValidationError("GraphTraversalRequest.max_nodes must be greater than zero")
        object.__setattr__(
            self,
            "relationship_types",
            tuple(
                sorted(
                    {
                        _required(
                            relationship_type,
                            "GraphTraversalRequest.relationship_types[]",
                        )
                        for relationship_type in self.relationship_types
                    }
                )
            ),
        )
        object.__setattr__(
            self,
            "allowed_acl_labels",
            tuple(
                sorted(
                    {
                        _required(label, "GraphTraversalRequest.allowed_acl_labels[]")
                        for label in self.allowed_acl_labels
                    }
                )
            ),
        )


@dataclass(frozen=True)
class Citation:
    source_id: str
    chunk_id: str
    source_uri: str | None
    score: float
    score_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _required(self.source_id, "Citation.source_id"))
        object.__setattr__(self, "chunk_id", _required(self.chunk_id, "Citation.chunk_id"))
        object.__setattr__(self, "score_type", _required(self.score_type, "Citation.score_type"))
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))


@dataclass(frozen=True)
class AuthorizationContext:
    subject: str
    tenant_id: str
    roles: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    acl_labels: tuple[str, ...] = ()
    security: SubjectSecurity = field(default_factory=SubjectSecurity)
    allow_external_model: bool = False
    diagnostic_access: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _required(self.subject, "AuthorizationContext.subject"))
        object.__setattr__(
            self,
            "tenant_id",
            _required(self.tenant_id, "AuthorizationContext.tenant_id"),
        )
        for field_name in ("roles", "groups", "acl_labels"):
            object.__setattr__(
                self,
                field_name,
                _normalized_values(
                    getattr(self, field_name),
                    f"AuthorizationContext.{field_name}",
                ),
            )


@dataclass(frozen=True)
class CapabilityDescriptor:
    capability_id: str
    display_name: str
    description: str
    domain: str
    pipeline_name: str
    diagnostics_available: bool
    external_model: bool
    policy_version: int

    def __post_init__(self) -> None:
        for field_name in (
            "capability_id",
            "display_name",
            "domain",
            "pipeline_name",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"CapabilityDescriptor.{field_name}"),
            )
        if self.policy_version < 1:
            raise ValidationError("CapabilityDescriptor.policy_version must be positive")


@dataclass(frozen=True)
class ClaimGroupMapping:
    claim_name: str
    claim_value: str
    group_id: str

    def __post_init__(self) -> None:
        if self.claim_name not in {"groups", "roles"}:
            raise ValidationError("ClaimGroupMapping.claim_name must be groups or roles")
        for field_name in ("claim_value", "group_id"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"ClaimGroupMapping.{field_name}"),
            )


@dataclass(frozen=True)
class GroupCapabilityGrant:
    group_id: str
    capability_id: str

    def __post_init__(self) -> None:
        for field_name in ("group_id", "capability_id"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"GroupCapabilityGrant.{field_name}"),
            )


@dataclass(frozen=True)
class AccessPolicyBundle:
    tenant_id: str
    version: int
    group_ids: tuple[str, ...]
    claim_mappings: tuple[ClaimGroupMapping, ...]
    capabilities: tuple[CapabilityDescriptor, ...]
    grants: tuple[GroupCapabilityGrant, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            _required(self.tenant_id, "AccessPolicyBundle.tenant_id"),
        )
        if self.version < 1:
            raise ValidationError("AccessPolicyBundle.version must be positive")
        normalized_groups = _normalized_values(
            self.group_ids,
            "AccessPolicyBundle.group_ids",
        )
        object.__setattr__(self, "group_ids", normalized_groups)
        group_ids = set(normalized_groups)
        capability_ids = [item.capability_id for item in self.capabilities]
        if len(capability_ids) != len(set(capability_ids)):
            raise ValidationError("AccessPolicyBundle capabilities must be unique")
        known_capabilities = set(capability_ids)
        for capability in self.capabilities:
            if capability.policy_version != self.version:
                raise ValidationError(
                    "AccessPolicyBundle capability policy_version must match bundle version"
                )
        for mapping in self.claim_mappings:
            if mapping.group_id not in group_ids:
                raise ValidationError("Claim mapping references an unknown group")
        for grant in self.grants:
            if grant.group_id not in group_ids:
                raise ValidationError("Capability grant references an unknown group")
            if grant.capability_id not in known_capabilities:
                raise ValidationError("Capability grant references an unknown capability")
        if len(self.claim_mappings) != len(set(self.claim_mappings)):
            raise ValidationError("AccessPolicyBundle claim mappings must be unique")
        if len(self.grants) != len(set(self.grants)):
            raise ValidationError("AccessPolicyBundle grants must be unique")


@dataclass(frozen=True)
class ResolvedAccessPolicy:
    subject: str
    tenant_id: str
    group_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    pipeline_names: tuple[str, ...]
    policy_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _required(self.subject, "ResolvedAccessPolicy.subject"))
        object.__setattr__(
            self,
            "tenant_id",
            _required(self.tenant_id, "ResolvedAccessPolicy.tenant_id"),
        )
        for field_name in ("group_ids", "capability_ids", "pipeline_names"):
            object.__setattr__(
                self,
                field_name,
                _normalized_values(
                    getattr(self, field_name),
                    f"ResolvedAccessPolicy.{field_name}",
                ),
            )
        if self.policy_version < 1:
            raise ValidationError("ResolvedAccessPolicy.policy_version must be positive")


@dataclass(frozen=True)
class PipelineAccessDecision:
    allowed: bool
    reason_code: str
    capability: CapabilityDescriptor | None
    policy: ResolvedAccessPolicy

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason_code",
            _required(self.reason_code, "PipelineAccessDecision.reason_code"),
        )
        if self.allowed and self.capability is None:
            raise ValidationError("Allowed PipelineAccessDecision requires capability")


@dataclass(frozen=True)
class RetrievalDiagnostic:
    chunk_id: str
    source_id: str
    score: float
    score_type: str
    rank: int
    origin: str
    graph_depth: int | None = None
    graph_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelTransmissionDiagnostic:
    checked: bool
    allowed: bool
    reason_code: str
    selected_model_server_id: str | None
    final_model_server_id: str | None
    rerouted: bool
    trust_boundary: TrustBoundary | None
    external_transmission: ExternalTransmissionPolicy | None
    context_security_requirement: ContextSecurityRequirement
    checked_chunk_ids: tuple[str, ...]
    blocked_chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class QueryDiagnostics:
    contract_version: str
    subject_hash: str
    tenant_id: str
    allowed_acl_labels: tuple[str, ...]
    security_model_kind: SecurityModelKind
    search_mode: SearchMode
    retrieval: tuple[RetrievalDiagnostic, ...]
    omitted_chunk_ids: tuple[str, ...]
    context_chunk_ids: tuple[str, ...]
    context_characters: int
    provider: str
    model: str
    prompt_key: str
    model_transmission: ModelTransmissionDiagnostic
    system_prompt_hash: str
    prompt_tokens: int
    completion_tokens: int
    model_duration_ms: int
    pipeline_trace: tuple[str, ...]


@dataclass(frozen=True)
class QueryCommand:
    request_id: str
    query: str
    domain: str
    session_id: str
    authorization: AuthorizationContext
    filters: Mapping[str, Any] = field(default_factory=dict)
    diagnostics_requested: bool = False

    def __post_init__(self) -> None:
        for field_name in ("request_id", "query", "domain", "session_id"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"QueryCommand.{field_name}"),
            )
        object.__setattr__(self, "filters", _immutable_mapping(self.filters))


@dataclass(frozen=True)
class QueryResult:
    request_id: str
    answer: str
    domain: str
    session_id: str
    citations: tuple[Citation, ...]
    pipeline_trace: tuple[str, ...]
    diagnostics: QueryDiagnostics | None = None


@dataclass(frozen=True)
class ModelGeneration:
    text: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _required(self.text, "ModelGeneration.text"))
        if self.prompt_tokens < 0:
            raise ValidationError("ModelGeneration.prompt_tokens cannot be negative")
        if self.completion_tokens < 0:
            raise ValidationError("ModelGeneration.completion_tokens cannot be negative")
        if self.estimated_cost < 0:
            raise ValidationError("ModelGeneration.estimated_cost cannot be negative")
        object.__setattr__(self, "estimated_cost", float(self.estimated_cost))

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class PipelineRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class PipelineStepDefinition:
    step_id: str
    action: str
    action_version: str
    next_step_id: str | None = None
    routes: Mapping[str, str] = field(default_factory=dict)
    terminal: bool = False
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "step_id",
            _required(self.step_id, "PipelineStepDefinition.step_id"),
        )
        object.__setattr__(self, "action", _required(self.action, "PipelineStepDefinition.action"))
        object.__setattr__(
            self,
            "action_version",
            _required(self.action_version, "PipelineStepDefinition.action_version"),
        )
        if self.next_step_id is not None:
            object.__setattr__(
                self,
                "next_step_id",
                _required(self.next_step_id, "PipelineStepDefinition.next_step_id"),
            )
        normalized_routes = {
            _required(key, "PipelineStepDefinition.routes key"): _required(
                value,
                "PipelineStepDefinition.routes value",
            )
            for key, value in self.routes.items()
        }
        object.__setattr__(self, "routes", MappingProxyType(normalized_routes))
        object.__setattr__(
            self,
            "config",
            _freeze_step_config(self.config),
        )
        if self.terminal and (self.next_step_id is not None or self.routes):
            raise ValidationError("A terminal pipeline step cannot define transitions")
        if not self.terminal and self.next_step_id is None and not self.routes:
            raise ValidationError("A non-terminal pipeline step must define a transition")


@dataclass(frozen=True)
class PipelineDefinition:
    name: str
    behavior_version: str
    entry_step_id: str
    max_steps: int
    steps: tuple[PipelineStepDefinition, ...]
    checksum: str

    def __post_init__(self) -> None:
        for field_name in ("name", "behavior_version", "entry_step_id", "checksum"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"PipelineDefinition.{field_name}"),
            )
        if self.max_steps < 1:
            raise ValidationError("PipelineDefinition.max_steps must be greater than zero")
        if not self.steps:
            raise ValidationError("PipelineDefinition.steps cannot be empty")

    def step(self, step_id: str) -> PipelineStepDefinition:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise ValidationError(f"Unknown pipeline step: {step_id}")


def _freeze_step_config(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("PipelineStepDefinition.config must be a mapping")
    return MappingProxyType(
        {
            _required(str(key), "PipelineStepDefinition.config key"): _freeze_config_value(
                item,
                f"PipelineStepDefinition.config[{key!r}]",
            )
            for key, item in value.items()
        }
    )


def _freeze_config_value(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                _required(str(key), f"{field_name} key"): _freeze_config_value(
                    item,
                    f"{field_name}.{key}",
                )
                for key, item in value.items()
            }
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_config_value(item, f"{field_name}[]") for item in value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise ValidationError(f"{field_name} must contain only JSON-compatible values")


@dataclass(frozen=True)
class PipelineRun:
    run_id: str
    request_id: str
    session_id: str
    domain: str
    tenant_id: str
    pipeline_name: str
    pipeline_version: str
    pipeline_checksum: str
    query: str

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "request_id",
            "session_id",
            "domain",
            "tenant_id",
            "pipeline_name",
            "pipeline_version",
            "pipeline_checksum",
            "query",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"PipelineRun.{field_name}"),
            )


@dataclass(frozen=True)
class PipelineStepAudit:
    run_id: str
    sequence_number: int
    step_id: str
    action: str
    action_version: str
    duration_ms: int
    next_step_id: str | None

    def __post_init__(self) -> None:
        for field_name in ("run_id", "step_id", "action", "action_version"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"PipelineStepAudit.{field_name}"),
            )
        if self.sequence_number < 1:
            raise ValidationError("PipelineStepAudit.sequence_number must be greater than zero")
        if self.duration_ms < 0:
            raise ValidationError("PipelineStepAudit.duration_ms cannot be negative")


class IngestionJobStatus(StrEnum):
    STAGED = "staged"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class DatasetImportStatus(StrEnum):
    STAGING = "staging"
    RELATING = "relating"
    DELETING = "deleting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class DatasetImportRequest:
    import_id: str
    domain: str
    tenant_id: str
    dataset_hash: str
    source_count: int
    chunk_count: int
    relationship_count: int
    deletion_count: int

    def __post_init__(self) -> None:
        for field_name in ("import_id", "domain", "tenant_id", "dataset_hash"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"DatasetImportRequest.{field_name}"),
            )
        for field_name in (
            "source_count",
            "chunk_count",
            "relationship_count",
            "deletion_count",
        ):
            if getattr(self, field_name) < 0:
                raise ValidationError(f"DatasetImportRequest.{field_name} cannot be negative")
        if self.source_count < 1 or self.chunk_count < 1:
            raise ValidationError("Dataset import must contain at least one source and chunk")


@dataclass(frozen=True)
class DatasetImportRun:
    import_id: str
    domain: str
    tenant_id: str
    dataset_hash: str
    status: DatasetImportStatus
    source_count: int
    chunk_count: int
    relationship_count: int
    deletion_count: int
    indexed_sources: int = 0
    published_relationships: int = 0
    deleted_sources: int = 0
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        DatasetImportRequest(
            import_id=self.import_id,
            domain=self.domain,
            tenant_id=self.tenant_id,
            dataset_hash=self.dataset_hash,
            source_count=self.source_count,
            chunk_count=self.chunk_count,
            relationship_count=self.relationship_count,
            deletion_count=self.deletion_count,
        )
        for field_name in (
            "indexed_sources",
            "published_relationships",
            "deleted_sources",
        ):
            if getattr(self, field_name) < 0:
                raise ValidationError(f"DatasetImportRun.{field_name} cannot be negative")


@dataclass(frozen=True)
class DatasetConsistencyReport:
    domain: str
    tenant_id: str
    active_sources: int
    active_chunks: int
    indexed_chunks: int
    active_relationships: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "domain",
            _required(self.domain, "DatasetConsistencyReport.domain"),
        )
        object.__setattr__(
            self,
            "tenant_id",
            _required(self.tenant_id, "DatasetConsistencyReport.tenant_id"),
        )
        for field_name in (
            "active_sources",
            "active_chunks",
            "indexed_chunks",
            "active_relationships",
        ):
            if getattr(self, field_name) < 0:
                raise ValidationError(f"DatasetConsistencyReport.{field_name} cannot be negative")

    @property
    def consistent(self) -> bool:
        return self.active_chunks == self.indexed_chunks


@dataclass(frozen=True)
class IngestionCommand:
    idempotency_key: str
    domain: str
    tenant_id: str
    source_id: str
    source_version: str
    chunks: tuple[DocumentChunk, ...]
    relationships: tuple[GraphRelationship, ...] = ()
    source_uri: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "idempotency_key",
            "domain",
            "tenant_id",
            "source_id",
            "source_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"IngestionCommand.{field_name}"),
            )
        if not self.chunks:
            raise ValidationError("IngestionCommand.chunks cannot be empty")
        chunk_ids: set[str] = set()
        for chunk in self.chunks:
            if (
                chunk.domain != self.domain
                or chunk.tenant_id != self.tenant_id
                or chunk.source_id != self.source_id
            ):
                raise ValidationError(
                    "Every ingestion chunk must match command domain, tenant and source"
                )
            if chunk.chunk_id in chunk_ids:
                raise ValidationError(f"Duplicate ingestion chunk id: {chunk.chunk_id}")
            chunk_ids.add(chunk.chunk_id)
        relationship_keys: set[tuple[str, str, str, str, str]] = set()
        for relationship in self.relationships:
            if relationship.from_node.source_id != self.source_id:
                raise ValidationError(
                    "Every ingestion relationship must originate from the command source"
                )
            if relationship.from_node.chunk_id not in chunk_ids:
                raise ValidationError(
                    "Every ingestion relationship must originate from an ingested chunk"
                )
            if (
                relationship.to_node.source_id == self.source_id
                and relationship.to_node.chunk_id not in chunk_ids
            ):
                raise ValidationError("An internal relationship target must be an ingested chunk")
            key = (
                relationship.from_node.source_id,
                relationship.from_node.chunk_id,
                relationship.to_node.source_id,
                relationship.to_node.chunk_id,
                relationship.relationship_type,
            )
            if key in relationship_keys:
                raise ValidationError("Duplicate ingestion relationship")
            relationship_keys.add(key)
        object.__setattr__(self, "metadata", _immutable_mapping(self.metadata))


@dataclass(frozen=True)
class IngestionJob:
    job_id: str
    payload_hash: str
    status: IngestionJobStatus
    command: IngestionCommand
    attempts: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _required(self.job_id, "IngestionJob.job_id"))
        object.__setattr__(
            self,
            "payload_hash",
            _required(self.payload_hash, "IngestionJob.payload_hash"),
        )
        if self.attempts < 0:
            raise ValidationError("IngestionJob.attempts cannot be negative")


@dataclass(frozen=True)
class IngestionResult:
    job_id: str
    domain: str
    tenant_id: str
    source_id: str
    source_version: str
    status: IngestionJobStatus
    chunk_count: int

    def __post_init__(self) -> None:
        for field_name in ("job_id", "domain", "tenant_id", "source_id", "source_version"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), f"IngestionResult.{field_name}"),
            )
        if self.chunk_count < 1:
            raise ValidationError("IngestionResult.chunk_count must be greater than zero")
