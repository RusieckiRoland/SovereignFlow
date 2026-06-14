from __future__ import annotations

import logging
import os
import random
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .identifiers import (
    NODE_TYPES,
    chunk_id,
    cluster_name,
    domain_name,
    source_id,
    tenant_id,
    type_label,
    version_id,
)
from .manifest import build_manifest
from .models import FileStatistics, GeneratorConfig, NodeAddress, PublicationError
from .validation import prepare_output
from .writers import write_json, write_jsonl

LOGGER = logging.getLogger("dataset_generator")

EDGE_TEMPLATES = (
    ("controller", "service", "calls"),
    ("service", "repository", "calls"),
    ("repository", "table", "writes"),
    ("repository", "table", "reads"),
    ("service", "log_table", "writes"),
    ("service", "config", "configured_by"),
    ("service", "validator", "validates_with"),
    ("service", "event", "emits"),
    ("worker", "service", "handles"),
    ("command", "service", "handles"),
    ("event", "service", "belongs_to"),
)

TEXT_VARIANTS = {
    "controller": (
        "{title} accepts {cluster} requests and delegates processing to the service.",
        "{title} routes incoming {cluster} operations to the application service.",
    ),
    "service": (
        "{title} validates {cluster} data, applies business rules, and uses the repository.",
        "{title} coordinates {cluster} processing, persistence, validation, and events.",
    ),
    "repository": (
        "{title} stores and reads {cluster} records using the domain table.",
        "{title} provides persistence operations for {cluster} data.",
    ),
    "table": (
        "{title} contains the durable relational records for {cluster}.",
        "{title} stores the current persisted state of {cluster} entities.",
    ),
    "log_table": (
        "{title} records audit and processing history for {cluster}.",
        "{title} stores operational logs produced by {cluster} processing.",
    ),
    "config": (
        "{title} defines runtime settings used by the {cluster} service.",
        "{title} provides configuration values for {cluster} processing.",
    ),
    "worker": (
        "{title} executes background {cluster} work through the service.",
        "{title} schedules asynchronous processing for {cluster} operations.",
    ),
    "event": (
        "{title} represents a completed {cluster} processing event.",
        "{title} notifies consumers about changes in {cluster} data.",
    ),
    "command": (
        "{title} requests a validated {cluster} operation from the service.",
        "{title} carries input for a {cluster} business operation.",
    ),
    "validator": (
        "{title} checks business constraints for {cluster} data.",
        "{title} validates required fields and rules for {cluster} operations.",
    ),
}

KEYWORDS = {
    "controller": ("request", "routing", "controller"),
    "service": ("processing", "validation", "business_rules", "repository"),
    "repository": ("storage", "persistence", "reads", "writes"),
    "table": ("storage", "records", "database"),
    "log_table": ("audit", "logs", "history"),
    "config": ("configuration", "settings", "runtime"),
    "worker": ("background", "worker", "scheduling"),
    "event": ("event", "notification", "change"),
    "command": ("command", "input", "operation"),
    "validator": ("validation", "constraints", "rules"),
}

CONCEPT_SUFFIXES = {
    "controller": ("request-routing",),
    "service": ("processing", "validation", "storage"),
    "repository": ("storage", "persistence"),
    "table": ("storage", "persistence"),
    "log_table": ("audit-history",),
    "config": ("configuration",),
    "worker": ("background-processing",),
    "event": ("event-notification",),
    "command": ("command-handling",),
    "validator": ("validation",),
}

ACL_LABELS = ("public", "internal", "restricted")
QUERY_TYPES = (
    "easy",
    "confusing",
    "graph",
    "security",
    "control",
    "before_update",
    "after_update",
    "deleted",
)
QUERY_VARIANTS = {
    "easy": "How are {domain} records stored?",
    "confusing": "Find the storage flow for {domain}, not similarly named domains.",
    "graph": "Trace {domain} from request handling to persistent storage.",
    "security": "Return permitted storage evidence for {domain}.",
    "control": "Which components belong only to the {domain} cluster?",
    "before_update": "How did {domain} store records before its latest update?",
    "after_update": "How does the current {domain} version store records?",
    "deleted": "Is the removed configuration source still available for {domain}?",
}


@dataclass(frozen=True)
class GenerationSummary:
    nodes: int
    edges: int
    queries: int
    ground_truth: int
    operations: int


