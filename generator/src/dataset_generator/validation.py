from __future__ import annotations

from pathlib import Path

from .identifiers import NODE_TYPES
from .models import ConfigurationError, GeneratorConfig, OutputConflictError

OUTPUT_FILES = (
    "nodes.jsonl",
    "edges.jsonl",
    "queries.jsonl",
    "ground_truth.jsonl",
    "operations.jsonl",
    "manifest.json",
)


def validate_config(config: GeneratorConfig) -> None:
    _positive(config.nodes, "nodes")
    _positive(config.domains, "domains")
    _positive(config.queries, "queries")
    _positive(config.progress_every, "progress_every")
    _positive(config.tenants, "tenants")
    _positive(config.max_edges_per_node, "max_edges_per_node")
    _positive(config.versions, "versions")
    if config.seed < 0:
        raise ConfigurationError("seed cannot be negative")
    if config.tenants > config.domains:
        raise ConfigurationError("tenants cannot exceed domains")
    if config.max_edges_per_node < 5:
        raise ConfigurationError(
            "max_edges_per_node must be at least 5 for the baseline service graph"
        )
    minimum_nodes = config.domains * config.versions * len(NODE_TYPES)
    if config.nodes < minimum_nodes:
        raise ConfigurationError(
            f"nodes must be at least {minimum_nodes} to create one complete system per domain"
        )
    if config.output_directory.exists() and not config.output_directory.is_dir():
        raise ConfigurationError("out must refer to a directory")


def prepare_output(config: GeneratorConfig) -> dict[str, Path]:
    validate_config(config)
    try:
        config.output_directory.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError("out parent directory cannot be created") from exc
    paths = {file_name: config.output_directory / file_name for file_name in OUTPUT_FILES}
    conflicts = [path.name for path in paths.values() if path.exists()]
    if conflicts and not config.overwrite:
        joined = ", ".join(sorted(conflicts))
        raise OutputConflictError(
            f"Output files already exist: {joined}. Use --overwrite to replace them."
        )
    return paths


def _positive(value: int, field_name: str) -> None:
    if value < 1:
        raise ConfigurationError(f"{field_name} must be greater than zero")
