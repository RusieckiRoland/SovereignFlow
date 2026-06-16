from __future__ import annotations

from pathlib import Path

import pytest

from sovereignflow.application.ingestion import _validate_document_security
from sovereignflow.bootstrap.config import _security_model
from sovereignflow.domain import (
    ClassificationLabelsModel,
    ClearanceLevelModel,
    ContextSecurityRequirement,
    DocumentChunk,
    DocumentSecurity,
    DomainProfile,
    GraphDirection,
    GraphTraversalProfile,
    ModelServerDefinition,
    ModelServerSecurityProfile,
    ModelServerSelection,
    PolicyViolationError,
    RetrievalProfile,
    SearchHit,
    SearchMode,
    SecurityModel,
    SecurityModelKind,
    SubjectSecurity,
    TrustBoundary,
    ValidationError,
    acl_visible_to_subject,
    context_security_requirement,
    document_visible_to_subject,
    model_server_satisfies_requirement,
)
from sovereignflow.domain.models import _required_model
from sovereignflow.infrastructure.dataset_reader import _security_from_record
from sovereignflow.infrastructure.graph import _security_filter as graph_security_filter
from sovereignflow.infrastructure.weaviate import _security_filter as weaviate_security_filter


def clearance_model() -> SecurityModel:
    return SecurityModel(
        SecurityModelKind.CLEARANCE_LEVEL,
        clearance_level=ClearanceLevelModel({"PUBLIC": 0, "INTERNAL": 10}),
    )


def labels_model() -> SecurityModel:
    return SecurityModel(
        SecurityModelKind.CLASSIFICATION_LABELS,
        classification_labels=ClassificationLabelsModel(("US_NOFORN", "US_ORCON")),
    )


def test_model_server_security_profiles_validate_shapes() -> None:
    with pytest.raises(ValidationError, match="none"):
        ModelServerSecurityProfile(SecurityModelKind.NONE, clearance_label="PUBLIC")
    with pytest.raises(ValidationError, match="clearance_level"):
        ModelServerSecurityProfile(SecurityModelKind.CLEARANCE_LEVEL)
    with pytest.raises(ValidationError, match="clearance_level"):
        ModelServerSecurityProfile(
            SecurityModelKind.CLEARANCE_LEVEL,
            clearance_label="PUBLIC",
            classification_labels=("US_NOFORN",),
        )
    with pytest.raises(ValidationError, match="classification_labels"):
        ModelServerSecurityProfile(
            SecurityModelKind.CLASSIFICATION_LABELS,
            clearance_label="PUBLIC",
        )
    profile = ModelServerSecurityProfile(
        SecurityModelKind.CLASSIFICATION_LABELS,
        classification_labels=("US_ORCON", "US_NOFORN", "US_NOFORN"),
    )
    assert profile.classification_labels == ("US_NOFORN", "US_ORCON")
    with pytest.raises(ValidationError, match="server_id"):
        ModelServerDefinition(
            "",
            TrustBoundary.INTERNAL,
            ModelServerSecurityProfile(SecurityModelKind.NONE),
        )
    definition = ModelServerDefinition(
        "external",
        TrustBoundary.EXTERNAL,
        ModelServerSecurityProfile(SecurityModelKind.NONE),
        security_reroute_server_id=" internal ",
    )
    assert definition.security_reroute_server_id == "internal"
    with pytest.raises(ValidationError, match="invalid"):
        ModelServerSecurityProfile("bad")  # type: ignore[arg-type]


def test_context_requirement_and_selection_validation() -> None:
    requirement = context_security_requirement(model=SecurityModel.none(), hits=())
    assert requirement.security_model_kind == SecurityModelKind.NONE
    with pytest.raises(ValidationError, match="none"):
        ContextSecurityRequirement(SecurityModelKind.NONE, clearance_label="PUBLIC")
    with pytest.raises(ValidationError, match="clearance_level"):
        ContextSecurityRequirement(
            SecurityModelKind.CLEARANCE_LEVEL,
            classification_labels=("US_NOFORN",),
        )
    with pytest.raises(ValidationError, match="classification_labels"):
        ContextSecurityRequirement(
            SecurityModelKind.CLASSIFICATION_LABELS,
            clearance_label="PUBLIC",
        )
    selection = ModelServerSelection(
        True,
        " allowed ",
        " selected ",
        " final ",
        True,
        TrustBoundary.INTERNAL,
        requirement,
    )
    assert selection.reason_code == "allowed"
    assert selection.selected_server_id == "selected"
    assert selection.final_server_id == "final"
    without_final = ModelServerSelection(
        False,
        "blocked",
        "selected",
        None,
        False,
        None,
        requirement,
    )
    assert without_final.final_server_id is None
    with pytest.raises(ValidationError, match="invalid"):
        ContextSecurityRequirement("bad")  # type: ignore[arg-type]


