from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from sovereignflow.domain import (
    DocumentChunk,
    DomainProfile,
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


class StubModel:
    def __init__(self, *, scope: str = "local", answer: str = "answer") -> None:
        self._scope = scope
        self.answer = answer
        self.calls = []
        self.healthy = True

    @property
    def scope(self) -> str:
        return self._scope

    def generate(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.answer

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
