from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from dataset_generator.evaluation import cli
from dataset_generator.evaluation.analyzer import AnalysisOutcome
from dataset_generator.evaluation.contracts import EvaluationError


def test_evaluation_cli_run_and_analyze(monkeypatch, tmp_path: Path, caplog) -> None:
    seen = {}
    monkeypatch.setenv("DIAGNOSTIC_KEY", "secret")

    def fake_execute(config):
        seen["execution"] = config
        return 3

    def fake_analyze(config):
        seen["analysis"] = config
        return AnalysisOutcome(report={}, threshold_passed=True)

    monkeypatch.setattr(cli, "execute_queries", fake_execute)
    monkeypatch.setattr(cli, "analyze_results", fake_analyze)
    caplog.set_level("INFO", logger="dataset_generator.evaluation")

    assert (
        cli.main(
            [
                "run",
                "--queries",
                str(tmp_path / "queries.jsonl"),
                "--results",
                str(tmp_path / "results.jsonl"),
                "--endpoint",
                "http://localhost/v1/query",
                "--diagnostic-key-env",
                "DIAGNOSTIC_KEY",
                "--overwrite",
            ]
        )
        == 0
    )
    assert seen["execution"].diagnostic_key == "secret"
    assert seen["execution"].overwrite is True
    assert "Executed 3 queries" in caplog.text

    assert (
        cli.main(
            [
                "analyze",
                "--queries",
                str(tmp_path / "queries.jsonl"),
                "--results",
                str(tmp_path / "results.jsonl"),
                "--ground-truth",
                str(tmp_path / "ground_truth.jsonl"),
                "--out",
                str(tmp_path / "report"),
                "--manifest",
                str(tmp_path / "manifest.json"),
                "--thresholds",
                str(tmp_path / "thresholds.json"),
                "--recall-at-k",
                "5",
                "--metrics-csv",
                "--overwrite",
            ]
        )
        == 0
    )
    assert seen["analysis"].recall_at_k == 5
    assert seen["analysis"].write_csv is True
    assert cli._diagnostic_key(None) is None


def test_evaluation_cli_failure_codes(monkeypatch, tmp_path: Path, caplog) -> None:
    monkeypatch.setattr(
        cli,
        "analyze_results",
        lambda config: AnalysisOutcome(report={}, threshold_passed=False),
    )
    arguments = [
        "analyze",
        "--queries",
        str(tmp_path / "queries.jsonl"),
        "--results",
        str(tmp_path / "results.jsonl"),
        "--ground-truth",
        str(tmp_path / "ground_truth.jsonl"),
        "--out",
        str(tmp_path / "report"),
    ]
    assert cli.main(arguments) == 3
    assert "thresholds failed" in caplog.text

    monkeypatch.setattr(
        cli,
        "analyze_results",
        lambda config: (_ for _ in ()).throw(EvaluationError("controlled")),
    )
    assert cli.main(arguments) == 2
    assert "controlled" in caplog.text

    assert (
        cli.main(
            [
                "run",
                "--queries",
                "queries.jsonl",
                "--results",
                "results.jsonl",
                "--endpoint",
                "http://localhost",
                "--diagnostic-key-env",
                "MISSING_KEY",
            ]
        )
        == 2
    )


def test_evaluation_cli_parser_and_module_entrypoint(monkeypatch) -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as error:
        parser.parse_args([])
    assert error.value.code == 2

    monkeypatch.setattr(cli, "main", lambda argv=None: 7)
    monkeypatch.setattr(sys, "argv", ["dataset_generator.evaluation"])
    with pytest.raises(SystemExit) as module_error:
        runpy.run_module("dataset_generator.evaluation", run_name="__main__")
    assert module_error.value.code == 7
