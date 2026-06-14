from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from sovereignflow.application import PipelineEngine, RagQueryService, default_action_registry
from sovereignflow.domain import (
    DocumentChunk,
    DomainProfile,
    GraphDirection,
    GraphTraversalProfile,
    ModelGeneration,
    PipelineDefinition,
    PipelineStepDefinition,
    RetrievalProfile,
    SearchHit,
    SearchMode,
)


class StubRetrieval:
    def __init__(self, hits: tuple[SearchHit, ...] = ()) -> None:
        self.hits = hits
        self.requests = []
        self.healthy = True

    def search(self, request):
        self.requests.append(request)
        return self.hits

    def healthcheck(self) -> None:
        if not self.healthy:
            raise RuntimeError("unhealthy")


class StubGraph:
    name = "graph_traversal"

    def __init__(self, hits: tuple[SearchHit, ...] = ()) -> None:
        self.hits = hits
        self.requests = []
        self.checked = 0

    def expand(self, request):
        self.requests.append(request)
        return self.hits

    def check(self) -> None:
        self.checked += 1


class StubModel:
    def __init__(self, *, scope: str = "local", answer: str = "answer") -> None:
        self._scope = scope
        self.answer = answer
        self.calls = []
        self.healthy = True

    @property
    def scope(self) -> str:
        return self._scope

    def generate(self, **kwargs) -> ModelGeneration:
        self.calls.append(kwargs)
        return ModelGeneration(
            text=self.answer,
            prompt_tokens=10,
            completion_tokens=5,
            estimated_cost=0.001,
        )

    def healthcheck(self) -> None:
        if not self.healthy:
            raise RuntimeError("unhealthy")


class StubPrompts:
    def __init__(self, value: str = "system") -> None:
        self.value = value
        self.names = []

    def load(self, name: str) -> str:
        self.names.append(name)
        return self.value


class StubAudit:
    def __init__(self) -> None:
        self.started = []
        self.steps = []
        self.succeeded = []
        self.failed = []

    def start(self, run) -> None:
        self.started.append(run)

    def record_step(self, step) -> None:
        self.steps.append(step)

    def succeed(self, run_id: str, **kwargs) -> None:
        self.succeeded.append((run_id, kwargs))

    def fail(self, run_id: str, **kwargs) -> None:
        self.failed.append((run_id, kwargs))

    def fetch(self, request_id: str, *, tenant_id: str):
        return None

    def metrics(self, *, tenant_id: str, hours: int):
        return {"tenant_id": tenant_id, "window_hours": hours}


class StubOperations:
    def execution(self, request_id: str, *, tenant_id: str):
        return None

    def metrics(self, *, tenant_id: str, hours: int):
        return {"tenant_id": tenant_id, "window_hours": hours}

    def ingestion_job(self, job_id: str, *, tenant_id: str):
        return {"job_id": job_id, "tenant_id": tenant_id}

    def retry_ingestion(self, job_id: str, *, tenant_id: str):
        return {"job_id": job_id, "tenant_id": tenant_id, "status": "completed"}


def default_pipeline() -> PipelineDefinition:
    action_ids = (
        "normalize_query",
        "retrieve",
        "expand_graph",
        "build_context",
        "call_model",
        "finalize",
    )
    return PipelineDefinition(
        name="default-rag",
        behavior_version="1.0",
        entry_step_id=action_ids[0],
        max_steps=len(action_ids),
        steps=tuple(
            PipelineStepDefinition(
                step_id=action_id,
                action=action_id,
                action_version="1.0",
                next_step_id=action_ids[index + 1] if index + 1 < len(action_ids) else None,
                terminal=index + 1 == len(action_ids),
            )
            for index, action_id in enumerate(action_ids)
        ),
        checksum="a" * 64,
    )


def build_query_service(
    *,
    domain: DomainProfile,
    retrieval,
    model,
    prompts,
    graph: StubGraph | None = None,
    audit: StubAudit | None = None,
) -> RagQueryService:
    selected_audit = audit or StubAudit()
    return RagQueryService(
        domain=domain,
        retrieval=retrieval,
        graph=graph or StubGraph(),
        model=model,
        prompts=prompts,
        pipeline=default_pipeline(),
        engine=PipelineEngine(
            registry=default_action_registry(),
            audit=selected_audit,
            monotonic=lambda: 1.0,
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000001",
        ),
    )


@pytest.fixture
def domain_profile() -> DomainProfile:
    return DomainProfile(
        name="general",
        description="General",
        collection="GeneralDocuments",
        tenant_id="tenant-a",
        prompt_name="answer",
        allow_external_model=False,
        disclaimer="Verify the result.",
        allowed_acl_labels=("public",),
        max_classification_level=1,
        retrieval=RetrievalProfile(
            mode=SearchMode.HYBRID,
            top_k=3,
            max_context_characters=500,
            filters={"status": "active"},
        ),
        graph=GraphTraversalProfile(
            enabled=True,
            max_depth=2,
            max_nodes=10,
            direction=GraphDirection.BOTH,
            relationship_types=("references",),
        ),
    )


@pytest.fixture
def search_hit() -> SearchHit:
    return SearchHit(
        chunk=DocumentChunk(
            chunk_id="chunk-1",
            domain="general",
            tenant_id="tenant-a",
            source_id="source-1",
            source_uri="https://example.test/source-1",
            text="Evidence text.",
            metadata={"kind": "example"},
            acl_labels=("public",),
            classification_level=1,
        ),
        score=0.75,
        score_type="hybrid",
    )


class _ProtocolHandler(BaseHTTPRequestHandler):
    server: _ProtocolServer

    def do_GET(self) -> None:
        self.server.requests.append(("GET", self.path, dict(self.headers), None))
        self._respond(self.server.responses.get(("GET", self.path)))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        body = json.loads(raw.decode("utf-8")) if raw else None
        self.server.requests.append(("POST", self.path, dict(self.headers), body))
        self._respond(self.server.responses.get(("POST", self.path)))

    def _respond(self, response: tuple[int, Any, str] | None) -> None:
        status, body, content_type = response or (
            404,
            {"error": "not found"},
            "application/json",
        )
        encoded = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _ProtocolServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _ProtocolHandler)
        self.responses: dict[tuple[str, str], tuple[int, Any, str]] = {}
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []


@contextmanager
def protocol_server() -> Iterator[_ProtocolServer]:
    server = _ProtocolServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def http_server():
    with protocol_server() as server:
        yield server
