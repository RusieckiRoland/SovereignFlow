from __future__ import annotations

from pathlib import Path

import pytest

from dataset_generator.evaluation.contracts import (
    ContractError,
    optional_number,
    optional_string,
    require_mapping,
    require_string,
    require_string_list,
)
from dataset_generator.evaluation.io import (
    json_text,
    jsonl_text,
    publish_reports,
    read_json,
    read_jsonl,
    write_jsonl_atomic,
)
from dataset_generator.evaluation.metrics import (
    latency_metrics,
    mean,
    percentile,
    precision,
    recall,
    reciprocal_rank,
)
from dataset_generator.evaluation.reports import markdown_report


def test_metric_helpers_cover_empty_ranked_and_interpolated_values() -> None:
    assert recall(set(), []) == 1
    assert recall({"a", "b"}, ["a"]) == 0.5
    assert precision(set(), []) == 1
    assert precision({"a"}, []) == 0
    assert precision({"a"}, ["a", "b"]) == 0.5
    assert reciprocal_rank({"b"}, ["a", "b"]) == 0.5
    assert reciprocal_rank({"c"}, ["a", "b"]) == 0
    assert mean([]) is None
    assert mean([1, 2]) == 1.5
    assert percentile([], 0.95) is None
    assert percentile([4], 0.95) == 4
    assert percentile([0, 10], 0.5) == 5
    assert latency_metrics([]) == {
        "mean_latency_ms": None,
        "median_latency_ms": None,
        "p90_latency_ms": None,
        "p95_latency_ms": None,
        "p99_latency_ms": None,
    }


def test_contract_helpers_accept_and_reject_values() -> None:
    mapping = {
        "text": "value",
        "items": ["a"],
        "nullable": None,
        "number": 2,
    }
    assert require_mapping(mapping, "value") is mapping
    assert require_string(mapping, "text", "value") == "value"
    assert require_string_list(mapping, "items", "value") == ["a"]
    assert optional_string(mapping, "nullable", "value") is None
    assert optional_number(mapping, "number", "value") == 2.0
    assert optional_number(mapping, "nullable", "value") is None

    for call in (
        lambda: require_mapping([], "value"),
        lambda: require_string({"text": ""}, "text", "value"),
        lambda: require_string_list({"items": [1]}, "items", "value"),
        lambda: optional_string({"text": 1}, "text", "value"),
        lambda: optional_number({"number": True}, "number", "value"),
    ):
        with pytest.raises(ContractError):
            call()


def test_json_io_contracts_and_atomic_output(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    write_jsonl_atomic(source, [{"b": 2, "a": 1}], overwrite=False)
    assert source.read_text(encoding="utf-8") == '{"a":1,"b":2}\n'
    assert list(read_jsonl(source)) == [(1, {"a": 1, "b": 2})]
    with pytest.raises(Exception, match="already exists"):
        write_jsonl_atomic(source, [], overwrite=False)
    write_jsonl_atomic(source, [{"a": 3}], overwrite=True)
    assert list(read_jsonl(source))[0][1]["a"] == 3

    json_path = tmp_path / "value.json"
    json_path.write_text('{"a": 1}', encoding="utf-8")
    assert read_json(json_path) == {"a": 1}
    assert json_text({"b": 2, "a": 1}).startswith('{\n  "a"')
    assert jsonl_text([{"b": 2, "a": 1}]) == '{"a":1,"b":2}\n'


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("\n", "empty line"),
        ("not-json\n", "invalid JSON"),
        ("[]\n", "JSON object"),
    ],
)
def test_read_jsonl_rejects_invalid_content(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    path = tmp_path / "invalid.jsonl"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ContractError, match=message):
        list(read_jsonl(path))


def test_json_read_errors_and_report_publication(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ContractError, match="Cannot open"):
        read_json(missing)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid JSON"):
        read_json(invalid)
    invalid.write_text("[]", encoding="utf-8")
    with pytest.raises(ContractError, match="JSON object"):
        read_json(invalid)
    with pytest.raises(ContractError, match="Cannot open JSONL"):
        list(read_jsonl(tmp_path / "missing.jsonl"))

    output = tmp_path / "reports"
    publish_reports(output, {"report.json": "{}\n"}, overwrite=False)
    assert (output / "report.json").read_text(encoding="utf-8") == "{}\n"
    publish_reports(output, {"report.json": '{"updated":true}\n'}, overwrite=True)
    assert "updated" in (output / "report.json").read_text(encoding="utf-8")
    file_output = tmp_path / "file-output"
    file_output.write_text("file", encoding="utf-8")
    with pytest.raises(ContractError, match="directory"):
        publish_reports(file_output, {"report.json": "{}"}, overwrite=False)


def test_markdown_report_without_thresholds_and_unavailable_values() -> None:
    report = {
        "summary": {
            "query_count": 1,
            "successful_queries": 1,
            "failed_queries": 0,
            "retrieval_trace_coverage": 0,
            "seed_recall": None,
            "graph_recall": None,
            "seed_concept_recall": None,
            "graph_concept_recall": None,
            "citation_coverage": 1.0,
            "forbidden_leaks": 0,
            "p95_latency_ms": None,
        },
        "thresholds": None,
        "groups": {
            "query_type": {
                "easy": {
                    "query_count": 1,
                    "seed_recall": None,
                    "graph_recall": None,
                    "forbidden_leaks": 0,
                    "p95_latency_ms": None,
                }
            }
        },
    }
    rendered = markdown_report(report)
    assert "No acceptance thresholds" in rendered
    assert "N/A" in rendered