class DistributionTracker:
    def __init__(self) -> None:
        self.node_types: Counter[str] = Counter()
        self.relationship_types: Counter[str] = Counter()
        self.tenants: Counter[str] = Counter()
        self.acl_labels: Counter[str] = Counter()
        self.classification_levels: Counter[str] = Counter()
        self.concepts: Counter[str] = Counter()
        self.query_types: Counter[str] = Counter()
        self.operations: Counter[str] = Counter()

    def as_mapping(self) -> dict[str, Mapping[str, int]]:
        return {
            "node_types": self.node_types,
            "relationship_types": self.relationship_types,
            "tenants": self.tenants,
            "acl_labels": self.acl_labels,
            "classification_levels": self.classification_levels,
            "concepts": self.concepts,
            "query_types": self.query_types,
            "operations": self.operations,
        }


def generate_dataset(
    config: GeneratorConfig,
    *,
    clock=time.monotonic,
) -> GenerationSummary:
    prepare_output(config)
    staging_directory = Path(
        tempfile.mkdtemp(
            prefix=f".{config.output_directory.name}-staging-",
            dir=config.output_directory.parent,
        )
    )
    tracker = DistributionTracker()
    started_at = clock()
    try:
        files = _write_staging_dataset(
            config,
            staging_directory=staging_directory,
            tracker=tracker,
            started_at=started_at,
            clock=clock,
        )
        write_json(
            staging_directory / "manifest.json",
            build_manifest(
                config,
                files=files,
                distributions=tracker.as_mapping(),
            ),
        )
        _publish(staging_directory, config.output_directory)
    except Exception:
        shutil.rmtree(staging_directory, ignore_errors=True)
        raise
    return GenerationSummary(
        nodes=files["nodes.jsonl"].records,
        edges=files["edges.jsonl"].records,
        queries=files["queries.jsonl"].records,
        ground_truth=files["ground_truth.jsonl"].records,
        operations=files["operations.jsonl"].records,
    )


def _write_staging_dataset(
    config: GeneratorConfig,
    *,
    staging_directory: Path,
    tracker: DistributionTracker,
    started_at: float,
    clock,
) -> dict[str, FileStatistics]:
    return {
        "nodes.jsonl": write_jsonl(
            staging_directory / "nodes.jsonl",
            node_records(
                config,
                node_rng=random.Random(config.seed),
                tracker=tracker,
                started_at=started_at,
                clock=clock,
            ),
        ),
        "ground_truth.jsonl": write_jsonl(
            staging_directory / "ground_truth.jsonl",
            ground_truth_records(config, tracker=tracker),
        ),
        "edges.jsonl": write_jsonl(
            staging_directory / "edges.jsonl",
            edge_records(
                config,
                edge_rng=random.Random(config.seed ^ 0xA5A5A5A5),
                tracker=tracker,
            ),
        ),
        "operations.jsonl": write_jsonl(
            staging_directory / "operations.jsonl",
            operation_records(config, tracker=tracker),
        ),
        "queries.jsonl": write_jsonl(
            staging_directory / "queries.jsonl",
            query_records(
                config,
                query_rng=random.Random(config.seed ^ 0x5A5A5A5A),
                tracker=tracker,
            ),
        ),
    }


def _publish(staging_directory: Path, output_directory: Path) -> None:
    backup_directory = output_directory.with_name(f".{output_directory.name}-backup")
    previous_moved = False
    try:
        if backup_directory.exists():
            raise PublicationError("Dataset backup directory already exists")
        if output_directory.exists():
            os.replace(output_directory, backup_directory)
            previous_moved = True
        os.replace(staging_directory, output_directory)
    except OSError as exc:
        if previous_moved:
            try:
                os.replace(backup_directory, output_directory)
            except OSError as rollback_error:
                raise PublicationError(
                    "Dataset publication and rollback failed"
                ) from rollback_error
        raise PublicationError("Dataset publication failed") from exc
    if previous_moved:
        shutil.rmtree(backup_directory)


