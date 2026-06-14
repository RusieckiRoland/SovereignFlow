from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest
from conftest import read_jsonl

from dataset_generator.cli import build_parser, main


def arguments(output: Path) -> list[str]:
    return [
        "--out",
        str(output),
        "--nodes",
        "20",
        "--domains",
        "2",
        "--seed",
        "123",
        "--queries",
        "8",
        "--tenants",
        "1",
        "--versions",
        "1",
        "--max-edges-per-node",
        "6",
        "--progress-every",
        "10",
    ]


@pytest.mark.integration
def test_cli_success_and_controlled_failure(tmp_path: Path, caplog) -> None:
    output = tmp_path / "generated"
    caplog.set_level("INFO", logger="dataset_generator")

    assert main(arguments(output)) == 0
    assert len(read_jsonl(output / "nodes.jsonl")) == 20
    assert "Dataset complete" in caplog.text
    assert main(arguments(output)) == 2
    assert "Use --overwrite" in caplog.text
    assert main([*arguments(output), "--overwrite"]) == 0


def test_parser_contract_and_argument_errors(tmp_path: Path) -> None:
    parser = build_parser()
    parsed = parser.parse_args(arguments(tmp_path / "generated"))
    assert parsed.seed == 123
    assert parsed.overwrite is False
    assert parsed.tenants == 1
    assert parsed.versions == 1

    with pytest.raises(SystemExit) as error:
        parser.parse_args([])
    assert error.value.code == 2


@pytest.mark.integration
def test_module_entrypoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["dataset_generator", *arguments(tmp_path / "generated")],
    )

    with pytest.raises(SystemExit) as error:
        runpy.run_module("dataset_generator", run_name="__main__")

    assert error.value.code == 0
