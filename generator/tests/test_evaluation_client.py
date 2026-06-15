from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from conftest import read_jsonl

from dataset_generator.evaluation.client import execute_queries
from dataset_generator.evaluation.contracts import (
    ContractError,
    ExecutionConfig,
    OutputConflictError,
)


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def execution_config(tmp_path: Path, queries: Path) -> ExecutionConfig:
    return ExecutionConfig(
        queries_path=queries,
        output_path=tmp_path / "results.jsonl",
        endpoint="http://localhost:8000/v1/query",
        timeout_seconds=2,
        access_token="access-token",
    )


def write_queries(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_execute_queries_normalizes_success_response(tmp_path: Path) -> None:
    queries = tmp_path / "queries.jsonl"
    write_queries(
        queries,
        [
            {
                "query_id": "q1",
                "query": "Find orders",
                "capability_id": "orders-query",
                "filters": {},
            }
        ],
    )
    seen = {}
    ticks = iter((1.0, 1.025))

    def open_url(request, timeout):
        seen["timeout"] = timeout
        seen["headers"] = dict(request.header_items())
        seen["payload"] = json.loads(request.data)
        return FakeResponse(
            json.dumps(
                {
                    "request_id": "body-request",
                    "answer": "Answer",
                    "citations": [
                        {
                            "chunk_id": "chunk-1",
                            "source_id": "source-1",
                            "metadata": {
                                "domain": "Orders",
                                "tenant_id": "tenant-1",
                                "acl_labels": ["internal"],
                                "classification_level": 1,
                            },
                            "rank": 1,
                        }
                    ],
                    "pipeline_trace": ["retrieve"],
                    "retrieval_trace": {
                        "seed_nodes": [
                            {
                                "chunk_id": "chunk-1",
                                "source_id": "source-1",
                                "domain": "Orders",
                                "tenant_id": "tenant-1",
                                "acl_labels": ["internal"],
                                "classification_level": 1,
                                "rank": 1,
                            }
                        ],
                        "graph_nodes": [],
                        "relationship_types": ["calls"],
                    },
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                        "cost": 0.01,
                    },
                }
            ).encode(),
            headers={"X-Request-ID": "header-request"},
        )

    assert (
        execute_queries(
            execution_config(tmp_path, queries), open_url=open_url, clock=lambda: next(ticks)
        )
        == 1
    )

    result = read_jsonl(tmp_path / "results.jsonl")[0]
    assert result["ok"] is True
    assert result["request_id"] == "body-request"
    assert result["duration_ms"] == 25.0
    assert result["retrieval_trace"]["seed_nodes"][0]["domain"] == "Orders"
    assert result["usage"]["total_tokens"] == 12.0
    assert seen["payload"] == {
        "query": "Find orders",
        "capability_id": "orders-query",
        "session_id": "q1",
        "filters": {},
    }
    assert seen["timeout"] == 2
    assert seen["headers"]["Authorization"] == "Bearer access-token"
    assert seen["headers"]["X-sovereignflow-diagnostics"] == "true"
    assert result["pipeline_trace"] == [{"step_id": "retrieve"}]


@pytest.mark.parametrize(
    ("exception", "code", "status"),
    [
        (HTTPError("url", 503, "error", {}, None), "http_error", 503),
        (TimeoutError(), "timeout", None),
        (URLError(TimeoutError()), "timeout", None),
        (URLError("offline"), "transport_error", None),
    ],
)
def test_execute_queries_records_controlled_request_errors(
    tmp_path: Path,
    exception: Exception,
    code: str,
    status: int | None,
) -> None:
    queries = tmp_path / "queries.jsonl"
    write_queries(
        queries,
        [{"query_id": "q1", "query": "Query", "capability_id": "domain-query"}],
    )
    ticks = iter((1.0, 1.1))

    def open_url(*args, **kwargs):
        raise exception

    execute_queries(
        execution_config(tmp_path, queries), open_url=open_url, clock=lambda: next(ticks)
    )

    result = read_jsonl(tmp_path / "results.jsonl")[0]
    assert result["ok"] is False
    assert result["status_code"] == status
    assert result["error"]["code"] == code


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        json.dumps({"answer": "", "citations": [], "pipeline_trace": []}).encode(),
        json.dumps({"answer": "ok", "citations": "bad", "pipeline_trace": []}).encode(),
        json.dumps({"answer": "ok", "citations": [], "pipeline_trace": "bad"}).encode(),
        json.dumps(
            {
                "answer": "ok",
                "citations": [],
                "pipeline_trace": [],
                "retrieval_trace": {
                    "seed_nodes": "bad",
                    "graph_nodes": [],
                    "relationship_types": [],
                },
            }
        ).encode(),
        json.dumps(
            {
                "answer": "ok",
                "citations": [{"acl_labels": [1]}],
                "pipeline_trace": [],
            }
        ).encode(),
        json.dumps(
            {
                "answer": "ok",
                "citations": [{"classification_level": "secret"}],
                "pipeline_trace": [],
            }
        ).encode(),
        json.dumps(
            {
                "answer": "ok",
                "citations": [],
                "pipeline_trace": [],
                "retrieval_trace": {
                    "seed_nodes": [],
                    "graph_nodes": "bad",
                    "relationship_types": [],
                },
            }
        ).encode(),
        json.dumps(
            {
                "answer": "ok",
                "citations": [],
                "pipeline_trace": [],
                "retrieval_trace": {"seed_nodes": [], "graph_nodes": [], "relationship_types": [1]},
            }
        ).encode(),
    ],
)
def test_invalid_api_response_is_recorded(tmp_path: Path, payload: bytes) -> None:
    queries = tmp_path / "queries.jsonl"
    write_queries(
        queries,
        [{"query_id": "q1", "query": "Query", "capability_id": "domain-query"}],
    )

    execute_queries(
        execution_config(tmp_path, queries),
        open_url=lambda *args, **kwargs: FakeResponse(payload),
        clock=lambda: 1.0,
    )

    assert read_jsonl(tmp_path / "results.jsonl")[0]["error"]["code"] == "invalid_response"


