from sovereignflow.api import create_app
from sovereignflow.domain import load_domain_profile
from sovereignflow.models import DocumentChunk
from sovereignflow.providers.in_memory import InMemoryDocumentStore
from sovereignflow.rag import RAGService


class DemoModel:
    def generate(self, *, system_prompt: str, user_prompt: str, security_context=None) -> str:
        return "Demo response. Configure a local OpenAI-compatible model for real generation."


domain = load_domain_profile("config/domains/general.yaml")
store = InMemoryDocumentStore(
    [
        DocumentChunk(
            chunk_id="demo-1",
            domain="general",
            source_id="demo",
            text="SovereignFlow is a domain-neutral, local-first RAG foundation.",
        )
    ]
)
service = RAGService(domain=domain, retrieval=store, model=DemoModel())
app = create_app({domain.name: service})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)

