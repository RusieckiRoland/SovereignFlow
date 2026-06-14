from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import read_jsonl

from dataset_generator.evaluation.analyzer import analyze_results
from dataset_generator.evaluation.contracts import (
    AnalysisConfig,
    ContractError,
    OutputConflictError,
)
from dataset_generator.evaluation.thresholds import evaluate_thresholds


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def analysis_config(
    tmp_path: Path,
    dataset: dict,
    *,
    thresholds: Path | None = None,
    overwrite: bool = False,
    write_csv: bool = True,
) -> AnalysisConfig:
    results_path = tmp_path / "results.jsonl"
    if not results_path.exists():
        write_jsonl(results_path, dataset["results"])
    return AnalysisConfig(
        queries_path=dataset["directory"] / "queries.jsonl",
        results_path=results_path,
        ground_truth_path=dataset["directory"] / "ground_truth.jsonl",
        output_directory=tmp_path / "report",
        manifest_path=dataset["directory"] / "manifest.json",
        thresholds_path=thresholds,
        recall_at_k=10,
        overwrite=overwrite,
        write_csv=write_csv,
    )


@pytest.mark.integration
def test_analyze_perfect_results_and_publish_reports(
    tmp_path: Path,
    evaluation_dataset: dict,
) -> None:
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(
        json.dumps(
            {
                "minimum_seed_recall": 1,
                "minimum_graph_recall": 1,
                "minimum_seed_concept_recall": 1,
                "minimum_graph_concept_recall": 1,
                "minimum_citation_coverage": 1,
                "minimum_success_rate": 1,
                "maximum_forbidden_leaks": 0,
                "maximum_error_rate": 0,
                "maximum_p95_latency_ms": 80,
            }
        ),
        encoding="utf-8",
    )

    outcome = analyze_results(analysis_config(tmp_path, evaluation_dataset, thresholds=thresholds))

    summary = outcome.report["summary"]
    assert outcome.threshold_passed is True
    assert summary["query_count"] == 8
    assert summary["seed_recall"] == 1
    assert summary["graph_recall"] == 1
    assert summary["seed_concept_recall"] == 1
    assert summary["graph_concept_recall"] == 1
    assert summary["citation_coverage"] == 1
    assert summary["forbidden_leaks"] == 0
    assert summary["p95_latency_ms"] == pytest.approx(76.5)
    assert summary["queries_per_second"] == pytest.approx(22.222222)
    assert outcome.report["dataset"]["schema_version"] == "2.0"
    assert set(outcome.report["groups"]) == {
        "query_type",
        "search_mode",
        "domain",
        "tenant_id",
        "source_version",
        "graph_depth",
        "expected_state",
    }
    assert (tmp_path / "report" / "report.json").exists()
    assert "# SovereignFlow dataset evaluation" in (tmp_path / "report" / "report.md").read_text(
        encoding="utf-8"
    )
    assert read_jsonl(tmp_path / "report" / "failures.jsonl") == []
    assert "query_id,query_type" in (tmp_path / "report" / "metrics.csv").read_text(
        encoding="utf-8"
    )


def test_analysis_detects_quality_security_and_request_failures(
    tmp_path: Path,
    evaluation_dataset: dict,
) -> None:
    results = evaluation_dataset["results"]
    results[0]["retrieval_trace"]["seed_nodes"] = []
    leaked = dict(results[1]["retrieval_trace"]["graph_nodes"][0])
    leaked.update(
        {
            "chunk_id": evaluation_dataset["queries"][1]["forbidden_nodes"][0]
            if evaluation_dataset["queries"][1]["forbidden_nodes"]
            else "forbidden-node",
            "domain": evaluation_dataset["queries"][1]["forbidden_domains"][0],
            "tenant_id": evaluation_dataset["queries"][1]["forbidden_tenants"][0],
            "acl_labels": ["not-allowed"],
            "classification_level": 99,
        }
    )
    evaluation_dataset["queries"][1]["forbidden_nodes"] = [leaked["chunk_id"]]
    results[1]["retrieval_trace"]["graph_nodes"].append(leaked)
    results[2]["retrieval_trace"] = None
    results[3].update(
        {
            "ok": False,
            "status_code": None,
            "answer": None,
            "citations": [],
            "retrieval_trace": None,
            "error": {"code": "timeout", "message": "Request timed out"},
        }
    )
    results[4]["citations"] = []
    write_jsonl(tmp_path / "results.jsonl", results)
    write_jsonl(
        evaluation_dataset["directory"] / "queries.jsonl",
        evaluation_dataset["queries"],
    )

    outcome = analyze_results(analysis_config(tmp_path, evaluation_dataset))

    summary = outcome.report["summary"]
    assert summary["seed_recall"] < 1
    assert summary["graph_precision"] < 1
    assert summary["retrieval_trace_coverage"] == 0.75
    assert summary["timeout_count"] == 1
    assert summary["forbidden_domain_leaks"] == 1
    assert summary["forbidden_tenant_leaks"] == 1
    assert summary["forbidden_node_leaks"] == 1
    assert summary["acl_violations"] == 1
    assert summary["classification_violations"] == 1
    assert summary["response_without_evidence_count"] == 1
    failures = read_jsonl(tmp_path / "report" / "failures.jsonl")
    reasons = {reason for failure in failures for reason in failure["reasons"]}
    assert {
        "incomplete_seed_recall",
        "security_leak",
        "missing_retrieval_trace",
        "request_error:timeout",
        "response_without_evidence",
        "incomplete_citation_coverage",
    }.issubset(reasons)


