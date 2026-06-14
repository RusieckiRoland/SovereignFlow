from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class EvaluationError(Exception):
    """Base error for controlled evaluation failures."""


class ContractError(EvaluationError):
    """Raised when an evaluation input violates its contract."""


class OutputConflictError(EvaluationError):
    """Raised when evaluation output would be overwritten."""


@dataclass(frozen=True)
class ExecutionConfig:
    queries_path: Path
    output_path: Path
    endpoint: str
    timeout_seconds: float
    overwrite: bool = False
    diagnostic_key: str | None = None


@dataclass(frozen=True)
class AnalysisConfig:
    queries_path: Path
    results_path: Path
    ground_truth_path: Path
    output_directory: Path
    manifest_path: Path | None = None
    thresholds_path: Path | None = None
    recall_at_k: int = 10
    overwrite: bool = False
    write_csv: bool = False


def require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{context} must be a JSON object")
    return value


def require_string(mapping: dict[str, Any], field: str, context: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ContractError(f"{context}.{field} must be a non-empty string")
    return value


def require_string_list(mapping: dict[str, Any], field: str, context: str) -> list[str]:
    value = mapping.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(f"{context}.{field} must be a list of strings")
    return value


def optional_string(mapping: dict[str, Any], field: str, context: str) -> str | None:
    value = mapping.get(field)
    if value is not None and not isinstance(value, str):
        raise ContractError(f"{context}.{field} must be a string or null")
    return value


def optional_number(mapping: dict[str, Any], field: str, context: str) -> float | None:
    value = mapping.get(field)
    if value is not None and (not isinstance(value, int | float) or isinstance(value, bool)):
        raise ContractError(f"{context}.{field} must be a number or null")
    return None if value is None else float(value)
