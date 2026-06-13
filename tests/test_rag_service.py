from pathlib import Path

from sovereignflow.domain import DomainProfile
from sovereignflow.models import DocumentChunk, QueryRequest
from sovereignflow.providers.in_memory import InMemoryDocumentStore
from sovereignflow.rag import RAGService


class CapturingModel:
    def __init__(self) -> None:
        self.user_prompt = ""

    def generate(self, *, system_prompt: str, user_prompt: str, security_context=None) -> str:
        self.user_prompt = user_prompt
        return "Evidence-based answer."


def profile() -> DomainProfile:
    pipeline_path = Path(__file__).parents[1] / "pipelines" / "default.yaml"
    return DomainProfile.from_mapping(
        {
            "name": "taric",
            "collection": "BtiDecisions",
            "pipeline": str(pipeline_path),
            "system_prompt": "Use only customs evidence.",
            "disclaimer": "A professional makes the final decision.",
            "retrieval": {"mode": "hybrid", "top_k": 3},
        }
    )


def test_rag_service_returns_sources_and_disclaimer() -> None:
    store = InMemoryDocumentStore(
        [
            DocumentChunk(
                chunk_id="PL-1:description",
                domain="taric",
                source_id="PL-1",
                source_uri="https://example.test/PL-1",
                text="A steel bottle was classified under commodity code 1234.",
                metadata={"commodity_code": "1234"},
            )
        ]
    )
    model = CapturingModel()
    service = RAGService(profile(), store, model)

    response = service.query(
        QueryRequest(
            query="steel bottle commodity code",
            domain="taric",
            session_id="session-1",
        )
    )

    assert response.answer.endswith("A professional makes the final decision.")
    assert response.citations[0].source_id == "PL-1"
    assert response.pipeline_trace == (
        "normalize_query",
        "retrieve",
        "build_context",
        "call_model",
        "finalize",
    )
    assert "commodity code 1234" in model.user_prompt


def test_acl_and_classification_are_enforced_before_generation() -> None:
    store = InMemoryDocumentStore(
        [
            DocumentChunk(
                chunk_id="public",
                domain="taric",
                source_id="public",
                text="public tariff evidence",
            ),
            DocumentChunk(
                chunk_id="restricted",
                domain="taric",
                source_id="restricted",
                text="restricted tariff evidence",
                acl_labels=("customs-office",),
                classification_level=2,
            ),
        ]
    )
    model = CapturingModel()
    service = RAGService(profile(), store, model)

    response = service.query(
        QueryRequest(
            query="tariff evidence",
            domain="taric",
            session_id="session-2",
            max_classification_level=0,
        )
    )

    assert [citation.source_id for citation in response.citations] == ["public"]
    assert "restricted tariff evidence" not in model.user_prompt