def test_threshold_failures_and_unavailable_metrics(
    tmp_path: Path,
    evaluation_dataset: dict,
) -> None:
    for result in evaluation_dataset["results"]:
        result["retrieval_trace"] = None
    write_jsonl(tmp_path / "results.jsonl", evaluation_dataset["results"])
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(
        json.dumps(
            {
                "minimum_seed_recall": 0.9,
                "maximum_p95_latency_ms": 1,
            }
        ),
        encoding="utf-8",
    )

    outcome = analyze_results(analysis_config(tmp_path, evaluation_dataset, thresholds=thresholds))

    assert outcome.threshold_passed is False
    checks = {check["threshold"]: check for check in outcome.report["thresholds"]["checks"]}
    assert checks["minimum_seed_recall"]["reason"] == "metric unavailable"
    assert "does not satisfy maximum" in checks["maximum_p95_latency_ms"]["reason"]


@pytest.mark.parametrize(
    ("thresholds", "message"),
    [
        ({"unknown": 1}, "Unknown threshold"),
        ({"minimum_seed_recall": True}, "must be a number"),
    ],
)
def test_threshold_contract_errors(thresholds: dict, message: str) -> None:
    with pytest.raises(ContractError, match=message):
        evaluate_thresholds(thresholds, {"seed_recall": 1})


def test_analysis_output_conflict_and_optional_files(
    tmp_path: Path,
    evaluation_dataset: dict,
) -> None:
    config = analysis_config(tmp_path, evaluation_dataset, write_csv=False)
    first = analyze_results(config)
    assert first.report["thresholds"] is None
    assert first.report["dataset"] is not None
    assert not (tmp_path / "report" / "metrics.csv").exists()
    with pytest.raises(OutputConflictError):
        analyze_results(config)
    overwritten = analyze_results(
        AnalysisConfig(**{**config.__dict__, "overwrite": True, "manifest_path": None})
    )
    assert overwritten.report["dataset"] is None