def test_context_security_requirement_uses_highest_clearance_and_label_union() -> None:
    public = SearchHit(
        DocumentChunk(
            "public",
            "general",
            "tenant",
            "source",
            "text",
            security=DocumentSecurity(clearance_label="PUBLIC"),
        ),
        0.1,
        "hybrid",
    )
    internal = SearchHit(
        DocumentChunk(
            "internal",
            "general",
            "tenant",
            "source",
            "text",
            security=DocumentSecurity(clearance_label="INTERNAL"),
        ),
        0.2,
        "hybrid",
    )
    requirement = context_security_requirement(model=clearance_model(), hits=(public, internal))
    assert requirement.clearance_label == "INTERNAL"
    with pytest.raises(ValidationError, match="missing clearance"):
        context_security_requirement(
            model=clearance_model(),
            hits=(
                SearchHit(
                    DocumentChunk("missing", "general", "tenant", "source", "text"),
                    0.1,
                    "hybrid",
                ),
            ),
        )
    noforn = SearchHit(
        DocumentChunk(
            "noforn",
            "general",
            "tenant",
            "source",
            "text",
            security=DocumentSecurity(classification_labels=("US_NOFORN",)),
        ),
        0.1,
        "hybrid",
    )
    orcon = SearchHit(
        DocumentChunk(
            "orcon",
            "general",
            "tenant",
            "source",
            "text",
            security=DocumentSecurity(classification_labels=("US_ORCON",)),
        ),
        0.2,
        "hybrid",
    )
    requirement = context_security_requirement(model=labels_model(), hits=(noforn, orcon))
    assert requirement.classification_labels == ("US_NOFORN", "US_ORCON")


def test_model_server_requirement_decisions_cover_security_models() -> None:
    none_server = ModelServerDefinition(
        "none",
        TrustBoundary.INTERNAL,
        ModelServerSecurityProfile(SecurityModelKind.NONE),
    )
    assert model_server_satisfies_requirement(
        model=SecurityModel.none(),
        server=none_server,
        requirement=ContextSecurityRequirement(SecurityModelKind.NONE),
    ).allowed
    assert (
        model_server_satisfies_requirement(
            model=clearance_model(),
            server=none_server,
            requirement=ContextSecurityRequirement(
                SecurityModelKind.CLEARANCE_LEVEL,
                clearance_label="PUBLIC",
            ),
        ).reason_code
        == "model_server_security_model_mismatch"
    )
    clearance_server = ModelServerDefinition(
        "clearance",
        TrustBoundary.INTERNAL,
        ModelServerSecurityProfile(SecurityModelKind.CLEARANCE_LEVEL, clearance_label="INTERNAL"),
    )
    assert (
        model_server_satisfies_requirement(
            model=clearance_model(),
            server=clearance_server,
            requirement=ContextSecurityRequirement(SecurityModelKind.CLEARANCE_LEVEL),
        ).reason_code
        == "model_server_allowed_empty_context"
    )
    assert model_server_satisfies_requirement(
        model=clearance_model(),
        server=clearance_server,
        requirement=ContextSecurityRequirement(
            SecurityModelKind.CLEARANCE_LEVEL,
            clearance_label="INTERNAL",
        ),
    ).allowed
    broken_profile = object.__new__(ModelServerSecurityProfile)
    object.__setattr__(broken_profile, "security_model_kind", SecurityModelKind.CLEARANCE_LEVEL)
    object.__setattr__(broken_profile, "clearance_label", None)
    object.__setattr__(broken_profile, "classification_labels", ())
    broken_server = ModelServerDefinition(
        "broken",
        TrustBoundary.INTERNAL,
        broken_profile,
    )
    assert (
        model_server_satisfies_requirement(
            model=clearance_model(),
            server=broken_server,
            requirement=ContextSecurityRequirement(
                SecurityModelKind.CLEARANCE_LEVEL,
                clearance_label="PUBLIC",
            ),
        ).reason_code
        == "model_server_clearance_missing"
    )
    label_server = ModelServerDefinition(
        "labels",
        TrustBoundary.INTERNAL,
        ModelServerSecurityProfile(
            SecurityModelKind.CLASSIFICATION_LABELS,
            classification_labels=("US_NOFORN",),
        ),
    )
    assert model_server_satisfies_requirement(
        model=labels_model(),
        server=label_server,
        requirement=ContextSecurityRequirement(
            SecurityModelKind.CLASSIFICATION_LABELS,
            classification_labels=("US_NOFORN",),
        ),
    ).allowed
    assert (
        model_server_satisfies_requirement(
            model=labels_model(),
            server=label_server,
            requirement=ContextSecurityRequirement(
                SecurityModelKind.CLASSIFICATION_LABELS,
                classification_labels=("US_NOFORN", "US_ORCON"),
            ),
        ).reason_code
        == "model_server_labels_missing"
    )


