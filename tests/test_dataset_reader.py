from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sovereignflow.domain import ValidationError
from sovereignflow.infrastructure import JsonlDatasetReader, RelationshipScope
from sovereignflow.infrastructure import dataset_reader as reader_module


def write_jsonl(path: Path, records) -> None:
    path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
    )


def node(
    source_id: str,
    version: str,
    *,
    chunk_id: str | None = None,
    domain: str = "neutral",
    tenant_id: str = "tenant-a",
) -> dict:
    selected_chunk = chunk_id or f"{source_id}-{version}"
    return {
        "chunk_id": selected_chunk,
        "domain": domain,
        "tenant_id": tenant_id,
        "source_id": source_id,
        "source_version": version,
        "source_uri": f"synthetic://{selected_chunk}",
        "text": f"text {selected_chunk}",
        "metadata": {"kind": "synthetic"},
        "acl_labels": ["public"],
        "classification_level": 1,
    }


def edge(
    from_source: str,
    from_chunk: str,
    to_source: str,
    to_chunk: str,
    version: str,
) -> dict:
    return {
        "tenant_id": "tenant-a",
        "owner_source_id": from_source,
        "owner_source_version": version,
        "from_source_id": from_source,
        "from_source_version": version,
        "from_chunk_id": from_chunk,
        "to_source_id": to_source,
        "to_source_version": version,
        "to_chunk_id": to_chunk,
        "relationship_type": "references",
        "metadata": {"weight": 1},
    }


def files(tmp_path: Path):
    nodes = tmp_path / "nodes.jsonl"
    edges = tmp_path / "edges.jsonl"
    operations = tmp_path / "operations.jsonl"
    write_jsonl(
        nodes,
        [
            node("source-a", "v1"),
            node("source-b", "v1"),
            node("source-a", "v2"),
            node("source-b", "v2"),
            node("ignored", "v1", domain="other"),
        ],
    )
    write_jsonl(
        edges,
        [
            edge("source-a", "source-a-v2", "source-b", "source-b-v2", "v2"),
            edge("source-b", "source-b-v2", "source-a", "source-a-v2", "v2"),
            edge("source-a", "source-a-v2", "external", "external-v2", "v2"),
            {
                **edge("source-a", "source-a-v2", "source-b", "source-b-v2", "v2"),
                "tenant_id": "tenant-b",
            },
        ],
    )
    write_jsonl(
        operations,
        [
            {
                "operation": "add_source",
                "domain": "neutral",
                "tenant_id": "tenant-a",
                "source_id": "source-a",
                "source_version": "v1",
            },
            {
                "operation": "replace_source",
                "domain": "neutral",
                "tenant_id": "tenant-a",
                "source_id": "source-a",
                "from_version": "v1",
                "to_version": "v2",
            },
            {
                "operation": "add_source",
                "domain": "neutral",
                "tenant_id": "tenant-a",
                "source_id": "source-b",
                "source_version": "v1",
            },
            {
                "operation": "replace_source",
                "domain": "neutral",
                "tenant_id": "tenant-a",
                "source_id": "source-b",
                "from_version": "v1",
                "to_version": "v2",
            },
            {
                "operation": "delete_source",
                "domain": "neutral",
                "tenant_id": "tenant-a",
                "source_id": "source-b",
                "source_version": "v2",
            },
        ],
    )
    return nodes, edges, operations


def reader(tmp_path: Path, **kwargs) -> JsonlDatasetReader:
    nodes, edges, operations = files(tmp_path)
    return JsonlDatasetReader(
        import_id="import-1",
        nodes_path=nodes,
        edges_path=edges,
        operations_path=operations,
        workspace_path=tmp_path / "workspace.sqlite",
        relationship_scope=kwargs.pop(
            "relationship_scope",
            RelationshipScope.INTERNAL,
        ),
        **kwargs,
    )