def test_analysis_validates_configuration_and_result_sets(
    tmp_path: Path,
    evaluation_dataset: dict,
) -> None:
    config = analysis_config(tmp_path, evaluation_dataset)
    with pytest.raises(ContractError, match="recall_at_k"):
        analyze_results(AnalysisConfig(**{**config.__dict__, "recall_at_k": 0}))

    write_jsonl(tmp_path / "results.jsonl", evaluation_dataset["results"][:-1])
    with pytest.raises(ContractError, match="Missing results"):
        analyze_results(config)

    extra = {
        **evaluation_dataset["results"][0],
        "query_id": "unknown",
    }
    write_jsonl(tmp_path / "results.jsonl", [*evaluation_dataset["results"], extra])
    with pytest.raises(ContractError, match="Unknown result"):
        analyze_results(config)

    write_jsonl(
        tmp_path / "results.jsonl",
        [evaluation_dataset["results"][0], evaluation_dataset["results"][0]],
    )
    with pytest.raises(ContractError, match="Duplicate result"):
        analyze_results(config)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda item: item.update(ok="yes"), r"\.ok"),
        (lambda item: item.update(duration_ms=-1), "duration_ms"),
        (lambda item: item.update(citations="bad"), "citations"),
        (lambda item: item.update(pipeline_trace="bad"), "pipeline_trace"),
        (
            lambda item: item.update(
                retrieval_trace={
                    "seed_nodes": "bad",
                    "graph_nodes": [],
                    "relationship_types": [],
                }
            ),
            "seed_nodes",
        ),
        (
            lambda item: item.update(
                retrieval_trace={
                    "seed_nodes": [],
                    "graph_nodes": "bad",
                    "relationship_types": [],
                }
            ),
            "graph_nodes",
        ),
        (
            lambda item: item.update(
                retrieval_trace={
                    "seed_nodes": [],
                    "graph_nodes": [],
                    "relationship_types": [1],
                }
            ),
            "relationship_types",
        ),
        (lambda item: item.update(error=[]), r"\.error"),
    ],
)
def test_analysis_rejects_invalid_result_contracts(
    tmp_path: Path,
    evaluation_dataset: dict,
    mutate,
    message: str,
) -> None:
    mutate(evaluation_dataset["results"][0])
    write_jsonl(tmp_path / "results.jsonl", evaluation_dataset["results"])

    with pytest.raises(ContractError, match=message):
        analyze_results(analysis_config(tmp_path, evaluation_dataset))


def test_analysis_rejects_invalid_query_and_ground_truth_contracts(
    tmp_path: Path,
    evaluation_dataset: dict,
) -> None:
    queries_path = evaluation_dataset["directory"] / "queries.jsonl"
    original_queries = evaluation_dataset["queries"]
    write_jsonl(queries_path, [])
    write_jsonl(tmp_path / "results.jsonl", evaluation_dataset["results"])
    with pytest.raises(ContractError, match="cannot be empty"):
        analyze_results(analysis_config(tmp_path, evaluation_dataset))

    duplicate_queries = [original_queries[0], original_queries[0]]
    write_jsonl(queries_path, duplicate_queries)
    with pytest.raises(ContractError, match="Duplicate query"):
        analyze_results(analysis_config(tmp_path, evaluation_dataset))

    invalid_query = dict(original_queries[0])
    invalid_query["expected_seed_nodes"] = "bad"
    write_jsonl(queries_path, [invalid_query])
    write_jsonl(tmp_path / "results.jsonl", [evaluation_dataset["results"][0]])
    with pytest.raises(ContractError, match="expected_seed_nodes"):
        analyze_results(analysis_config(tmp_path, evaluation_dataset))

    write_jsonl(queries_path, original_queries)
    write_jsonl(tmp_path / "results.jsonl", evaluation_dataset["results"])
    ground_truth_path = evaluation_dataset["directory"] / "ground_truth.jsonl"
    ground_truth = read_jsonl(ground_truth_path)
    required_id = original_queries[0]["expected_seed_nodes"][0]
    duplicate = next(item for item in ground_truth if item["chunk_id"] == required_id)
    ground_truth.append(duplicate)
    write_jsonl(ground_truth_path, ground_truth)
    with pytest.raises(ContractError, match="Duplicate ground-truth"):
        analyze_results(analysis_config(tmp_path, evaluation_dataset))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_classification_level", True),
        ("graph_depth", "three"),
    ],
)
def test_analysis_rejects_invalid_query_numbers(
    tmp_path: Path,
    evaluation_dataset: dict,
    field: str,
    value,
) -> None:
    query = dict(evaluation_dataset["queries"][0])
    query[field] = value
    write_jsonl(evaluation_dataset["directory"] / "queries.jsonl", [query])
    write_jsonl(tmp_path / "results.jsonl", [evaluation_dataset["results"][0]])
    with pytest.raises(ContractError, match=field):
        analyze_results(analysis_config(tmp_path, evaluation_dataset))


def test_analysis_reports_incomplete_graph_recall(
    tmp_path: Path,
    evaluation_dataset: dict,
) -> None:
    evaluation_dataset["results"][0]["retrieval_trace"]["graph_nodes"] = []
    write_jsonl(tmp_path / "results.jsonl", evaluation_dataset["results"])

    analyze_results(analysis_config(tmp_path, evaluation_dataset))

    failures = read_jsonl(tmp_path / "report" / "failures.jsonl")
    assert "incomplete_graph_recall" in failures[0]["reasons"]