def test_security_model_validates_clearance_and_label_configuration() -> None:
    assert ClearanceLevelModel({"PUBLIC": 0}).allowed_document_labels("PUBLIC") == ("PUBLIC",)
    with pytest.raises(ValidationError, match="empty"):
        ClearanceLevelModel({})
    with pytest.raises(ValidationError, match="non-negative"):
        ClearanceLevelModel({"PUBLIC": -1})
    with pytest.raises(ValidationError, match="unique"):
        ClearanceLevelModel({" PUBLIC ": 0, "PUBLIC": 1})
    with pytest.raises(ValidationError, match="not allowed"):
        ClearanceLevelModel({"PUBLIC": 0}).value("SECRET", "subject")
    with pytest.raises(ValidationError, match="empty"):
        ClassificationLabelsModel(())
    with pytest.raises(ValidationError, match="outside"):
        ClassificationLabelsModel(("US_NOFORN",)).validate_labels(("US_ORCON",), "labels")
    with pytest.raises(ValidationError, match="none"):
        SecurityModel(SecurityModelKind.NONE, clearance_level=ClearanceLevelModel({"PUBLIC": 0}))
    with pytest.raises(ValidationError, match="clearance_level"):
        SecurityModel(SecurityModelKind.CLEARANCE_LEVEL)
    with pytest.raises(ValidationError, match="classification_labels"):
        SecurityModel(SecurityModelKind.CLASSIFICATION_LABELS)


def test_document_and_acl_decisions_cover_all_policy_outcomes() -> None:
    assert (
        document_visible_to_subject(
            model=SecurityModel.none(),
            document=DocumentSecurity(clearance_label="SECRET"),
            subject=SubjectSecurity(),
        ).reason_code
        == "security_model_none"
    )
    assert (
        document_visible_to_subject(
            model=clearance_model(),
            document=DocumentSecurity(),
            subject=SubjectSecurity(clearance_label="PUBLIC"),
        ).reason_code
        == "document_clearance_missing"
    )
    assert (
        document_visible_to_subject(
            model=clearance_model(),
            document=DocumentSecurity(clearance_label="PUBLIC"),
            subject=SubjectSecurity(),
        ).reason_code
        == "subject_clearance_missing"
    )
    assert (
        document_visible_to_subject(
            model=clearance_model(),
            document=DocumentSecurity(clearance_label="INTERNAL"),
            subject=SubjectSecurity(clearance_label="PUBLIC"),
        ).reason_code
        == "clearance_denied"
    )
    assert (
        document_visible_to_subject(
            model=labels_model(),
            document=DocumentSecurity(classification_labels=("US_NOFORN", "US_ORCON")),
            subject=SubjectSecurity(classification_labels=("US_NOFORN",)),
        ).reason_code
        == "classification_labels_denied"
    )
    assert document_visible_to_subject(
        model=labels_model(),
        document=DocumentSecurity(classification_labels=("US_NOFORN",)),
        subject=SubjectSecurity(classification_labels=("US_NOFORN", "US_ORCON")),
    ).allowed
    assert (
        acl_visible_to_subject(document_acl_labels=(), subject_acl_labels=()).reason_code
        == "acl_public"
    )
    assert (
        acl_visible_to_subject(
            document_acl_labels=("finance",),
            subject_acl_labels=("finance", "analyst"),
        ).reason_code
        == "acl_allowed"
    )
    assert not acl_visible_to_subject(
        document_acl_labels=("finance",),
        subject_acl_labels=("developer",),
    ).allowed


