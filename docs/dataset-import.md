# Neutral dataset import

SovereignFlow imports neutral source versions, document chunks, relationships, and
deletion operations without interpreting domain-specific names or metadata.

PostgreSQL remains the source of truth. Weaviate is a rebuildable search index.
Relationships are published only after every selected source version has been staged
and indexed.

## Input files

The operational JSONL adapter accepts three files:

- `nodes.jsonl` — document chunks and their source versions;
- `edges.jsonl` — neutral relationships between chunks;
- `operations.jsonl` — active-version selection and source deletions.

Every non-empty line must contain one JSON object. Invalid JSON, duplicate chunks,
duplicate relationships, unknown versions, dangling relationship targets in complete
scope, invalid ACL values, and invalid classification values fail the import.

### Node

```json
{
  "chunk_id": "chunk-001",
  "domain": "example",
  "tenant_id": "tenant-a",
  "source_id": "source-001",
  "source_version": "v1",
  "source_uri": "https://example.test/source-001",
  "text": "Neutral document content.",
  "metadata": {"document_type": "guide"},
  "acl_labels": ["public"],
  "classification_level": 1
}
```

### Relationship

```json
{
  "tenant_id": "tenant-a",
  "owner_source_id": "source-001",
  "owner_source_version": "v1",
  "from_source_id": "source-001",
  "from_source_version": "v1",
  "from_chunk_id": "chunk-001",
  "to_source_id": "source-002",
  "to_source_version": "v1",
  "to_chunk_id": "chunk-002",
  "relationship_type": "references",
  "metadata": {"weight": 1.0}
}
```

### Operations

```json
{"operation":"add_source","domain":"example","tenant_id":"tenant-a","source_id":"source-001","source_version":"v1"}
{"operation":"replace_source","domain":"example","tenant_id":"tenant-a","source_id":"source-002","from_version":"v1","to_version":"v2"}
{"operation":"delete_source","domain":"example","tenant_id":"tenant-a","source_id":"source-003"}
```

## Relationship scope

The import command requires an explicit relationship scope:

- `internal` imports relationships whose two endpoints are present in the selected
  domain and tenant boundary; relationships leaving that explicitly selected boundary
  are excluded;
- `complete` requires every relationship endpoint to be present and rejects any
  dangling or external target.

There is no implicit scope selection.

## Import

The selected domain must exist in the SovereignFlow configuration and its tenant,
collection, ACL labels, and classification ceiling define the import boundary.

```bash
sovereignflow-import import \
  --config config/sovereignflow.yaml \
  --domain example \
  --nodes generated/nodes.jsonl \
  --edges generated/edges.jsonl \
  --operations generated/operations.jsonl \
  --workspace .work/example-import.sqlite \
  --import-id example-2026-06-14 \
  --relationship-scope complete
```

The SQLite workspace is an operational staging index. It prevents file ordering and
dataset size from forcing the complete dataset into application memory.

Import phases are durable:

1. stage and index source versions;
2. publish relationships for active versions;
3. delete explicitly removed sources;
4. mark the import complete.

An embedding or Weaviate failure marks the run as failed. Repeating the same command
resumes through idempotent source operations; no provider fallback is attempted.

## Status

```bash
sovereignflow-import status \
  --config config/sovereignflow.yaml \
  --domain example \
  --import-id example-2026-06-14
```

The JSON response contains the safe import state, expected counts, completed counts,
safe error code, and timestamps. It never returns document text.

## Consistency verification

```bash
sovereignflow-import verify \
  --config config/sovereignflow.yaml \
  --domain example
```

The command compares active PostgreSQL sources and chunks with Weaviate objects and
reports active graph relationships. Exit code `3` means that the active chunk count
and index count differ. Exit code `2` indicates a controlled operational failure.

## Integration test

The lifecycle integration test requires real PostgreSQL and Weaviate services. The
embedding adapter uses a controlled OpenAI-compatible HTTP endpoint.

```bash
export SOVEREIGNFLOW_TEST_POSTGRES_URL='postgresql://user:password@127.0.0.1:25432/sovereignflow'
export SOVEREIGNFLOW_TEST_WEAVIATE_HOST='127.0.0.1'
export SOVEREIGNFLOW_TEST_WEAVIATE_API_KEY='test-key'
export SOVEREIGNFLOW_TEST_WEAVIATE_HTTP_PORT='28080'
export SOVEREIGNFLOW_TEST_WEAVIATE_GRPC_PORT='25005'

python -m pytest \
  tests/test_ingestion_integration.py::test_dataset_import_full_lifecycle_across_real_adapters
```

The scenario proves failed-import visibility and resumption, idempotent replay,
version replacement, cyclic cross-source relationships, source deletion, and storage
consistency.