def node_records(
    config: GeneratorConfig,
    *,
    node_rng: random.Random,
    tracker: DistributionTracker,
    started_at: float,
    clock=time.monotonic,
) -> Iterator[Mapping[str, Any]]:
    for generated, address in enumerate(node_addresses(config), start=1):
        record = node_record(address, config, node_rng)
        tracker.node_types[address.node_type] += 1
        tracker.tenants[str(record["tenant_id"])] += 1
        tracker.acl_labels[str(record["acl_labels"][0])] += 1
        tracker.classification_levels[str(record["classification_level"])] += 1
        yield record
        if generated % config.progress_every == 0 or generated == config.nodes:
            elapsed = max(clock() - started_at, 1e-9)
            LOGGER.info(
                "Generated %d/%d nodes (%.2f%%, %.2f nodes/s)",
                generated,
                config.nodes,
                generated * 100 / config.nodes,
                generated / elapsed,
            )


def ground_truth_records(
    config: GeneratorConfig,
    *,
    tracker: DistributionTracker,
) -> Iterator[Mapping[str, Any]]:
    for address in node_addresses(config):
        domain = domain_name(address.domain_index)
        cluster = cluster_name(address.domain_index)
        concepts = [f"{cluster}-{suffix}" for suffix in CONCEPT_SUFFIXES[address.node_type]]
        tracker.concepts.update(concepts)
        yield {
            "chunk_id": chunk_id(
                domain,
                address.node_type,
                address.instance,
                address.version,
            ),
            "source_version": version_id(address.version),
            "concept_ids": concepts,
        }


def edge_records(
    config: GeneratorConfig,
    *,
    edge_rng: random.Random,
    tracker: DistributionTracker,
) -> Iterator[Mapping[str, Any]]:
    for domain_index in range(config.domains):
        domain = domain_name(domain_index)
        tenant = tenant_id(domain_index, config.tenants)
        for version in range(1, config.versions + 1):
            counts = node_type_counts(config, domain_index, version)
            for from_type, to_type, relationship_type in EDGE_TEMPLATES:
                complete_instances = min(counts[from_type], counts[to_type])
                for instance in range(1, complete_instances + 1):
                    tracker.relationship_types[relationship_type] += 1
                    yield edge_record(
                        domain=domain,
                        tenant=tenant,
                        version=version,
                        from_type=from_type,
                        to_type=to_type,
                        instance=instance,
                        relationship_type=relationship_type,
                        weight=edge_rng.choice((0.9, 1.0)),
                    )
            target_index = _cross_domain_target(
                domain_index,
                domain_count=config.domains,
                tenant_count=config.tenants,
            )
            if config.max_edges_per_node >= 6 and target_index is not None:
                target_domain = domain_name(target_index)
                relationship_type = "similar_to" if domain_index % 2 else "depends_on"
                tracker.relationship_types[relationship_type] += 1
                yield edge_record(
                    domain=domain,
                    tenant=tenant,
                    version=version,
                    from_type="service",
                    to_type="service",
                    instance=1,
                    relationship_type=relationship_type,
                    weight=0.5,
                    target_domain=target_domain,
                )


def edge_record(
    *,
    domain: str,
    tenant: str,
    version: int,
    from_type: str,
    to_type: str,
    instance: int,
    relationship_type: str,
    weight: float,
    target_domain: str | None = None,
) -> Mapping[str, Any]:
    selected_target = target_domain or domain
    selected_version = version_id(version)
    return {
        "tenant_id": tenant,
        "owner_source_id": source_id(domain, from_type),
        "owner_source_version": selected_version,
        "from_source_id": source_id(domain, from_type),
        "from_source_version": selected_version,
        "from_chunk_id": chunk_id(domain, from_type, instance, version),
        "to_source_id": source_id(selected_target, to_type),
        "to_source_version": selected_version,
        "to_chunk_id": chunk_id(selected_target, to_type, instance, version),
        "relationship_type": relationship_type,
        "metadata": {"weight": weight, "synthetic": True},
    }


