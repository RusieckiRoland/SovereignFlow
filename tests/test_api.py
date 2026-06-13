from pathlib import Path

from sovereignflow.api import create_app
from sovereignflow.domain import DomainProfile
from sovereignflow.models import DocumentChunk
from sovereignflow.providers.in_memory import InMemoryDocumentStore
from sovereignflow.rag import RAGService


class FakeModel:
    def generate(self, **kwargs) -> str:
        return "Mobile-ready response."


def test_query_endpoint_exposes_generic_rag_contract() -> None:
    pipeline_path = Path(__file__).parents[1] / "pipelines" / "default.yaml"
    domain = DomainProfile.from_mapping(
        {
            "name": "general",
            "collection": "General",
            "pipeline": str(pipeline_path),
            "system_prompt": "Use evidence.",
        }
    )
    store = InMemoryDocumentStore(
        [
            DocumentChunk(
                chunk_id="chunk-1",
                domain="general",
                source_id="source-1",
                text="SovereignFlow can serve mobile applications through an HTTP API.",
            )
        ]
    )
    app = create_app({"general": RAGService(domain, store, FakeModel())})

    response = app.test_client().post(
        "/v1/query",
        json={
            "query": "mobile HTTP API",
            "domain": "general",
            "session_id": "mobile-session",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["answer"] == "Mobile-ready response."
    assert payload["citations"][0]["source_id"] == "source-1"
