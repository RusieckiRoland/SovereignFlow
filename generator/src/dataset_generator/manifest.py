from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import FileStatistics, GeneratorConfig

SCHEMA_VERSION = "2.0"
GENERATOR_VERSION = "0.2.0"


def build_manifest(
    config: GeneratorConfig,
    *,
    files: Mapping[str, FileStatistics],
    distributions: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "seed": config.seed,
        "configuration": {
            "nodes": config.nodes,
            "domains": config.domains,
            "tenants": config.tenants,
            "queries": config.queries,
            "versions": config.versions,
            "max_edges_per_node": config.max_edges_per_node,
        },
        "files": {
            name: {
                "records": stats.records,
                "sha256": stats.sha256,
                "bytes": stats.bytes,
            }
            for name, stats in sorted(files.items())
        },
        "distributions": {
            name: dict(sorted(values.items())) for name, values in sorted(distributions.items())
        },
    }