def operation_records(
    config: GeneratorConfig,
    *,
    tracker: DistributionTracker,
) -> Iterator[Mapping[str, Any]]:
    operation_index = 0
    for domain_index in range(config.domains):
        domain = domain_name(domain_index)
        tenant = tenant_id(domain_index, config.tenants)
        for node_type in NODE_TYPES:
            operation_index += 1
            tracker.operations["add_source"] += 1
            yield {
                "operation_id": f"operation_{operation_index:08d}",
                "operation": "add_source",
                "tenant_id": tenant,
                "domain": domain,
                "source_id": source_id(domain, node_type),
                "source_version": "v1",
            }
            for version in range(2, config.versions + 1):
                operation_index += 1
                tracker.operations["replace_source"] += 1
                yield {
                    "operation_id": f"operation_{operation_index:08d}",
                    "operation": "replace_source",
                    "tenant_id": tenant,
                    "domain": domain,
                    "source_id": source_id(domain, node_type),
                    "from_version": version_id(version - 1),
                    "to_version": version_id(version),
                    "changes": _version_changes(node_type, version),
                }
        operation_index += 1
        tracker.operations["delete_source"] += 1
        yield {
            "operation_id": f"operation_{operation_index:08d}",
            "operation": "delete_source",
            "tenant_id": tenant,
            "domain": domain,
            "source_id": source_id(domain, "config"),
            "source_version": version_id(config.versions),
        }


def query_records(
    config: GeneratorConfig,
    *,
    query_rng: random.Random,
    tracker: DistributionTracker,
) -> Iterator[Mapping[str, Any]]:
    for query_index in range(config.queries):
        query_type = QUERY_TYPES[query_index % len(QUERY_TYPES)]
        if config.versions == 1 and query_type in {"before_update", "after_update"}:
            query_type = "easy"
        tracker.query_types[query_type] += 1
        domain_index = query_index % config.domains
        domain = domain_name(domain_index)
        tenant = tenant_id(domain_index, config.tenants)
        cluster = cluster_name(domain_index)
        version = 1 if query_type == "before_update" else config.versions
        service = chunk_id(domain, "service", 1, version)
        repository = chunk_id(domain, "repository", 1, version)
        graph_nodes = [
            chunk_id(domain, "controller", 1, version),
            service,
            repository,
            chunk_id(domain, "table", 1, version),
        ]
        expected_addresses = (
            NodeAddress(domain_index, version, "controller", 1),
            NodeAddress(domain_index, version, "service", 1),
            NodeAddress(domain_index, version, "repository", 1),
            NodeAddress(domain_index, version, "table", 1),
        )
        security = [security_values(address, config.seed) for address in expected_addresses]
        allowed_acl_labels = sorted({item[0] for item in security})
        maximum_classification = max(item[1] for item in security)
        deleted_node = chunk_id(domain, "config", 1, config.versions)
        is_deleted_query = query_type == "deleted"
        yield {
            "query_id": f"query_{query_index + 1:06d}",
            "query_type": query_type,
            "query": QUERY_VARIANTS[query_type].format(domain=domain),
            "tenant_id": tenant,
            "domain": domain,
            "search_mode": query_rng.choice(("semantic", "bm25", "hybrid")),
            "allowed_acl_labels": allowed_acl_labels,
            "max_classification_level": maximum_classification,
            "expected_seed_nodes": [] if is_deleted_query else [service, repository],
            "expected_graph_nodes": [] if is_deleted_query else graph_nodes,
            "expected_seed_concept_ids": [] if is_deleted_query else [f"{cluster}-storage"],
            "expected_graph_concept_ids": (
                []
                if is_deleted_query
                else [
                    f"{cluster}-request-routing",
                    f"{cluster}-processing",
                    f"{cluster}-storage",
                    f"{cluster}-persistence",
                ]
            ),
            "expected_relationship_types": ([] if is_deleted_query else ["calls", "writes"]),
            "expected_source_ids": (
                []
                if is_deleted_query
                else [
                    source_id(domain, "service"),
                    source_id(domain, "repository"),
                ]
            ),
            "graph_depth": 3,
            "forbidden_domains": _forbidden_domains(
                config.domains,
                domain_index,
                query_rng,
            ),
            "forbidden_tenants": _forbidden_tenants(
                config.tenants,
                tenant,
                query_rng,
            ),
            "forbidden_nodes": [deleted_node] if is_deleted_query else [],
            "expected_state": _expected_state(query_type, config.versions),
            "source_version": version_id(version),
        }


def node_addresses(config: GeneratorConfig) -> Iterator[NodeAddress]:
    for domain_index in range(config.domains):
        for version in range(1, config.versions + 1):
            slot_count = nodes_in_slot(config, domain_index, version)
            for local_index in range(slot_count):
                yield NodeAddress(
                    domain_index=domain_index,
                    version=version,
                    node_type=NODE_TYPES[local_index % len(NODE_TYPES)],
                    instance=local_index // len(NODE_TYPES) + 1,
                )