def test_execute_queries_validates_config_input_and_output(tmp_path: Path) -> None:
    queries = tmp_path / "queries.jsonl"
    write_queries(
        queries,
        [{"query_id": "q1", "query": "Query", "capability_id": "domain-query"}],
    )
    config = execution_config(tmp_path, queries)

    with pytest.raises(ContractError, match="timeout_seconds"):
        execute_queries(ExecutionConfig(**{**config.__dict__, "timeout_seconds": 0}))
    with pytest.raises(ContractError, match="endpoint"):
        execute_queries(ExecutionConfig(**{**config.__dict__, "endpoint": "ftp://invalid"}))
    with pytest.raises(ContractError, match="access_token"):
        execute_queries(ExecutionConfig(**{**config.__dict__, "access_token": ""}))

    execute_queries(
        config,
        open_url=lambda *args, **kwargs: FakeResponse(
            json.dumps({"answer": "ok", "citations": [], "pipeline_trace": []}).encode()
        ),
    )
    with pytest.raises(OutputConflictError):
        execute_queries(config)
    assert (
        execute_queries(
            ExecutionConfig(**{**config.__dict__, "overwrite": True}),
            open_url=lambda *args, **kwargs: FakeResponse(
                json.dumps({"answer": "ok", "citations": [], "pipeline_trace": []}).encode()
            ),
        )
        == 1
    )

    directory_output = tmp_path / "directory-result"
    directory_output.mkdir()
    with pytest.raises(ContractError, match="not a file"):
        execute_queries(
            ExecutionConfig(
                **{
                    **config.__dict__,
                    "output_path": directory_output,
                    "overwrite": True,
                }
            ),
        )


@pytest.mark.parametrize(
    ("records", "message"),
    [
        (
            [{"query_id": "q1", "query": "", "capability_id": "domain-query"}],
            "query",
        ),
        (
            [
                {
                    "query_id": "q1",
                    "query": "Query",
                    "capability_id": "domain-query",
                    "filters": [],
                }
            ],
            "filters",
        ),
        (
            [
                {"query_id": "q1", "query": "Query", "capability_id": "domain-query"},
                {"query_id": "q1", "query": "Query", "capability_id": "domain-query"},
            ],
            "Duplicate",
        ),
    ],
)
def test_execute_queries_rejects_invalid_queries(
    tmp_path: Path,
    records: list[dict],
    message: str,
) -> None:
    queries = tmp_path / "queries.jsonl"
    write_queries(queries, records)

    with pytest.raises(ContractError, match=message):
        execute_queries(
            execution_config(tmp_path, queries),
            open_url=lambda *args, **kwargs: FakeResponse(b"{}"),
        )
    assert not (tmp_path / "results.jsonl").exists()


@pytest.mark.integration
def test_execute_queries_against_http_server(tmp_path: Path) -> None:
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            received["payload"] = json.loads(self.rfile.read(length))
            body = json.dumps(
                {
                    "answer": "HTTP answer",
                    "citations": [],
                    "pipeline_trace": [],
                    "retrieval_trace": {
                        "seed_nodes": [],
                        "graph_nodes": [],
                        "relationship_types": [],
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    queries = tmp_path / "queries.jsonl"
    write_queries(
        queries,
        [{"query_id": "q1", "query": "Query", "capability_id": "domain-query"}],
    )
    try:
        config = ExecutionConfig(
            queries_path=queries,
            output_path=tmp_path / "results.jsonl",
            endpoint=f"http://127.0.0.1:{server.server_port}/v1/query",
            timeout_seconds=2,
            access_token="access-token",
        )
        assert execute_queries(config) == 1
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert received["payload"]["session_id"] == "q1"
    assert read_jsonl(tmp_path / "results.jsonl")[0]["answer"] == "HTTP answer"