def test_reader_prepares_commands_relationships_and_deletions(tmp_path: Path) -> None:
    selected = reader(tmp_path)

    request = selected.prepare(domain="neutral", tenant_id="tenant-a")
    commands = list(selected.source_commands())
    relationships = list(selected.relationship_commands())
    deletions = list(selected.deletions())

    assert request.source_count == 4
    assert request.chunk_count == 4
    assert request.relationship_count == 2
    assert request.deletion_count == 1
    assert len(request.dataset_hash) == 64
    assert [(item.source_id, item.source_version) for item in commands] == [
        ("source-a", "v1"),
        ("source-a", "v2"),
        ("source-b", "v1"),
        ("source-b", "v2"),
    ]
    assert all(item.metadata == {} for item in commands)
    assert commands[0].idempotency_key == ("dataset:neutral:tenant-a:source-a:v1")
    assert {item.source_id for item in relationships} == {"source-a", "source-b"}
    assert sum(len(item.relationships) for item in relationships) == 2
    assert deletions == ["source-b"]


def test_reader_is_deterministic_and_skips_unselected_external_edges(tmp_path: Path) -> None:
    first = reader(tmp_path)
    first_request = first.prepare(domain="neutral", tenant_id="tenant-a")
    second_root = tmp_path / "second"
    second_root.mkdir()
    second = reader(second_root)
    second_request = second.prepare(domain="neutral", tenant_id="tenant-a")

    assert first_request.dataset_hash == second_request.dataset_hash


def test_reader_requires_prepare_and_matching_data(tmp_path: Path) -> None:
    selected = reader(tmp_path)
    with pytest.raises(ValidationError, match="prepared"):
        list(selected.source_commands())
    with pytest.raises(ValidationError, match="prepared"):
        list(selected.relationship_commands())
    with pytest.raises(ValidationError, match="prepared"):
        list(selected.deletions())
    with pytest.raises(ValidationError, match="does not contain"):
        selected.prepare(domain="missing", tenant_id="tenant-a")


def test_reader_rejects_external_relationship_when_strict(tmp_path: Path) -> None:
    selected = reader(tmp_path, relationship_scope=RelationshipScope.COMPLETE)
    with pytest.raises(ValidationError, match="Complete relationship scope"):
        selected.prepare(domain="neutral", tenant_id="tenant-a")


def test_reader_rejects_missing_current_versions(tmp_path: Path) -> None:
    selected = reader(tmp_path)
    write_jsonl(selected._operations_path, [])
    with pytest.raises(ValidationError, match="current version"):
        selected.prepare(domain="neutral", tenant_id="tenant-a")


@pytest.mark.parametrize(
    ("modify", "message"),
    [
        (
            lambda nodes, edges, operations: write_jsonl(
                nodes,
                [node("source-a", "v1"), node("source-a", "v1")],
            ),
            "Duplicate dataset node",
        ),
        (
            lambda nodes, edges, operations: write_jsonl(
                operations,
                [
                    {
                        "operation": "unsupported",
                        "domain": "neutral",
                        "tenant_id": "tenant-a",
                        "source_id": "source-a",
                    }
                ],
            ),
            "Unsupported",
        ),
        (
            lambda nodes, edges, operations: write_jsonl(
                operations,
                [
                    {
                        "operation": "add_source",
                        "domain": "neutral",
                        "tenant_id": "tenant-a",
                        "source_id": "source-a",
                        "source_version": "missing",
                    }
                ],
            ),
            "unknown source version",
        ),
        (
            lambda nodes, edges, operations: write_jsonl(
                edges,
                [
                    {
                        **edge(
                            "source-a",
                            "source-a-v2",
                            "source-b",
                            "source-b-v2",
                            "v2",
                        ),
                        "owner_source_id": "wrong",
                    }
                ],
            ),
            "owner",
        ),
        (
            lambda nodes, edges, operations: write_jsonl(
                edges,
                [
                    {
                        **edge(
                            "source-a",
                            "source-a-v2",
                            "source-b",
                            "source-b-v2",
                            "v2",
                        ),
                        "to_source_id": "wrong",
                    }
                ],
            ),
            "target",
        ),
        (
            lambda nodes, edges, operations: write_jsonl(
                edges,
                [
                    edge(
                        "source-a",
                        "source-a-v2",
                        "source-b",
                        "source-b-v2",
                        "v2",
                    )
                ]
                * 2,
            ),
            "Duplicate dataset relationship",
        ),
    ],
)
def test_reader_rejects_invalid_dataset_contracts(
    tmp_path: Path,
    modify,
    message: str,
) -> None:
    nodes, edges, operations = files(tmp_path)
    modify(nodes, edges, operations)
    selected = JsonlDatasetReader(
        import_id="import-1",
        nodes_path=nodes,
        edges_path=edges,
        operations_path=operations,
        workspace_path=tmp_path / "workspace.sqlite",
        relationship_scope=RelationshipScope.INTERNAL,
    )
    with pytest.raises(ValidationError, match=message):
        selected.prepare(domain="neutral", tenant_id="tenant-a")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("\n", "empty line"),
        ("not-json\n", "invalid JSON"),
        ("[]\n", "JSON object"),
    ],
)
def test_reader_rejects_invalid_jsonl(
    tmp_path: Path,
    content: str,
    message: str,
) -> None:
    nodes, edges, operations = files(tmp_path)
    nodes.write_text(content, encoding="utf-8")
    selected = JsonlDatasetReader(
        import_id="import-1",
        nodes_path=nodes,
        edges_path=edges,
        operations_path=operations,
        workspace_path=tmp_path / "workspace.sqlite",
        relationship_scope=RelationshipScope.INTERNAL,
    )
    with pytest.raises(ValidationError, match=message):
        selected.prepare(domain="neutral", tenant_id="tenant-a")


