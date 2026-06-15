from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    AnalysisConfig,
    ContractError,
    require_mapping,
    require_string,
    require_string_list,
)
from .io import json_text, jsonl_text, publish_reports, read_json, read_jsonl
from .metrics import latency_metrics, mean, precision, recall, reciprocal_rank
from .reports import markdown_report
from .thresholds import evaluate_thresholds

GROUP_FIELDS = (
    "query_type",
    "search_mode",
    "domain",
    "tenant_id",
    "source_version",
    "graph_depth",
    "expected_state",
)


@dataclass(frozen=True)
class AnalysisOutcome:
    report: Mapping[str, Any]
    threshold_passed: bool


def analyze_results(config: AnalysisConfig) -> AnalysisOutcome:
    if config.recall_at_k < 1:
        raise ContractError("recall_at_k must be greater than zero")
    queries = _load_queries(config.queries_path)
    results = _load_results(config.results_path)
    manifest = read_json(config.manifest_path) if config.manifest_path is not None else None
    _validate_result_set(queries, results)
    required_ids = _required_ground_truth_ids(queries.values(), results.values())
    concepts = _load_ground_truth(config.ground_truth_path, required_ids)
    rows = [
        _analyze_query(query, results[query_id], concepts, config.recall_at_k)
        for query_id, query in queries.items()
    ]
    summary = _summarize(rows)
    groups = {field: _group_rows(rows, field) for field in GROUP_FIELDS}
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "configuration": {"recall_at_k": config.recall_at_k},
        "dataset": _dataset_summary(manifest),
        "summary": summary,
        "groups": groups,
        "thresholds": None,
    }
    threshold_passed = True
    if config.thresholds_path is not None:
        threshold_result = evaluate_thresholds(read_json(config.thresholds_path), summary)
        report["thresholds"] = threshold_result
        threshold_passed = bool(threshold_result["passed"])
    failures = [
        {
            "query_id": row["query_id"],
            "reasons": row["failure_reasons"],
            "metrics": row["metrics"],
        }
        for row in rows
        if row["failure_reasons"]
    ]
    files = {
        "report.json": json_text(report),
        "report.md": markdown_report(report),
        "failures.jsonl": jsonl_text(failures),
    }
    if config.write_csv:
        files["metrics.csv"] = _metrics_csv(rows)
    publish_reports(config.output_directory, files, overwrite=config.overwrite)
    return AnalysisOutcome(report=report, threshold_passed=threshold_passed)


def _load_queries(path: Path) -> dict[str, dict[str, Any]]:
    queries: dict[str, dict[str, Any]] = {}
    required_lists = (
        "allowed_acl_labels",
        "expected_seed_nodes",
        "expected_graph_nodes",
        "expected_seed_concept_ids",
        "expected_graph_concept_ids",
        "expected_relationship_types",
        "expected_source_ids",
        "forbidden_domains",
        "forbidden_tenants",
        "forbidden_nodes",
    )
    for line_number, query in read_jsonl(path):
        context = f"{path}:{line_number}"
        query_id = require_string(query, "query_id", context)
        if query_id in queries:
            raise ContractError(f"Duplicate query_id: {query_id}")
        for field in (
            "query_type",
            "query",
            "capability_id",
            "tenant_id",
            "domain",
            "search_mode",
            "source_version",
            "expected_state",
        ):
            require_string(query, field, context)
        for field in required_lists:
            require_string_list(query, field, context)
        maximum = query.get("max_classification_level")
        depth = query.get("graph_depth")
        if not isinstance(maximum, int) or isinstance(maximum, bool):
            raise ContractError(f"{context}.max_classification_level must be an integer")
        if not isinstance(depth, int) or isinstance(depth, bool):
            raise ContractError(f"{context}.graph_depth must be an integer")
        queries[query_id] = query
    if not queries:
        raise ContractError("queries.jsonl cannot be empty")
    return queries