def nodes_in_slot(config: GeneratorConfig, domain_index: int, version: int) -> int:
    slot_index = domain_index * config.versions + version - 1
    slot_count = config.domains * config.versions
    quotient, remainder = divmod(config.nodes, slot_count)
    return quotient + (1 if slot_index < remainder else 0)


def nodes_in_domain(config: GeneratorConfig, domain_index: int) -> int:
    return sum(
        nodes_in_slot(config, domain_index, version) for version in range(1, config.versions + 1)
    )


def node_type_counts(
    config: GeneratorConfig,
    domain_index: int,
    version: int = 1,
) -> dict[str, int]:
    slot_count = nodes_in_slot(config, domain_index, version)
    complete_cycles, remainder = divmod(slot_count, len(NODE_TYPES))
    return {
        node_type: complete_cycles + (1 if index < remainder else 0)
        for index, node_type in enumerate(NODE_TYPES)
    }


def node_record(
    address: NodeAddress,
    config: GeneratorConfig,
    node_rng: random.Random,
) -> Mapping[str, Any]:
    domain = domain_name(address.domain_index)
    cluster = cluster_name(address.domain_index)
    tenant = tenant_id(address.domain_index, config.tenants)
    label = type_label(address.node_type)
    title = f"{domain} {label} {address.instance:04d}"
    text = node_rng.choice(TEXT_VARIANTS[address.node_type]).format(
        title=title,
        cluster=cluster,
    )
    if address.version > 1:
        text = f"{text} This is source version {version_id(address.version)}."
    acl_label, classification_level = security_values(address, config.seed)
    return {
        "chunk_id": chunk_id(
            domain,
            address.node_type,
            address.instance,
            address.version,
        ),
        "domain": domain,
        "tenant_id": tenant,
        "source_id": source_id(domain, address.node_type),
        "source_version": version_id(address.version),
        "source_uri": (f"synthetic://{tenant}/{domain}/{label}/{address.instance:04d}"),
        "text": text,
        "metadata": {
            "node_type": address.node_type,
            "cluster": cluster,
            "title": title,
            "keywords": [cluster, *KEYWORDS[address.node_type]],
            "synthetic": True,
        },
        "acl_labels": [acl_label],
        "classification_level": classification_level,
        "token_estimate": max(1, len(text.split())),
    }


def security_values(address: NodeAddress, seed: int) -> tuple[str, int]:
    value = (
        seed
        + address.domain_index * 31
        + address.version * 17
        + NODE_TYPES.index(address.node_type) * 13
        + address.instance * 7
    )
    security_rng = random.Random(value ^ 0xC3C3C3C3)
    return security_rng.choice(ACL_LABELS), security_rng.randrange(4)


def _version_changes(node_type: str, version: int) -> list[str]:
    changes = ["text", "metadata"]
    if node_type in {"service", "repository", "event"}:
        changes.append("relationships")
    if (NODE_TYPES.index(node_type) + version) % 2 == 0:
        changes.extend(("acl_labels", "classification_level"))
    return changes


def _forbidden_domains(
    domain_count: int,
    selected_index: int,
    query_rng: random.Random,
) -> list[str]:
    available = [index for index in range(domain_count) if index != selected_index]
    query_rng.shuffle(available)
    return [domain_name(index) for index in available[:2]]


def _forbidden_tenants(
    tenant_count: int,
    selected_tenant: str,
    query_rng: random.Random,
) -> list[str]:
    available = [
        f"tenant_{index:04d}"
        for index in range(1, tenant_count + 1)
        if f"tenant_{index:04d}" != selected_tenant
    ]
    query_rng.shuffle(available)
    return available[:2]


def _cross_domain_target(
    domain_index: int,
    *,
    domain_count: int,
    tenant_count: int,
) -> int | None:
    later = domain_index + tenant_count
    if later < domain_count:
        return later
    first_for_tenant = domain_index % tenant_count
    return first_for_tenant if first_for_tenant != domain_index else None


def _expected_state(query_type: str, versions: int = 2) -> str:
    if query_type == "before_update" and versions > 1:
        return "historical"
    if query_type == "deleted":
        return "deleted"
    return "current"
