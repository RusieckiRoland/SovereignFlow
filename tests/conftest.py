from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from sovereignflow.application import PipelineEngine, RagQueryService, default_action_registry
from sovereignflow.application.pipeline import ModelServerRuntime
from sovereignflow.domain import (
    AuthorizationContext,
    ClearanceLevelModel,
    DocumentChunk,
    DocumentSecurity,
    DomainProfile,
    GraphDirection,
    GraphTraversalProfile,
    ModelGeneration,
    ModelServerDefinition,
    ModelServerSecurityProfile,
    PipelineDefinition,
    PipelineStepDefinition,
    RetrievalProfile,
    SearchHit,
    SearchMode,
    SecurityModel,
    SecurityModelKind,
    SubjectSecurity,
    TrustBoundary,
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
    def name(self) -> str:
        return "stub-provider"

    @property
    def model_id(self) -> str:
        return "stub-model"

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


def authorization_context(**overrides) -> AuthorizationContext:
    values = {
        "subject": "user-1",
        "tenant_id": "tenant-a",
        "roles": ("user",),
        "groups": ("group-a",),
        "acl_labels": ("public",),
        "security": SubjectSecurity(clearance_label="PUBLIC"),
        "allow_external_model": False,
        "diagnostic_access": True,
    }
    values.update(overrides)
    return AuthorizationContext(**values)


class StubAuthenticator:
    def __init__(self, context: AuthorizationContext | None = None) -> None:
        self.context = context or authorization_context()
        self.tokens: list[str] = []

    def authenticate(self, access_token: str) -> AuthorizationContext:
        self.tokens.append(access_token)
        return self.context


def default_pipeline() -> PipelineDefinition:
    action_ids = (
        "normalize_query",
        "retrieve",
        "expand_graph",
        "manage_context_budget",
        "enforce_model_transmission_policy",
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
                action_version=(
                    "2.0" if action_id == "enforce_model_transmission_policy" else "1.0"
                ),
                next_step_id=action_ids[index + 1] if index + 1 < len(action_ids) else None,
                terminal=index + 1 == len(action_ids),
                config=_default_step_config(action_id),
            )
            for index, action_id in enumerate(action_ids)
        ),
        checksum="a" * 64,
    )


def _default_step_config(action_id: str) -> dict:
    configs = {
        "retrieve": {
            "query_source": "normalized_query",
            "search_mode": "hybrid",
            "top_k": 3,
            "filters": {},
        },
        "expand_graph": {
            "enabled": True,
            "max_depth": 2,
            "max_nodes": 10,
            "direction": "both",
            "relationship_types": ["references"],
        },
        "manage_context_budget": {
            "source": "hits",
            "target": "evidence",
            "max_context_characters": 500,
        },
        "enforce_model_transmission_policy": {
            "selected_model_server_id": "default-model",
            "external_transmission": "allowed",
        },
        "call_model": {
            "prompt_key": "answer",
            "user_parts": {
                "user_question": {
                    "source": "normalized_query",
                    "template": "USER QUESTION\n{}\n\n",
                },
                "evidence": {
                    "source": "evidence",
                    "template": "EVIDENCE\n{}\n\n",
                },
            },
        },
    }
    return configs.get(action_id, {})


def build_query_service(
    *,
    domain: DomainProfile,
    retrieval,
    model,
    prompts,
    graph: StubGraph | None = None,
    audit: StubAudit | None = None,
    pipeline: PipelineDefinition | None = None,
    conversation_history=None,
) -> RagQueryService:
    selected_audit = audit or StubAudit()
    return RagQueryService(
        domain=domain,
        retrieval=retrieval,
        graph=graph or StubGraph(),
        model_servers=model_servers(
            default=model,
            trust_boundary=(
                TrustBoundary.EXTERNAL
                if getattr(model, "scope", "") == "external"
                else TrustBoundary.INTERNAL
            ),
            clearance_label="INTERNAL",
        ),
        prompts=prompts,
        pipeline=pipeline or default_pipeline(),
        conversation_history=conversation_history,
        engine=PipelineEngine(
            registry=default_action_registry(),
            audit=selected_audit,
            monotonic=lambda: 1.0,
            run_id_factory=lambda: "00000000-0000-0000-0000-000000000001",
        ),
    )


def model_servers(
    *,
    default,
    trust_boundary: TrustBoundary = TrustBoundary.INTERNAL,
    clearance_label: str = "INTERNAL",
    reroute_to: str | None = None,
    reroute_model=None,
    reroute_clearance_label: str = "INTERNAL",
) -> dict[str, ModelServerRuntime]:
    servers = {
        "default-model": ModelServerRuntime(
            definition=ModelServerDefinition(
                server_id="default-model",
                trust_boundary=trust_boundary,
                security_profile=ModelServerSecurityProfile(
                    SecurityModelKind.CLEARANCE_LEVEL,
                    clearance_label=clearance_label,
                ),
                security_reroute_server_id=reroute_to,
            ),
            gateway=default,
        )
    }
    if reroute_to is not None:
        servers[reroute_to] = ModelServerRuntime(
            definition=ModelServerDefinition(
                server_id=reroute_to,
                trust_boundary=TrustBoundary.INTERNAL,
                security_profile=ModelServerSecurityProfile(
                    SecurityModelKind.CLEARANCE_LEVEL,
                    clearance_label=reroute_clearance_label,
                ),
            ),
            gateway=reroute_model or default,
        )
    return servers


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
        security_model=SecurityModel(
            kind=SecurityModelKind.CLEARANCE_LEVEL,
            clearance_level=ClearanceLevelModel({"PUBLIC": 0, "INTERNAL": 10}),
        ),
        retrieval=RetrievalProfile(
            mode=SearchMode.HYBRID,
            top_k=3,
            max_context_characters=500,
            filters={"status": "active"},
            allowed_filter_fields=("country", "status"),
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
            security=DocumentSecurity(clearance_label="PUBLIC"),
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