def test_reader_rejects_missing_file_and_invalid_constructor(tmp_path: Path) -> None:
    nodes, edges, operations = files(tmp_path)
    with pytest.raises(ValidationError, match="required"):
        JsonlDatasetReader(
            import_id="",
            nodes_path=nodes,
            edges_path=edges,
            operations_path=operations,
            workspace_path=tmp_path / "workspace.sqlite",
            relationship_scope=RelationshipScope.INTERNAL,
        )
    nodes.unlink()
    selected = JsonlDatasetReader(
        import_id="import-1",
        nodes_path=nodes,
        edges_path=edges,
        operations_path=operations,
        workspace_path=tmp_path / "workspace.sqlite",
        relationship_scope=RelationshipScope.INTERNAL,
    )
    with pytest.raises(ValidationError, match="cannot be opened"):
        selected.prepare(domain="neutral", tenant_id="tenant-a")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("metadata", [], "metadata"),
        ("acl_labels", [1], "ACL"),
        ("classification_level", True, "classification"),
        ("source_uri", 1, "source_uri"),
        ("text", "", "text"),
    ],
)
def test_reader_rejects_invalid_node_fields(
    tmp_path: Path,
    field: str,
    value,
    message: str,
) -> None:
    nodes, edges, operations = files(tmp_path)
    invalid = node("source-a", "v1")
    invalid[field] = value
    write_jsonl(nodes, [invalid])
    selected = JsonlDatasetReader(
        import_id="import-1",
        nodes_path=nodes,
        edges_path=edges,
        operations_path=operations,
        workspace_path=tmp_path / "workspace.sqlite",
        relationship_scope=RelationshipScope.INTERNAL,
    )
    with pytest.raises(ValidationError, match=message):
        selected.prepare(domain="neutral", tenant_id="tenant-a")


def test_reader_rejects_relationship_metadata_and_workspace_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    nodes, edges, operations = files(tmp_path)
    invalid_edge = edge(
        "source-a",
        "source-a-v2",
        "source-b",
        "source-b-v2",
        "v2",
    )
    invalid_edge["metadata"] = []
    write_jsonl(edges, [invalid_edge])
    selected = JsonlDatasetReader(
        import_id="import-1",
        nodes_path=nodes,
        edges_path=edges,
        operations_path=operations,
        workspace_path=tmp_path / "workspace.sqlite",
        relationship_scope=RelationshipScope.INTERNAL,
    )
    with pytest.raises(ValidationError, match="metadata"):
        selected.prepare(domain="neutral", tenant_id="tenant-a")

    monkeypatch.setattr(
        reader_module.sqlite3,
        "connect",
        lambda path: (_ for _ in ()).throw(sqlite3.Error("broken")),
    )
    with pytest.raises(ValidationError, match="workspace"):
        selected.prepare(domain="neutral", tenant_id="tenant-a")


def test_reader_serialization_helpers_reject_invalid_values() -> None:
    with pytest.raises(ValidationError, match="valid JSON"):
        reader_module._json(object())
    with pytest.raises(ValidationError, match="JSON object"):
        reader_module._object("[]", "value")
    with pytest.raises(ValidationError, match="list of strings"):
        reader_module._array("[1]", "value")
