from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class GeneratorError(Exception):
    """Base error for controlled generator failures."""


class ConfigurationError(GeneratorError):
    """Raised when generator configuration is invalid."""


class OutputConflictError(GeneratorError):
    """Raised when output files would be overwritten without permission."""


class PublicationError(GeneratorError):
    """Raised when a complete dataset cannot be published."""


@dataclass(frozen=True)
class GeneratorConfig:
    output_directory: Path
    nodes: int
    domains: int
    seed: int
    queries: int
    progress_every: int
    overwrite: bool = False
    tenants: int = 1
    max_edges_per_node: int = 6
    versions: int = 1


@dataclass(frozen=True)
class NodeAddress:
    domain_index: int
    version: int
    node_type: str
    instance: int


@dataclass(frozen=True)
class FileStatistics:
    records: int
    sha256: str
    bytes: int
