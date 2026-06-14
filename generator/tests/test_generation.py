from __future__ import annotations

import hashlib
import json
import logging
import random
import tracemalloc
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import read_jsonl

from dataset_generator import generation
from dataset_generator.generation import (
    DistributionTracker,
    _cross_domain_target,
    _expected_state,
    _forbidden_domains,
    _forbidden_tenants,
    edge_record,
    edge_records,
    generate_dataset,
    ground_truth_records,
    node_addresses,
    node_record,
    node_records,
    node_type_counts,
    nodes_in_domain,
    nodes_in_slot,
    operation_records,
    query_records,
    security_values,
)
from dataset_generator.models import (
    GeneratorConfig,
    NodeAddress,
    PublicationError,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generation_writes_complete_versioned_dataset(config, caplog) -> None:
    caplog.set_level(logging.INFO, logger="dataset_generator")
    ticks = iter((0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0))

    summary = generate_dataset(config, clock=lambda: next(ticks))

    nodes = read_jsonl(config.output_directory / "nodes.jsonl")
    edges = read_jsonl(config.output_directory / "edges.jsonl")
    queries = read_jsonl(config.output_directory / "queries.jsonl")
    ground_truth = read_jsonl(config.output_directory / "ground_truth.jsonl")
    operations = read_jsonl(config.output_directory / "operations.jsonl")
    manifest = json.loads((config.output_directory / "manifest.json").read_text(encoding="utf-8"))

    assert summary.nodes == len(nodes) == 43
    assert summary.edges == len(edges) == 48
    assert summary.queries == len(queries) == 16
    assert summary.ground_truth == len(ground_truth) == 43
    assert summary.operations == len(operations) == 42
    assert manifest["schema_version"] == "2.0"
    assert manifest["configuration"]["versions"] == 2
    assert "Generated 43/43 nodes" in caplog.text
    assert set(nodes[0]) == {
        "chunk_id",
        "domain",
        "tenant_id",
        "source_id",
        "source_version",
        "source_uri",
        "text",
        "metadata",
        "acl_labels",
        "classification_level",
        "token_estimate",
    }
    assert "concept_ids" not in nodes[0]


def test_manifest_counts_checksums_and_distributions(config) -> None:
    generate_dataset(config)
    manifest = json.loads((config.output_directory / "manifest.json").read_text(encoding="utf-8"))

    for file_name in (
        "nodes.jsonl",
        "edges.jsonl",
        "queries.jsonl",
        "ground_truth.jsonl",
        "operations.jsonl",
    ):
        path = config.output_directory / file_name
        assert manifest["files"][file_name]["sha256"] == sha256(path)
        assert manifest["files"][file_name]["bytes"] == path.stat().st_size
        assert manifest["files"][file_name]["records"] == len(read_jsonl(path))

    assert sum(manifest["distributions"]["node_types"].values()) == config.nodes
    assert sum(manifest["distributions"]["tenants"].values()) == config.nodes
    assert sum(manifest["distributions"]["query_types"].values()) == config.queries
    assert manifest["distributions"]["operations"] == {
        "add_source": 20,
        "delete_source": 2,
        "replace_source": 20,
    }


def test_determinism_seed_and_query_stream_isolation(config, tmp_path: Path) -> None:
    first = config
    second = replace(config, output_directory=tmp_path / "second")
    changed_queries = replace(
        config,
        output_directory=tmp_path / "changed-queries",
        queries=8,
    )
    changed_seed = replace(
        config,
        output_directory=tmp_path / "changed-seed",
        seed=124,
    )

    for item in (first, second, changed_queries, changed_seed):
        generate_dataset(item)

    for file_name in (
        "nodes.jsonl",
        "edges.jsonl",
        "ground_truth.jsonl",
        "operations.jsonl",
    ):
        assert (first.output_directory / file_name).read_bytes() == (
            second.output_directory / file_name
        ).read_bytes()
        assert (first.output_directory / file_name).read_bytes() == (
            changed_queries.output_directory / file_name
        ).read_bytes()
    assert (first.output_directory / "queries.jsonl").read_bytes() == (
        second.output_directory / "queries.jsonl"
    ).read_bytes()
    assert (first.output_directory / "nodes.jsonl").read_bytes() != (
        changed_seed.output_directory / "nodes.jsonl"
    ).read_bytes()


def test_edges_respect_references_tenants_duplicates_and_degree(config) -> None:
    generate_dataset(config)
    nodes = read_jsonl(config.output_directory / "nodes.jsonl")
    edges = read_jsonl(config.output_directory / "edges.jsonl")
    node_tenants = {node["chunk_id"]: node["tenant_id"] for node in nodes}
    node_ids = set(node_tenants)
    edge_keys = set()
    outgoing = Counter()
    cross_domain = 0

    for edge in edges:
        assert edge["from_chunk_id"] in node_ids
        assert edge["to_chunk_id"] in node_ids
        assert node_tenants[edge["from_chunk_id"]] == node_tenants[edge["to_chunk_id"]]
        assert edge["tenant_id"] == node_tenants[edge["from_chunk_id"]]
        key = (
            edge["from_chunk_id"],
            edge["to_chunk_id"],
            edge["relationship_type"],
        )
        assert key not in edge_keys
        edge_keys.add(key)
        outgoing[edge["from_chunk_id"]] += 1
        if edge["from_source_id"].split("_")[0] != edge["to_source_id"].split("_")[0]:
            cross_domain += 1

    assert max(outgoing.values()) <= config.max_edges_per_node
    assert cross_domain == config.domains * config.versions
    assert {edge["relationship_type"] for edge in edges} == {
        "calls",
        "writes",
        "reads",
        "configured_by",
        "validates_with",
        "emits",
        "handles",
        "belongs_to",
        "depends_on",
        "similar_to",
    }


def test_security_versions_ground_truth_and_queries_are_consistent(config) -> None:
    generate_dataset(config)
    nodes = read_jsonl(config.output_directory / "nodes.jsonl")
    queries = read_jsonl(config.output_directory / "queries.jsonl")
    ground_truth = read_jsonl(config.output_directory / "ground_truth.jsonl")
    node_by_id = {node["chunk_id"]: node for node in nodes}
    concepts_by_node = {item["chunk_id"]: set(item["concept_ids"]) for item in ground_truth}

    assert {node["source_version"] for node in nodes} == {"v1", "v2"}
    assert {node["acl_labels"][0] for node in nodes} == {
        "public",
        "internal",
        "restricted",
    }
    assert {node["classification_level"] for node in nodes} == {0, 1, 2, 3}
    assert all("concept_ids" not in node for node in nodes)

    query_types = {query["query_type"] for query in queries}
    assert query_types == {
        "easy",
        "confusing",
        "graph",
        "security",
        "control",
        "before_update",
        "after_update",
        "deleted",
    }
    for query in queries:
        assert query["tenant_id"] not in query["forbidden_tenants"]
        assert query["domain"] not in query["forbidden_domains"]
        if query["query_type"] == "deleted":
            assert query["expected_state"] == "deleted"
            assert query["expected_seed_nodes"] == []
            assert query["forbidden_nodes"]
            continue
        expected_nodes = query["expected_graph_nodes"]
        assert set(expected_nodes).issubset(node_by_id)
        assert all(
            node_by_id[node_id]["tenant_id"] == query["tenant_id"] for node_id in expected_nodes
        )
        assert all(
            node_by_id[node_id]["classification_level"] <= query["max_classification_level"]
            for node_id in expected_nodes
        )
        assert all(
            set(node_by_id[node_id]["acl_labels"]).issubset(query["allowed_acl_labels"])
            for node_id in expected_nodes
        )
        found_concepts = set().union(*(concepts_by_node[node_id] for node_id in expected_nodes))
        assert set(query["expected_graph_concept_ids"]).issubset(found_concepts)


def test_operations_cover_add_replace_and_delete(config) -> None:
    operations = tuple(operation_records(config, tracker=DistributionTracker()))

    assert len(operations) == 42
    assert operations[0]["operation"] == "add_source"
    assert operations[1] == {
        "operation_id": "operation_00000002",
        "operation": "replace_source",
        "tenant_id": "tenant_0001",
        "domain": "Orders_000001",
        "source_id": "Orders_000001_Controller",
        "from_version": "v1",
        "to_version": "v2",
        "changes": [
            "text",
            "metadata",
            "acl_labels",
            "classification_level",
        ],
    }
    assert [item["operation"] for item in operations].count("delete_source") == 2
    replace_operations = [item for item in operations if item["operation"] == "replace_source"]
    assert any("relationships" in item["changes"] for item in replace_operations)
    assert any("acl_labels" in item["changes"] for item in replace_operations)


def test_distribution_handles_non_divisible_versioned_node_count(config) -> None:
    assert nodes_in_slot(config, 0, 1) == 11
    assert nodes_in_slot(config, 0, 2) == 11
    assert nodes_in_slot(config, 1, 1) == 11
    assert nodes_in_slot(config, 1, 2) == 10
    assert nodes_in_domain(config, 0) == 22
    assert nodes_in_domain(config, 1) == 21
    assert len(tuple(node_addresses(config))) == 43
    assert node_type_counts(config, 0, 1)["controller"] == 2
    assert node_type_counts(config, 0, 1)["service"] == 1


def test_record_helpers_and_limited_cross_domain_edges(config) -> None:
    address = NodeAddress(0, 2, "service", 1)
    first = node_record(address, config, random.Random(1))
    second = node_record(address, config, random.Random(2))
    assert first["chunk_id"] == second["chunk_id"]
    assert first["source_version"] == "v2"
    assert "source version v2" in first["text"]
    assert first["metadata"] == second["metadata"]
    assert security_values(address, config.seed) == (
        first["acl_labels"][0],
        first["classification_level"],
    )

    without_cross = replace(config, max_edges_per_node=5)
    edges = tuple(
        edge_records(
            without_cross,
            edge_rng=random.Random(1),
            tracker=DistributionTracker(),
        )
    )
    assert not any(edge["relationship_type"] in {"depends_on", "similar_to"} for edge in edges)

    explicit = edge_record(
        domain="Orders_000001",
        tenant="tenant_0001",
        version=1,
        from_type="service",
        to_type="repository",
        instance=1,
        relationship_type="reads",
        weight=1.0,
    )
    assert explicit["to_source_version"] == "v1"


def test_query_helpers_cover_single_domain_tenant_and_states(config) -> None:
    tracker = DistributionTracker()
    queries = tuple(query_records(config, query_rng=random.Random(1), tracker=tracker))
    ground_truth = tuple(ground_truth_records(config, tracker=DistributionTracker()))

    assert len(queries) == config.queries
    assert len(ground_truth) == config.nodes
    assert _forbidden_domains(1, 0, random.Random(1)) == []
    assert _forbidden_tenants(1, "tenant_0001", random.Random(1)) == []
    assert _expected_state("before_update") == "historical"
    assert _expected_state("before_update", 1) == "current"
    assert _expected_state("deleted") == "deleted"
    assert _expected_state("easy") == "current"
    assert _cross_domain_target(0, domain_count=3, tenant_count=2) == 2
    assert _cross_domain_target(1, domain_count=3, tenant_count=2) is None
    assert _cross_domain_target(2, domain_count=3, tenant_count=2) == 0


def test_uneven_tenant_distribution_never_creates_cross_tenant_edges(tmp_path: Path) -> None:
    uneven = GeneratorConfig(
        tmp_path / "uneven",
        nodes=60,
        domains=3,
        seed=1,
        queries=8,
        progress_every=100,
        tenants=2,
        versions=2,
    )
    nodes = {
        record["chunk_id"]: record["tenant_id"]
        for record in node_records(
            uneven,
            node_rng=random.Random(1),
            tracker=DistributionTracker(),
            started_at=0,
            clock=lambda: 1,
        )
    }
    edges = tuple(
        edge_records(
            uneven,
            edge_rng=random.Random(1),
            tracker=DistributionTracker(),
        )
    )

    assert all(nodes[edge["from_chunk_id"]] == nodes[edge["to_chunk_id"]] for edge in edges)


def test_single_version_queries_do_not_claim_historical_state(tmp_path: Path) -> None:
    single = GeneratorConfig(
        tmp_path / "single",
        nodes=10,
        domains=1,
        seed=1,
        queries=8,
        progress_every=100,
    )
    queries = tuple(
        query_records(
            single,
            query_rng=random.Random(1),
            tracker=DistributionTracker(),
        )
    )

    assert not any(query["expected_state"] == "historical" for query in queries)


def test_atomic_staging_cleanup_and_overwrite_preserves_previous_dataset(
    config,
    monkeypatch,
) -> None:
    generate_dataset(config)
    original_manifest = (config.output_directory / "manifest.json").read_bytes()
    real_write = generation.write_jsonl
    calls = 0

    def broken_write(path, records):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced generation failure")
        return real_write(path, records)

    monkeypatch.setattr(generation, "write_jsonl", broken_write)
    with pytest.raises(RuntimeError, match="forced"):
        generate_dataset(replace(config, overwrite=True))

    assert (config.output_directory / "manifest.json").read_bytes() == original_manifest
    assert not list(config.output_directory.parent.glob(".generated-staging-*"))


def test_publication_failure_is_explicit_and_cleans_staging(config, monkeypatch) -> None:
    monkeypatch.setattr(
        generation.os,
        "replace",
        lambda *args: (_ for _ in ()).throw(OSError("denied")),
    )

    with pytest.raises(PublicationError, match="publication"):
        generate_dataset(config)

    assert not (config.output_directory / "manifest.json").exists()
    assert not list(config.output_directory.parent.glob(".generated-staging-*"))


def test_atomic_publication_rolls_back_previous_dataset(config, monkeypatch) -> None:
    generate_dataset(config)
    original_manifest = (config.output_directory / "manifest.json").read_bytes()
    real_replace = generation.os.replace
    calls = 0

    def fail_new_dataset(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("publish failed")
        return real_replace(source, target)

    monkeypatch.setattr(generation.os, "replace", fail_new_dataset)
    with pytest.raises(PublicationError, match="publication"):
        generate_dataset(replace(config, overwrite=True))

    assert (config.output_directory / "manifest.json").read_bytes() == original_manifest
    assert not config.output_directory.with_name(".generated-backup").exists()


def test_publication_reports_failed_rollback(config, monkeypatch) -> None:
    generate_dataset(config)
    real_replace = generation.os.replace
    calls = 0

    def fail_publish_and_rollback(source, target):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("denied")
        return real_replace(source, target)

    monkeypatch.setattr(generation.os, "replace", fail_publish_and_rollback)
    with pytest.raises(PublicationError, match="rollback"):
        generate_dataset(replace(config, overwrite=True))

    backup = config.output_directory.with_name(".generated-backup")
    assert (backup / "manifest.json").exists()
    real_replace(backup, config.output_directory)


def test_publication_rejects_stale_backup_directory(config) -> None:
    backup = config.output_directory.with_name(".generated-backup")
    backup.mkdir()

    with pytest.raises(PublicationError, match="backup"):
        generate_dataset(config)


def test_node_stream_logs_final_record_without_progress_boundary(config, caplog) -> None:
    caplog.set_level(logging.INFO, logger="dataset_generator")
    records = tuple(
        node_records(
            replace(config, progress_every=100),
            node_rng=random.Random(1),
            tracker=DistributionTracker(),
            started_at=1.0,
            clock=lambda: 1.0,
        )
    )

    assert len(records) == config.nodes
    assert "Generated 43/43 nodes" in caplog.text


@pytest.mark.integration
def test_streaming_memory_does_not_scale_with_dataset_size(tmp_path: Path) -> None:
    def peak_for(nodes: int) -> int:
        config = GeneratorConfig(
            tmp_path / str(nodes),
            nodes,
            1,
            1,
            1,
            nodes + 1,
        )
        tracemalloc.start()
        for _ in node_records(
            config,
            node_rng=random.Random(1),
            tracker=DistributionTracker(),
            started_at=0.0,
            clock=lambda: 1.0,
        ):
            pass
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak

    small_peak = peak_for(1_000)
    large_peak = peak_for(50_000)

    assert large_peak < small_peak * 4
