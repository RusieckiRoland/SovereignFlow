from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dataset_generator.generation import generate_dataset
from dataset_generator.models import GeneratorConfig


@pytest.fixture
def config(tmp_path: Path) -> GeneratorConfig:
    return GeneratorConfig(
        output_directory=tmp_path / "generated",
        nodes=43,
        domains=2,
        seed=123,
        queries=16,
        progress_every=7,
        tenants=1,
        max_edges_per_node=6,
        versions=2,
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def evaluation_dataset(tmp_path: Path) -> dict[str, Any]:
    output = tmp_path / "evaluation-dataset"
    evaluation_config = GeneratorConfig(
        output_directory=output,
        nodes=80,
        domains=4,
        seed=123,
        queries=8,
        progress_every=100,
        tenants=2,
        max_edges_per_node=6,
        versions=2,
    )
    generate_dataset(evaluation_config)
    queries = read_jsonl(output / "queries.jsonl")
    nodes = read_jsonl(output / "nodes.jsonl")
    nodes_by_id = {node["chunk_id"]: node for node in nodes}
    return {
        "directory": output,
        "queries": queries,
        "nodes_by_id": nodes_by_id,
        "results": [
            perfect_result(query, nodes_by_id, index)
            for index, query in enumerate(queries, start=1)
        ],
    }


def perfect_result(
    query: dict[str, Any],
    nodes_by_id: dict[str, dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    seed_nodes = [
        _evidence(nodes_by_id[node_id], rank)
        for rank, node_id in enumerate(
            query["expected_seed_nodes"],
            start=1,
        )
    ]
    graph_nodes = [
        _evidence(nodes_by_id[node_id], rank)
        for rank, node_id in enumerate(
            query["expected_graph_nodes"],
            start=1,
        )
    ]
    citations = []
    for source_id in query["expected_source_ids"]:
        node = next(
            item
            for item in nodes_by_id.values()
            if item["source_id"] == source_id and item["source_version"] == query["source_version"]
        )
        citations.append(_evidence(node, len(citations) + 1))
    return {
        "query_id": query["query_id"],
        "request_id": f"request-{index}",
        "status_code": 200,
        "duration_ms": float(index * 10),
        "ok": True,
        "answer": "Grounded synthetic answer.",
        "citations": citations,
        "pipeline_trace": [{"step": "retrieve"}],
        "retrieval_trace": {
            "seed_nodes": seed_nodes,
            "graph_nodes": graph_nodes,
            "relationship_types": query["expected_relationship_types"],
        },
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.001,
        },
        "error": None,
    }


def _evidence(node: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "chunk_id": node["chunk_id"],
        "source_id": node["source_id"],
        "domain": node["domain"],
        "tenant_id": node["tenant_id"],
        "acl_labels": node["acl_labels"],
        "classification_level": node["classification_level"],
        "rank": rank,
    }
