from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing
from enum import StrEnum
from pathlib import Path
from typing import Any

from sovereignflow.domain import (
    DatasetImportRequest,
    DocumentChunk,
    DocumentSecurity,
    GraphNodeRef,
    GraphRelationship,
    IngestionCommand,
    ValidationError,
)


class RelationshipScope(StrEnum):
    INTERNAL = "internal"
    COMPLETE = "complete"


class JsonlDatasetReader:
    def __init__(
        self,
        *,
        import_id: str,
        nodes_path: Path,
        edges_path: Path,
        operations_path: Path,
        workspace_path: Path,
        relationship_scope: RelationshipScope,
    ) -> None:
        self._import_id = _required(import_id, "import_id")
        self._nodes_path = nodes_path
        self._edges_path = edges_path
        self._operations_path = operations_path
        self._workspace_path = workspace_path
        self._relationship_scope = RelationshipScope(relationship_scope)
        self._prepared = False

    def prepare(self, *, domain: str, tenant_id: str) -> DatasetImportRequest:
        selected_domain = _required(domain, "domain")
        selected_tenant = _required(tenant_id, "tenant_id")
        self._workspace_path.parent.mkdir(parents=True, exist_ok=True)
        self._workspace_path.unlink(missing_ok=True)
        digest = hashlib.sha256()
        try:
            with (
                closing(sqlite3.connect(self._workspace_path)) as connection,
                connection,
            ):
                _create_workspace(connection)
                chunk_count = self._load_nodes(
                    connection,
                    domain=selected_domain,
                    tenant_id=selected_tenant,
                    digest=digest,
                )
                self._load_operations(
                    connection,
                    domain=selected_domain,
                    tenant_id=selected_tenant,
                    digest=digest,
                )
                relationship_count = self._load_edges(
                    connection,
                    tenant_id=selected_tenant,
                    digest=digest,
                )
                source_count = int(
                    connection.execute("SELECT COUNT(*) FROM source_versions").fetchone()[0]
                )
                deletion_count = int(
                    connection.execute("SELECT COUNT(*) FROM deletions").fetchone()[0]
                )
                if source_count < 1:
                    raise ValidationError(
                        "Dataset does not contain sources for the selected domain and tenant"
                    )
                missing_current = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM source_versions source
                        LEFT JOIN current_versions current
                          ON current.source_id = source.source_id
                        WHERE current.source_id IS NULL
                        """
                    ).fetchone()[0]
                )
                if missing_current:
                    raise ValidationError(
                        "Operations do not define the current version for every source"
                    )
        except sqlite3.Error as exc:
            raise ValidationError("Dataset workspace could not be created") from exc
        self._prepared = True
        return DatasetImportRequest(
            import_id=self._import_id,
            domain=selected_domain,
            tenant_id=selected_tenant,
            dataset_hash=digest.hexdigest(),
            source_count=source_count,
            chunk_count=chunk_count,
            relationship_count=relationship_count,
            deletion_count=deletion_count,
        )

    def source_commands(self) -> Iterator[IngestionCommand]:
        self._require_prepared()
        with closing(sqlite3.connect(self._workspace_path)) as connection:
            rows = connection.execute(
                """
                SELECT source_id, source_version, domain, tenant_id, source_uri
                FROM source_versions
                ORDER BY source_id, version_order
                """
            )
            for source_id, source_version, domain, tenant_id, source_uri in rows:
                yield self._command(
                    connection,
                    source_id=str(source_id),
                    source_version=str(source_version),
                    domain=str(domain),
                    tenant_id=str(tenant_id),
                    source_uri=source_uri,
                    include_relationships=False,
                )

    def relationship_commands(self) -> Iterator[IngestionCommand]:
        self._require_prepared()
        with closing(sqlite3.connect(self._workspace_path)) as connection:
            rows = connection.execute(
                """
                SELECT source.source_id, source.source_version,
                       source.domain, source.tenant_id, source.source_uri
                FROM source_versions source
                JOIN current_versions current
                  ON current.source_id = source.source_id
                 AND current.source_version = source.source_version
                ORDER BY source.source_id
                """
            )
            for source_id, source_version, domain, tenant_id, source_uri in rows:
                yield self._command(
                    connection,
                    source_id=str(source_id),
                    source_version=str(source_version),
                    domain=str(domain),
                    tenant_id=str(tenant_id),
                    source_uri=source_uri,
                    include_relationships=True,
                )

    def deletions(self) -> Iterator[str]:
        self._require_prepared()
        with closing(sqlite3.connect(self._workspace_path)) as connection:
            rows = connection.execute("SELECT source_id FROM deletions ORDER BY source_id")
            for row in rows:
                yield str(row[0])

    def _load_nodes(
        self,
        connection: sqlite3.Connection,
        *,
        domain: str,
        tenant_id: str,
        digest: Any,
    ) -> int:
        count = 0
        version_order: dict[tuple[str, str], int] = {}
        for record, encoded in _jsonl(self._nodes_path):
            if record.get("domain") != domain or record.get("tenant_id") != tenant_id:
                continue
            chunk = _chunk(record, self._nodes_path)
            source_version = _string(record, "source_version", self._nodes_path)
            key = (chunk.source_id, source_version)
            if key not in version_order:
                version_order[key] = len(
                    [item for item in version_order if item[0] == chunk.source_id]
                )
            try:
                connection.execute(
                    """
                    INSERT INTO source_versions (
                        source_id, source_version, version_order,
                        domain, tenant_id, source_uri
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (source_id, source_version) DO NOTHING
                    """,
                    (
                        chunk.source_id,
                        source_version,
                        version_order[key],
                        chunk.domain,
                        chunk.tenant_id,
                        chunk.source_uri,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO nodes (
                        chunk_id, source_id, source_version, domain, tenant_id,
                        source_uri, text_content, metadata_json, acl_json,
                        security_json, position
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.source_id,
                        source_version,
                        chunk.domain,
                        chunk.tenant_id,
                        chunk.source_uri,
                        chunk.text,
                        _json(chunk.metadata),
                        _json(list(chunk.acl_labels)),
                        _json(_security_payload(chunk.security)),
                        count,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError(f"Duplicate dataset node: {chunk.chunk_id}") from exc
            digest.update(b"N")
            digest.update(encoded)
            count += 1
        return count

    def _load_operations(
        self,
        connection: sqlite3.Connection,
        *,
        domain: str,
        tenant_id: str,
        digest: Any,
    ) -> None:
        for record, encoded in _jsonl(self._operations_path):
            if record.get("domain") != domain or record.get("tenant_id") != tenant_id:
                continue
            operation = _string(record, "operation", self._operations_path)
            source_id = _string(record, "source_id", self._operations_path)
            if operation == "add_source":
                version = _string(record, "source_version", self._operations_path)
                _set_current(connection, source_id, version)
            elif operation == "replace_source":
                version = _string(record, "to_version", self._operations_path)
                _set_current(connection, source_id, version)
            elif operation == "delete_source":
                connection.execute(
                    "INSERT OR IGNORE INTO deletions (source_id) VALUES (?)",
                    (source_id,),
                )
            else:
                raise ValidationError(f"Unsupported dataset operation: {operation}")
            digest.update(b"O")
            digest.update(encoded)

    def _load_edges(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        digest: Any,
    ) -> int:
        count = 0
        for record, encoded in _jsonl(self._edges_path):
            if record.get("tenant_id") != tenant_id:
                continue
            from_chunk_id = _string(record, "from_chunk_id", self._edges_path)
            owner = connection.execute(
                """
                SELECT source_id, source_version
                FROM nodes
                WHERE chunk_id = ?
                """,
                (from_chunk_id,),
            ).fetchone()
            if owner is None:
                continue
            to_chunk_id = _string(record, "to_chunk_id", self._edges_path)
            target = connection.execute(
                "SELECT source_id, source_version FROM nodes WHERE chunk_id = ?",
                (to_chunk_id,),
            ).fetchone()
            if target is None:
                if self._relationship_scope == RelationshipScope.COMPLETE:
                    raise ValidationError(
                        f"Complete relationship scope requires target: {to_chunk_id}"
                    )
                continue
            owner_source_id = _string(record, "owner_source_id", self._edges_path)
            owner_version = _string(record, "owner_source_version", self._edges_path)
            from_source_id = _string(record, "from_source_id", self._edges_path)
            to_source_id = _string(record, "to_source_id", self._edges_path)
            if owner != (owner_source_id, owner_version):
                raise ValidationError("Relationship owner does not match its source node")
            if target != (
                to_source_id,
                _string(record, "to_source_version", self._edges_path),
            ):
                raise ValidationError("Relationship target does not match its target node")
            relationship_type = _string(record, "relationship_type", self._edges_path)
            metadata = record.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValidationError("Relationship metadata must be a JSON object")
            try:
                connection.execute(
                    """
                    INSERT INTO relationships (
                        owner_source_id, owner_source_version,
                        from_source_id, from_chunk_id,
                        to_source_id, to_chunk_id,
                        relationship_type, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        owner_source_id,
                        owner_version,
                        from_source_id,
                        from_chunk_id,
                        to_source_id,
                        to_chunk_id,
                        relationship_type,
                        _json(metadata),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValidationError("Duplicate dataset relationship") from exc
            digest.update(b"E")
            digest.update(encoded)
            count += 1
        return count

    def _command(
        self,
        connection: sqlite3.Connection,
        *,
        source_id: str,
        source_version: str,
        domain: str,
        tenant_id: str,
        source_uri: str | None,
        include_relationships: bool,
    ) -> IngestionCommand:
        chunk_rows = connection.execute(
            """
            SELECT chunk_id, source_uri, text_content, metadata_json,
                   acl_json, security_json
            FROM nodes
            WHERE source_id = ? AND source_version = ?
            ORDER BY position
            """,
            (source_id, source_version),
        )
        chunks = tuple(
            DocumentChunk(
                chunk_id=str(row[0]),
                domain=domain,
                tenant_id=tenant_id,
                source_id=source_id,
                source_uri=row[1],
                text=str(row[2]),
                metadata=_object(row[3], "node metadata"),
                acl_labels=tuple(_array(row[4], "node ACL")),
                security=_security_from_json(row[5]),
            )
            for row in chunk_rows
        )
        relationships: tuple[GraphRelationship, ...] = ()
        if include_relationships:
            relationship_rows = connection.execute(
                """
                SELECT from_source_id, from_chunk_id, to_source_id, to_chunk_id,
                       relationship_type, metadata_json
                FROM relationships
                WHERE owner_source_id = ? AND owner_source_version = ?
                ORDER BY from_source_id, from_chunk_id, to_source_id, to_chunk_id,
                         relationship_type
                """,
                (source_id, source_version),
            )
            relationships = tuple(
                GraphRelationship(
                    from_node=GraphNodeRef(str(row[0]), str(row[1])),
                    to_node=GraphNodeRef(str(row[2]), str(row[3])),
                    relationship_type=str(row[4]),
                    metadata=_object(row[5], "relationship metadata"),
                )
                for row in relationship_rows
            )
        return IngestionCommand(
            idempotency_key=(f"dataset:{domain}:{tenant_id}:{source_id}:{source_version}"),
            domain=domain,
            tenant_id=tenant_id,
            source_id=source_id,
            source_version=source_version,
            source_uri=source_uri,
            metadata={},
            chunks=chunks,
            relationships=relationships,
        )

    def _require_prepared(self) -> None:
        if not self._prepared:
            raise ValidationError("Dataset reader must be prepared before reading commands")


def _create_workspace(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE source_versions (
            source_id TEXT NOT NULL,
            source_version TEXT NOT NULL,
            version_order INTEGER NOT NULL,
            domain TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            source_uri TEXT,
            PRIMARY KEY (source_id, source_version)
        );
        CREATE TABLE nodes (
            chunk_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_version TEXT NOT NULL,
            domain TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            source_uri TEXT,
            text_content TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            acl_json TEXT NOT NULL,
            security_json TEXT NOT NULL,
            position INTEGER NOT NULL,
            FOREIGN KEY (source_id, source_version)
                REFERENCES source_versions (source_id, source_version)
        );
        CREATE TABLE relationships (
            owner_source_id TEXT NOT NULL,
            owner_source_version TEXT NOT NULL,
            from_source_id TEXT NOT NULL,
            from_chunk_id TEXT NOT NULL,
            to_source_id TEXT NOT NULL,
            to_chunk_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            PRIMARY KEY (
                owner_source_id, owner_source_version,
                from_source_id, from_chunk_id,
                to_source_id, to_chunk_id, relationship_type
            )
        );
        CREATE TABLE current_versions (
            source_id TEXT PRIMARY KEY,
            source_version TEXT NOT NULL
        );
        CREATE TABLE deletions (
            source_id TEXT PRIMARY KEY
        );
        """
    )


def _set_current(
    connection: sqlite3.Connection,
    source_id: str,
    source_version: str,
) -> None:
    exists = connection.execute(
        """
        SELECT 1 FROM source_versions
        WHERE source_id = ? AND source_version = ?
        """,
        (source_id, source_version),
    ).fetchone()
    if exists is None:
        raise ValidationError(
            f"Operation references an unknown source version: {source_id}/{source_version}"
        )
    connection.execute(
        """
        INSERT INTO current_versions (source_id, source_version)
        VALUES (?, ?)
        ON CONFLICT (source_id)
        DO UPDATE SET source_version = excluded.source_version
        """,
        (source_id, source_version),
    )


def _jsonl(path: Path) -> Iterator[tuple[dict[str, Any], bytes]]:
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise ValidationError(f"Dataset file cannot be opened: {path}") from exc
    with stream:
        for line_number, encoded in enumerate(stream, start=1):
            if not encoded.strip():
                raise ValidationError(f"{path}:{line_number} contains an empty line")
            try:
                value = json.loads(encoded)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError(f"{path}:{line_number} contains invalid JSON") from exc
            if not isinstance(value, dict):
                raise ValidationError(f"{path}:{line_number} must contain a JSON object")
            yield value, encoded


def _chunk(record: dict[str, Any], path: Path) -> DocumentChunk:
    metadata = record.get("metadata", {})
    acl_labels = record.get("acl_labels", [])
    security = _security_from_record(record, path)
    if not isinstance(metadata, dict):
        raise ValidationError(f"{path}: node metadata must be a JSON object")
    if not isinstance(acl_labels, list) or any(not isinstance(item, str) for item in acl_labels):
        raise ValidationError(f"{path}: node ACL must be a list of strings")
    source_uri = record.get("source_uri")
    if source_uri is not None and not isinstance(source_uri, str):
        raise ValidationError(f"{path}: source_uri must be a string or null")
    return DocumentChunk(
        chunk_id=_string(record, "chunk_id", path),
        domain=_string(record, "domain", path),
        tenant_id=_string(record, "tenant_id", path),
        source_id=_string(record, "source_id", path),
        source_uri=source_uri,
        text=_string(record, "text", path),
        metadata=metadata,
        acl_labels=tuple(acl_labels),
        security=security,
    )


def _string(record: dict[str, Any], field: str, path: Path) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{path}: {field} must be a non-empty string")
    return value.strip()


def _required(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValidationError(f"{field} is required")
    return normalized


def _security_from_record(record: dict[str, Any], path: Path) -> DocumentSecurity:
    security = record.get("security", {})
    if not isinstance(security, dict):
        raise ValidationError(f"{path}: node security must be a JSON object")
    clearance_label = security.get("clearance_label")
    classification_labels = security.get("classification_labels", [])
    if clearance_label is not None and not isinstance(clearance_label, str):
        raise ValidationError(f"{path}: security.clearance_label must be a string or null")
    if not isinstance(classification_labels, list) or any(
        not isinstance(item, str) for item in classification_labels
    ):
        raise ValidationError(f"{path}: security.classification_labels must be a list of strings")
    return DocumentSecurity(
        clearance_label=clearance_label,
        classification_labels=tuple(classification_labels),
    )


def _security_from_json(value: str) -> DocumentSecurity:
    decoded = _object(value, "node security")
    return DocumentSecurity(
        clearance_label=decoded.get("clearance_label"),
        classification_labels=tuple(
            _array(_json(decoded.get("classification_labels", [])), "node security labels")
        ),
    )


def _security_payload(security: DocumentSecurity) -> dict[str, object]:
    return {
        "clearance_label": security.clearance_label,
        "classification_labels": list(security.classification_labels),
    }


def _json(value: Any) -> str:
    serializable = dict(value) if isinstance(value, Mapping) else value
    try:
        return json.dumps(
            serializable,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("Dataset metadata must be valid JSON") from exc


def _object(value: str, context: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValidationError(f"{context} must be a JSON object")
    return decoded


def _array(value: str, context: str) -> list[str]:
    decoded = json.loads(value)
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValidationError(f"{context} must be a list of strings")
    return decoded