def test_ingestion_security_validation_rejects_mixed_or_invalid_metadata() -> None:
    _validate_document_security(model=SecurityModel.none(), security=DocumentSecurity())
    with pytest.raises(PolicyViolationError, match="disabled"):
        _validate_document_security(
            model=SecurityModel.none(),
            security=DocumentSecurity(clearance_label="PUBLIC"),
        )
    with pytest.raises(PolicyViolationError, match="incomplete clearance"):
        _validate_document_security(model=clearance_model(), security=DocumentSecurity())
    with pytest.raises(PolicyViolationError, match="forbidden clearance"):
        _validate_document_security(
            model=clearance_model(),
            security=DocumentSecurity(clearance_label="SECRET"),
        )
    with pytest.raises(PolicyViolationError, match="mixed"):
        _validate_document_security(
            model=clearance_model(),
            security=DocumentSecurity(
                clearance_label="PUBLIC",
                classification_labels=("US_NOFORN",),
            ),
        )
    with pytest.raises(PolicyViolationError, match="forbidden classification"):
        _validate_document_security(
            model=labels_model(),
            security=DocumentSecurity(classification_labels=("UNKNOWN",)),
        )
    with pytest.raises(PolicyViolationError, match="mixed"):
        _validate_document_security(
            model=labels_model(),
            security=DocumentSecurity(clearance_label="PUBLIC"),
        )
    _validate_document_security(
        model=labels_model(),
        security=DocumentSecurity(classification_labels=("US_NOFORN",)),
    )
    with pytest.raises(PolicyViolationError, match="incomplete classification"):
        broken = object.__new__(SecurityModel)
        object.__setattr__(broken, "kind", SecurityModelKind.CLASSIFICATION_LABELS)
        object.__setattr__(broken, "clearance_level", None)
        object.__setattr__(broken, "classification_labels", None)
        _validate_document_security(model=broken, security=DocumentSecurity())
    with pytest.raises(ValidationError, match="Unsupported"):
        broken = object.__new__(SecurityModel)
        object.__setattr__(broken, "kind", "invalid")
        object.__setattr__(broken, "clearance_level", None)
        object.__setattr__(broken, "classification_labels", None)
        _validate_document_security(model=broken, security=DocumentSecurity())


def test_config_loader_security_model_variants_and_validation() -> None:
    assert _security_model({"kind": "none"}).kind == SecurityModelKind.NONE
    assert (
        _security_model(
            {
                "kind": "clearance_level",
                "clearance_level": {"levels": {"PUBLIC": 0}},
            }
        ).kind
        == SecurityModelKind.CLEARANCE_LEVEL
    )
    assert (
        _security_model(
            {
                "kind": "clearance_level",
                "levels": {"PUBLIC": 0},
            }
        ).kind
        == SecurityModelKind.CLEARANCE_LEVEL
    )
    assert (
        _security_model(
            {
                "kind": "classification_labels",
                "labels_universe_subset": ["US_NOFORN"],
            }
        ).kind
        == SecurityModelKind.CLASSIFICATION_LABELS
    )
    with pytest.raises(Exception, match="kind"):
        _security_model({"kind": "bad"})
    with pytest.raises(Exception, match="levels"):
        _security_model({"kind": "clearance_level"})
    with pytest.raises(Exception, match="labels_universe_subset"):
        _security_model({"kind": "classification_labels"})
    with pytest.raises(Exception, match="integer"):
        _security_model({"kind": "clearance_level", "levels": {"PUBLIC": "bad"}})
    with pytest.raises(Exception, match="non-negative"):
        _security_model({"kind": "clearance_level", "levels": {"PUBLIC": -1}})


def test_domain_and_private_security_guards_cover_defensive_branches() -> None:
    with pytest.raises(ValidationError, match="invalid"):
        SecurityModel("bad")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="required"):
        _required_model(None, "test")
    with pytest.raises(ValidationError, match="require_travel_permission"):
        DomainProfile(
            "general",
            "",
            "General",
            "tenant",
            "answer",
            False,
            RetrievalProfile(SearchMode.HYBRID, 1, 100),
            GraphTraversalProfile(False, 1, 1, GraphDirection.BOTH),
            require_travel_permission="yes",  # type: ignore[arg-type]
        )


class FilterExpression:
    def __init__(self, value: str) -> None:
        self.value = value

    def __and__(self, other: FilterExpression) -> FilterExpression:
        return FilterExpression(f"({self.value}&{other.value})")

    def __or__(self, other: FilterExpression) -> FilterExpression:
        return FilterExpression(f"({self.value}|{other.value})")

    def __invert__(self) -> FilterExpression:
        return FilterExpression(f"~{self.value}")


