from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import (
    ContractError,
    ExecutionConfig,
    optional_number,
    optional_string,
    require_mapping,
    require_string,
)
from .io import read_jsonl, write_jsonl_atomic

OpenUrl = Callable[..., Any]


def execute_queries(
    config: ExecutionConfig,
    *,
    open_url: OpenUrl = urlopen,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    _validate_execution_config(config)
    count = 0

    def records() -> Iterator[Mapping[str, Any]]:
        nonlocal count
        seen: set[str] = set()
        for line_number, query in read_jsonl(config.queries_path):
            context = f"{config.queries_path}:{line_number}"
            query_id = require_string(query, "query_id", context)
            if query_id in seen:
                raise ContractError(f"Duplicate query_id: {query_id}")
            seen.add(query_id)
            count += 1
            yield _execute_query(
                query,
                config=config,
                open_url=open_url,
                clock=clock,
                context=context,
            )

    write_jsonl_atomic(config.output_path, records(), overwrite=config.overwrite)
    return count


def _execute_query(
    query: dict[str, Any],
    *,
    config: ExecutionConfig,
    open_url: OpenUrl,
    clock: Callable[[], float],
    context: str,
) -> Mapping[str, Any]:
    query_id = require_string(query, "query_id", context)
    payload = _request_payload(query, context)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.diagnostic_key is not None:
        headers["X-SovereignFlow-Diagnostic-Key"] = config.diagnostic_key
    request = Request(
        config.endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = clock()
    try:
        with open_url(request, timeout=config.timeout_seconds) as response:
            status_code = int(response.status)
            response_headers = response.headers
            body = response.read()
    except HTTPError as exc:
        duration = _duration_ms(started, clock)
        return _error_result(
            query_id,
            duration,
            status_code=exc.code,
            code="http_error",
            message=f"HTTP {exc.code}",
        )
    except TimeoutError:
        return _error_result(
            query_id,
            _duration_ms(started, clock),
            status_code=None,
            code="timeout",
            message="Request timed out",
        )
    except URLError as exc:
        code = "timeout" if isinstance(exc.reason, TimeoutError) else "transport_error"
        message = "Request timed out" if code == "timeout" else "Request failed"
        return _error_result(
            query_id,
            _duration_ms(started, clock),
            status_code=None,
            code=code,
            message=message,
        )
    duration = _duration_ms(started, clock)
    try:
        decoded = json.loads(body)
        response_mapping = require_mapping(decoded, "response")
        return _success_result(
            query_id,
            duration,
            status_code,
            response_mapping,
            response_headers.get("X-Request-ID"),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ContractError) as exc:
        return _error_result(
            query_id,
            duration,
            status_code=status_code,
            code="invalid_response",
            message=str(exc),
        )


def _request_payload(query: dict[str, Any], context: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": require_string(query, "query", context),
        "domain": require_string(query, "domain", context),
        "session_id": require_string(query, "query_id", context),
    }
    filters = query.get("filters")
    if filters is not None:
        payload["filters"] = require_mapping(filters, f"{context}.filters")
    return payload


def _success_result(
    query_id: str,
    duration_ms: float,
    status_code: int,
    response: dict[str, Any],
    header_request_id: str | None,
) -> Mapping[str, Any]:
    citations_value = response.get("citations", [])
    trace_value = response.get("pipeline_trace", [])
    if not isinstance(citations_value, list) or any(
        not isinstance(item, dict) for item in citations_value
    ):
        raise ContractError("response.citations must be a list of objects")
    if not isinstance(trace_value, list) or any(not isinstance(item, dict) for item in trace_value):
        raise ContractError("response.pipeline_trace must be a list of objects")
    retrieval_trace = _normalize_retrieval_trace(response.get("retrieval_trace"))
    usage = _normalize_usage(response.get("usage"))
    return {
        "query_id": query_id,
        "request_id": optional_string(response, "request_id", "response") or header_request_id,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "ok": True,
        "answer": require_string(response, "answer", "response"),
        "citations": [_normalize_evidence(item, "response.citations") for item in citations_value],
        "pipeline_trace": trace_value,
        "retrieval_trace": retrieval_trace,
        "usage": usage,
        "error": None,
    }


def _normalize_retrieval_trace(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    trace = require_mapping(value, "response.retrieval_trace")
    seed_value = trace.get("seed_nodes", [])
    graph_value = trace.get("graph_nodes", [])
    relationships = trace.get("relationship_types", [])
    if not isinstance(seed_value, list) or any(not isinstance(item, dict) for item in seed_value):
        raise ContractError("response.retrieval_trace.seed_nodes must be a list of objects")
    if not isinstance(graph_value, list) or any(not isinstance(item, dict) for item in graph_value):
        raise ContractError("response.retrieval_trace.graph_nodes must be a list of objects")
    if not isinstance(relationships, list) or any(
        not isinstance(item, str) for item in relationships
    ):
        raise ContractError("response.retrieval_trace.relationship_types must be a list of strings")
    return {
        "seed_nodes": [
            _normalize_evidence(item, "response.retrieval_trace.seed_nodes") for item in seed_value
        ],
        "graph_nodes": [
            _normalize_evidence(item, "response.retrieval_trace.graph_nodes")
            for item in graph_value
        ],
        "relationship_types": relationships,
    }


def _normalize_evidence(item: dict[str, Any], context: str) -> Mapping[str, Any]:
    metadata_value = item.get("metadata", {})
    metadata = require_mapping(metadata_value, f"{context}.metadata")
    acl_value = item.get("acl_labels", metadata.get("acl_labels", []))
    if not isinstance(acl_value, list) or any(not isinstance(label, str) for label in acl_value):
        raise ContractError(f"{context}.acl_labels must be a list of strings")
    classification = item.get(
        "classification_level",
        metadata.get("classification_level"),
    )
    if classification is not None and (
        not isinstance(classification, int) or isinstance(classification, bool)
    ):
        raise ContractError(f"{context}.classification_level must be an integer or null")
    return {
        "chunk_id": optional_string(item, "chunk_id", context),
        "source_id": optional_string(item, "source_id", context),
        "domain": optional_string(item, "domain", context)
        or optional_string(metadata, "domain", f"{context}.metadata"),
        "tenant_id": optional_string(item, "tenant_id", context)
        or optional_string(metadata, "tenant_id", f"{context}.metadata"),
        "acl_labels": acl_value,
        "classification_level": classification,
        "rank": optional_number(item, "rank", context),
    }


def _normalize_usage(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    usage = require_mapping(value, "response.usage")
    return {
        "prompt_tokens": optional_number(usage, "prompt_tokens", "response.usage"),
        "completion_tokens": optional_number(usage, "completion_tokens", "response.usage"),
        "total_tokens": optional_number(usage, "total_tokens", "response.usage"),
        "cost": optional_number(usage, "cost", "response.usage"),
    }


def _error_result(
    query_id: str,
    duration_ms: float,
    *,
    status_code: int | None,
    code: str,
    message: str,
) -> Mapping[str, Any]:
    return {
        "query_id": query_id,
        "request_id": None,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "ok": False,
        "answer": None,
        "citations": [],
        "pipeline_trace": [],
        "retrieval_trace": None,
        "usage": None,
        "error": {"code": code, "message": message},
    }


def _duration_ms(started: float, clock: Callable[[], float]) -> float:
    return round(max(0.0, clock() - started) * 1000, 3)


def _validate_execution_config(config: ExecutionConfig) -> None:
    if config.timeout_seconds <= 0:
        raise ContractError("timeout_seconds must be greater than zero")
    if not config.endpoint.startswith(("http://", "https://")):
        raise ContractError("endpoint must use http or https")