def _load_results(path: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for line_number, result in read_jsonl(path):
        context = f"{path}:{line_number}"
        query_id = require_string(result, "query_id", context)
        if query_id in results:
            raise ContractError(f"Duplicate result query_id: {query_id}")
        if not isinstance(result.get("ok"), bool):
            raise ContractError(f"{context}.ok must be a boolean")
        duration = result.get("duration_ms")
        if not isinstance(duration, int | float) or isinstance(duration, bool) or duration < 0:
            raise ContractError(f"{context}.duration_ms must be a non-negative number")
        for field in ("citations", "pipeline_trace"):
            value = result.get(field)
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ContractError(f"{context}.{field} must be a list of objects")
        trace = result.get("retrieval_trace")
        if trace is not None:
            _validate_trace(require_mapping(trace, f"{context}.retrieval_trace"), context)
        error = result.get("error")
        if error is not None:
            require_mapping(error, f"{context}.error")
        results[query_id] = result
    return results


def _validate_trace(trace: dict[str, Any], context: str) -> None:
    for field in ("seed_nodes", "graph_nodes"):
        value = trace.get(field)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ContractError(f"{context}.retrieval_trace.{field} must be a list of objects")
    require_string_list(trace, "relationship_types", f"{context}.retrieval_trace")


def _validate_result_set(
    queries: Mapping[str, Any],
    results: Mapping[str, Any],
) -> None:
    missing = sorted(set(queries) - set(results))
    extra = sorted(set(results) - set(queries))
    if missing:
        raise ContractError(f"Missing results for query IDs: {', '.join(missing)}")
    if extra:
        raise ContractError(f"Unknown result query IDs: {', '.join(extra)}")


def _required_ground_truth_ids(
    queries: Iterable[Mapping[str, Any]],
    results: Iterable[Mapping[str, Any]],
) -> set[str]:
    identifiers: set[str] = set()
    for query in queries:
        identifiers.update(query["expected_seed_nodes"])
        identifiers.update(query["expected_graph_nodes"])
    for result in results:
        trace = result.get("retrieval_trace")
        if trace is not None:
            for field in ("seed_nodes", "graph_nodes"):
                identifiers.update(
                    item["chunk_id"]
                    for item in trace[field]
                    if isinstance(item.get("chunk_id"), str)
                )
    return identifiers


def _load_ground_truth(path: Path, required_ids: set[str]) -> dict[str, set[str]]:
    concepts: dict[str, set[str]] = {}
    for line_number, record in read_jsonl(path):
        context = f"{path}:{line_number}"
        chunk_id = require_string(record, "chunk_id", context)
        if chunk_id not in required_ids:
            continue
        if chunk_id in concepts:
            raise ContractError(f"Duplicate ground-truth chunk_id: {chunk_id}")
        concepts[chunk_id] = set(require_string_list(record, "concept_ids", context))
    return concepts


def _analyze_query(
    query: Mapping[str, Any],
    result: Mapping[str, Any],
    concepts: Mapping[str, set[str]],
    recall_at_k: int,
) -> dict[str, Any]:
    trace = result.get("retrieval_trace")
    trace_available = trace is not None
    seed_evidence = [] if trace is None else trace["seed_nodes"]
    graph_evidence = [] if trace is None else trace["graph_nodes"]
    seed_ids = _evidence_ids(seed_evidence)
    graph_ids = _evidence_ids(graph_evidence)
    seed_concepts = _concept_sequence(seed_ids, concepts)
    graph_concepts = _concept_sequence(graph_ids, concepts)
    expected_seed = set(query["expected_seed_nodes"])
    expected_graph = set(query["expected_graph_nodes"])
    expected_seed_concepts = set(query["expected_seed_concept_ids"])
    expected_graph_concepts = set(query["expected_graph_concept_ids"])
    relationships = [] if trace is None else trace["relationship_types"]
    expected_relationships = set(query["expected_relationship_types"])
    citations = result["citations"]
    citation_sources = [
        item["source_id"] for item in citations if isinstance(item.get("source_id"), str)
    ]
    evidence = [*seed_evidence, *graph_evidence, *citations]
    leaks = _security_leaks(query, evidence)
    ok = bool(result["ok"])
    expected_trace = bool(expected_seed or expected_graph)
    metrics: dict[str, Any] = {
        "trace_available": trace_available,
        "seed_recall": recall(expected_seed, seed_ids) if trace_available else None,
        "seed_precision": precision(expected_seed, seed_ids) if trace_available else None,
        "recall_at_k": (recall(expected_seed, seed_ids[:recall_at_k]) if trace_available else None),
        "mean_reciprocal_rank": (
            reciprocal_rank(expected_seed, seed_ids) if trace_available else None
        ),
        "graph_recall": recall(expected_graph, graph_ids) if trace_available else None,
        "graph_precision": precision(expected_graph, graph_ids) if trace_available else None,
        "seed_concept_recall": (
            recall(expected_seed_concepts, seed_concepts) if trace_available else None
        ),
        "seed_concept_precision": (
            precision(expected_seed_concepts, seed_concepts) if trace_available else None
        ),
        "concept_recall_at_k": (
            recall(
                expected_seed_concepts,
                _concept_sequence(seed_ids[:recall_at_k], concepts),
            )
            if trace_available
            else None
        ),
        "concept_mean_reciprocal_rank": (
            reciprocal_rank(expected_seed_concepts, seed_concepts) if trace_available else None
        ),
        "graph_concept_recall": (
            recall(expected_graph_concepts, graph_concepts) if trace_available else None
        ),
        "graph_concept_precision": (
            precision(expected_graph_concepts, graph_concepts) if trace_available else None
        ),
        "relationship_coverage": (
            recall(expected_relationships, relationships) if trace_available else None
        ),
        "full_expected_path": (
            expected_graph.issubset(graph_ids) and expected_relationships.issubset(relationships)
            if trace_available
            else None
        ),
        "missing_expected_nodes": (
            len(expected_seed - set(seed_ids)) + len(expected_graph - set(graph_ids))
            if trace_available
            else None
        ),
        "citation_coverage": recall(set(query["expected_source_ids"]), citation_sources),
        "has_citations": bool(citations),
        "response_without_evidence": (ok and bool(query["expected_source_ids"]) and not citations),
        "success": ok,
        "duration_ms": float(result["duration_ms"]),
        "forbidden_domain_leaks": leaks["domains"],
        "forbidden_tenant_leaks": leaks["tenants"],
        "forbidden_node_leaks": leaks["nodes"],
        "acl_violations": leaks["acl"],
        "classification_violations": leaks["classification"],
        "security_leaks": sum(leaks.values()),
    }
    failure_reasons = _failure_reasons(metrics, expected_trace, result)
    return {
        "query_id": query["query_id"],
        "dimensions": {field: query[field] for field in GROUP_FIELDS},
        "metrics": metrics,
        "failure_reasons": failure_reasons,
    }


def _evidence_ids(evidence: Sequence[Mapping[str, Any]]) -> list[str]:
    return [item["chunk_id"] for item in evidence if isinstance(item.get("chunk_id"), str)]


def _concept_sequence(
    identifiers: Sequence[str],
    concepts: Mapping[str, set[str]],
) -> list[str]:
    sequence: list[str] = []
    seen: set[str] = set()
    for identifier in identifiers:
        for concept in sorted(concepts.get(identifier, set())):
            if concept not in seen:
                seen.add(concept)
                sequence.append(concept)
    return sequence


def _security_leaks(
    query: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    forbidden_domains = set(query["forbidden_domains"])
    forbidden_tenants = set(query["forbidden_tenants"])
    forbidden_nodes = set(query["forbidden_nodes"])
    allowed_acl = set(query["allowed_acl_labels"])
    maximum_classification = query["max_classification_level"]
    return {
        "domains": sum(item.get("domain") in forbidden_domains for item in evidence),
        "tenants": sum(item.get("tenant_id") in forbidden_tenants for item in evidence),
        "nodes": sum(item.get("chunk_id") in forbidden_nodes for item in evidence),
        "acl": sum(bool(set(item.get("acl_labels", [])) - allowed_acl) for item in evidence),
        "classification": sum(
            isinstance(item.get("classification_level"), int)
            and item["classification_level"] > maximum_classification
            for item in evidence
        ),
    }


def _failure_reasons(
    metrics: Mapping[str, Any],
    expected_trace: bool,
    result: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if not result["ok"]:
        error = result.get("error") or {}
        reasons.append(f"request_error:{error.get('code', 'unknown')}")
    if expected_trace and not metrics["trace_available"]:
        reasons.append("missing_retrieval_trace")
    if metrics["security_leaks"]:
        reasons.append("security_leak")
    if result["ok"] and metrics["response_without_evidence"]:
        reasons.append("response_without_evidence")
    if metrics["citation_coverage"] < 1:
        reasons.append("incomplete_citation_coverage")
    if metrics["seed_recall"] is not None and metrics["seed_recall"] < 1:
        reasons.append("incomplete_seed_recall")
    if metrics["graph_recall"] is not None and metrics["graph_recall"] < 1:
        reasons.append("incomplete_graph_recall")
    return reasons


def _summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    metrics = [row["metrics"] for row in rows]
    durations = [item["duration_ms"] for item in metrics]
    total_duration = sum(durations)
    summary: dict[str, Any] = {
        "query_count": count,
        "successful_queries": sum(item["success"] for item in metrics),
        "failed_queries": sum(not item["success"] for item in metrics),
        "success_rate": mean(float(item["success"]) for item in metrics),
        "error_rate": mean(float(not item["success"]) for item in metrics),
        "retrieval_trace_coverage": mean(float(item["trace_available"]) for item in metrics),
        "queries_per_second": 0.0 if total_duration == 0 else count / (total_duration / 1000),
        "timeout_count": sum("request_error:timeout" in row["failure_reasons"] for row in rows),
        "failure_count": sum(bool(row["failure_reasons"]) for row in rows),
        "forbidden_domain_leaks": sum(item["forbidden_domain_leaks"] for item in metrics),
        "forbidden_tenant_leaks": sum(item["forbidden_tenant_leaks"] for item in metrics),
        "forbidden_node_leaks": sum(item["forbidden_node_leaks"] for item in metrics),
        "acl_violations": sum(item["acl_violations"] for item in metrics),
        "classification_violations": sum(item["classification_violations"] for item in metrics),
        "forbidden_leaks": sum(item["security_leaks"] for item in metrics),
        "response_without_evidence_count": sum(
            item["response_without_evidence"] for item in metrics
        ),
        "citation_presence_rate": mean(float(item["has_citations"]) for item in metrics),
        "full_expected_path_rate": _available_mean(metrics, "full_expected_path"),
        "missing_expected_nodes": _available_sum(metrics, "missing_expected_nodes"),
    }
    for name in (
        "seed_recall",
        "seed_precision",
        "recall_at_k",
        "mean_reciprocal_rank",
        "graph_recall",
        "graph_precision",
        "seed_concept_recall",
        "seed_concept_precision",
        "concept_recall_at_k",
        "concept_mean_reciprocal_rank",
        "graph_concept_recall",
        "graph_concept_precision",
        "relationship_coverage",
        "citation_coverage",
    ):
        summary[name] = _available_mean(metrics, name)
    summary.update(latency_metrics(durations))
    return summary


def _available_mean(metrics: Sequence[Mapping[str, Any]], name: str) -> float | None:
    return mean(float(item[name]) for item in metrics if item[name] is not None)


def _available_sum(metrics: Sequence[Mapping[str, Any]], name: str) -> int | None:
    values = [int(item[name]) for item in metrics if item[name] is not None]
    return None if not values else sum(values)


def _group_rows(
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> dict[str, Mapping[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["dimensions"][field])].append(row)
    return {key: _summarize(grouped[key]) for key in sorted(grouped)}


def _dataset_summary(manifest: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if manifest is None:
        return None
    return {
        "schema_version": manifest.get("schema_version"),
        "configuration": manifest.get("configuration"),
        "files": manifest.get("files"),
    }


def _metrics_csv(rows: Sequence[Mapping[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    metric_names = sorted({name for row in rows for name in row["metrics"]})
    writer = csv.DictWriter(
        stream,
        fieldnames=["query_id", *GROUP_FIELDS, *metric_names, "failure_reasons"],
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "query_id": row["query_id"],
                **row["dimensions"],
                **row["metrics"],
                "failure_reasons": "|".join(row["failure_reasons"]),
            }
        )
    return stream.getvalue()
