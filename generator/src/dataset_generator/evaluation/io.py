from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from .contracts import ContractError, OutputConflictError


def read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        stream = path.open(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"Cannot open JSONL file: {path}") from exc
    with stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ContractError(f"{path}:{line_number} contains an empty line")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"{path}:{line_number} contains invalid JSON") from exc
            if not isinstance(value, dict):
                raise ContractError(f"{path}:{line_number} must contain a JSON object")
            yield line_number, value


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"Cannot open JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path} contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path} must contain a JSON object")
    return value


def write_jsonl_atomic(
    path: Path,
    records: Iterable[Mapping[str, Any]],
    *,
    overwrite: bool,
) -> None:
    _prepare_file(path, overwrite)
    temporary = _temporary_path(path)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(_compact_json(record) + "\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def publish_reports(
    output_directory: Path,
    files: Mapping[str, str],
    *,
    overwrite: bool,
) -> None:
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    if output_directory.exists() and not output_directory.is_dir():
        raise ContractError("Evaluation output must be a directory")
    conflicts = [name for name in files if (output_directory / name).exists()]
    if conflicts and not overwrite:
        raise OutputConflictError(f"Evaluation files already exist: {', '.join(sorted(conflicts))}")
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}-evaluation-",
            dir=output_directory.parent,
        )
    )
    try:
        for name, content in files.items():
            (staging / name).write_text(content, encoding="utf-8", newline="\n")
        output_directory.mkdir(parents=True, exist_ok=True)
        for name in files:
            os.replace(staging / name, output_directory / name)
    finally:
        staging.rmdir()


def json_text(payload: Mapping[str, Any]) -> str:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def jsonl_text(records: Iterable[Mapping[str, Any]]) -> str:
    return "".join(_compact_json(record) + "\n" for record in records)


def _prepare_file(path: Path, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise OutputConflictError(f"Evaluation file already exists: {path}")
    if path.exists() and not path.is_file():
        raise ContractError(f"Evaluation output is not a file: {path}")


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def _compact_json(record: Mapping[str, Any]) -> str:
    return json.dumps(
        record,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