class FilterProperty:
    def __init__(self, name: str) -> None:
        self.name = name

    def equal(self, value: object) -> FilterExpression:
        return FilterExpression(f"{self.name}= {value}")

    def contains_any(self, values: list[str]) -> FilterExpression:
        return FilterExpression(f"{self.name} any {','.join(values)}")


class FilterFactory:
    @staticmethod
    def by_property(name: str) -> FilterProperty:
        return FilterProperty(name)


class Request:
    def __init__(
        self,
        *,
        model: SecurityModel,
        subject: SubjectSecurity,
        domain: str = "general",
    ) -> None:
        self.security_model = model
        self.subject_security = subject
        self.domain = domain


def test_infrastructure_security_filters_cover_all_models() -> None:
    assert graph_security_filter(
        Request(model=SecurityModel.none(), subject=SubjectSecurity())
    ) == ("TRUE", ())
    assert graph_security_filter(Request(model=clearance_model(), subject=SubjectSecurity())) == (
        "FALSE",
        (),
    )
    assert graph_security_filter(
        Request(model=clearance_model(), subject=SubjectSecurity(clearance_label="INTERNAL"))
    ) == ("chunk.clearance_label = ANY(%s::text[])", (["INTERNAL", "PUBLIC"],))
    assert graph_security_filter(
        Request(
            model=labels_model(),
            subject=SubjectSecurity(classification_labels=("US_NOFORN",)),
        )
    ) == ("chunk.classification_labels <@ %s::text[]", (["US_NOFORN"],))
    broken_labels = object.__new__(SecurityModel)
    object.__setattr__(broken_labels, "kind", SecurityModelKind.CLASSIFICATION_LABELS)
    object.__setattr__(broken_labels, "clearance_level", None)
    object.__setattr__(broken_labels, "classification_labels", None)
    assert graph_security_filter(Request(model=broken_labels, subject=SubjectSecurity())) == (
        "FALSE",
        (),
    )
    broken_kind = object.__new__(SecurityModel)
    object.__setattr__(broken_kind, "kind", "invalid")
    object.__setattr__(broken_kind, "clearance_level", None)
    object.__setattr__(broken_kind, "classification_labels", None)
    assert graph_security_filter(Request(model=broken_kind, subject=SubjectSecurity())) == (
        "FALSE",
        (),
    )

    assert (
        weaviate_security_filter(
            FilterFactory,
            Request(model=SecurityModel.none(), subject=SubjectSecurity()),
        ).value
        == "domain= general"
    )
    assert (
        weaviate_security_filter(
            FilterFactory,
            Request(model=clearance_model(), subject=SubjectSecurity()),
        ).value
        == "domain= __never__"
    )
    assert (
        weaviate_security_filter(
            FilterFactory,
            Request(model=clearance_model(), subject=SubjectSecurity(clearance_label="INTERNAL")),
        ).value
        == "clearance_label any INTERNAL,PUBLIC"
    )
    assert (
        weaviate_security_filter(
            FilterFactory,
            Request(
                model=labels_model(),
                subject=SubjectSecurity(classification_labels=("US_NOFORN",)),
            ),
        ).value
        == "~classification_labels any US_ORCON"
    )
    assert (
        weaviate_security_filter(
            FilterFactory,
            Request(model=broken_labels, subject=SubjectSecurity()),
        ).value
        == "domain= __never__"
    )
    assert (
        weaviate_security_filter(
            FilterFactory,
            Request(
                model=labels_model(),
                subject=SubjectSecurity(classification_labels=("US_NOFORN", "US_ORCON")),
            ),
        ).value
        == "domain= general"
    )
    assert (
        weaviate_security_filter(
            FilterFactory,
            Request(model=broken_kind, subject=SubjectSecurity()),
        ).value
        == "domain= __never__"
    )


def test_dataset_reader_security_validation() -> None:
    assert _security_from_record(
        {"security": {"clearance_label": "PUBLIC", "classification_labels": []}},
        Path("nodes.jsonl"),
    ) == DocumentSecurity(clearance_label="PUBLIC")
    with pytest.raises(ValidationError, match="security"):
        _security_from_record({"security": []}, Path("nodes.jsonl"))
    with pytest.raises(ValidationError, match="clearance_label"):
        _security_from_record(
            {"security": {"clearance_label": 1}},
            Path("nodes.jsonl"),
        )
    with pytest.raises(ValidationError, match="classification_labels"):
        _security_from_record(
            {"security": {"classification_labels": [1]}},
            Path("nodes.jsonl"),
        )
