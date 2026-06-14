# SovereignFlow synthetic dataset generator

This independent Python 3.11+ project generates deterministic, streaming JSONL datasets for retrieval, graph, versioning, and security tests.

It has no runtime or code dependency on SovereignFlow, PostgreSQL, Weaviate, embedding services, or model providers.

## Generated files

- `nodes.jsonl` — versioned document chunks intended for later import;
- `edges.jsonl` — versioned intra-domain and controlled cross-domain relationships;
- `operations.jsonl` — ordered add, replace, and delete scenarios;
- `queries.jsonl` — retrieval, graph, security, version, and deletion expectations;
- `ground_truth.jsonl` — evaluation-only concept mappings;
- `manifest.json` — configuration, record counts, distributions, byte sizes, and SHA-256 checksums.

`ground_truth.jsonl` must never be indexed or sent to a model.

## Installation

```bash
cd generator
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Usage

```bash
python -m dataset_generator \
  --out ./generated \
  --nodes 5000000 \
  --domains 10000 \
  --tenants 10 \
  --seed 123 \
  --queries 10000 \
  --max-edges-per-node 6 \
  --versions 3 \
  --progress-every 100000
```

Existing output files are rejected unless `--overwrite` is supplied.

Every domain and version requires ten baseline nodes. Consequently:

```text
nodes >= domains × versions × 10
```

At least five outgoing edges must be permitted because the service baseline contains five distinct relationships. A sixth edge enables controlled cross-domain `depends_on` or `similar_to` relationships.

## Node contract

Each node contains:

```text
chunk_id
domain
tenant_id
source_id
source_version
source_uri
text
metadata
acl_labels
classification_level
token_estimate
```

Chunk identifiers include their source version and are globally unique inside the dataset.

## Graph contract

Generated relationship types:

```text
calls
writes
reads
configured_by
validates_with
emits
handles
belongs_to
depends_on
similar_to
```

Cross-domain relationships:

- never cross tenant boundaries;
- use only `depends_on` or `similar_to`;
- preserve the source version;
- respect `--max-edges-per-node`.

## Operations

`operations.jsonl` describes an ordered scenario:

1. add every source at `v1`;
2. replace each source through all requested versions;
3. delete the current configuration source for each domain.

Replace operations explicitly declare whether they change text, metadata, relationships, ACL labels, or classification.

The generator describes these operations but does not execute them against any database.

## Query categories

The query stream cycles through:

- easy;
- confusing;
- graph;
- security;
- control;
- before-update;
- after-update;
- deleted-source.

Expectations include exact node identifiers, stable concept identifiers, source versions, allowed ACL labels, classification ceilings, forbidden domains, forbidden tenants, and forbidden nodes.

## Ground truth

Concept mappings are stored separately from indexable nodes:

```json
{
  "chunk_id": "Orders_000001_Service_V0002_0001",
  "source_version": "v2",
  "concept_ids": [
    "orders-processing",
    "orders-validation",
    "orders-storage"
  ]
}
```

Concept identifiers remain stable across versions even when chunk identifiers or text change.

## Determinism

Node, edge, query, and security generation use isolated deterministic random streams or address-derived random generators.

Changing the query count does not change nodes, edges, operations, or ground truth. Identical configuration and seed values produce byte-identical data files and checksums.

## Atomic publication

All data files are written to a staging directory inside the output directory. Completed files are moved into place only after generation succeeds. `manifest.json` is published last and acts as the completeness marker.

If generation fails before publication:

- the previous complete dataset remains unchanged;
- staging files are removed;
- no new manifest is published.

## Memory behavior

Records are calculated and written one at a time. The implementation does not retain the complete node, edge, query, operation, or ground-truth collection in memory.

Only bounded counters used by the manifest remain resident.

## Checksum verification

The manifest contains SHA-256 values for every JSONL file. Example:

```bash
sha256sum generated/nodes.jsonl
```

Compare the result with:

```text
manifest.json → files → nodes.jsonl → sha256
```

## Tests

```bash
python -m pytest --cov=dataset_generator --cov-branch --cov-report=term-missing
ruff check .
ruff format --check .
python -m compileall -q src tests
```

The project enforces 100% statement and branch coverage.

## Evaluation runner

The evaluator is a separate module inside this independent project. It communicates with a running RAG backend only through HTTP and never imports SovereignFlow code or reads its databases.

Execute generated queries:

```bash
python -m dataset_generator.evaluation run \
  --queries ./generated/queries.jsonl \
  --results ./evaluation/results.jsonl \
  --endpoint http://localhost:8000/v1/query \
  --timeout 30
```

An optional diagnostic key can be read from an environment variable:

```bash
export SOVEREIGNFLOW_DIAGNOSTIC_KEY="..."

python -m dataset_generator.evaluation run \
  --queries ./generated/queries.jsonl \
  --results ./evaluation/results.jsonl \
  --endpoint http://localhost:8000/v1/query \
  --diagnostic-key-env SOVEREIGNFLOW_DIAGNOSTIC_KEY
```

The key is sent as `X-SovereignFlow-Diagnostic-Key`. A production deployment must protect diagnostic retrieval traces with authentication and authorization.

## Diagnostic response contract

Exact retrieval and graph evaluation requires the backend response to include:

```json
{
  "answer": "Grounded answer",
  "citations": [],
  "pipeline_trace": [],
  "retrieval_trace": {
    "seed_nodes": [
      {
        "chunk_id": "Orders_000001_Service_V0002_0001",
        "source_id": "Orders_000001_Service",
        "domain": "Orders_000001",
        "tenant_id": "tenant_0001",
        "acl_labels": ["internal"],
        "classification_level": 1,
        "rank": 1
      }
    ],
    "graph_nodes": [],
    "relationship_types": ["calls"]
  }
}
```

The runner never infers retrieval nodes from generated model text. If `retrieval_trace` is absent, retrieval metrics are marked unavailable and any threshold that requires them fails.

## Offline analysis

Analyze saved results without repeating model calls:

```bash
python -m dataset_generator.evaluation analyze \
  --queries ./generated/queries.jsonl \
  --results ./evaluation/results.jsonl \
  --ground-truth ./generated/ground_truth.jsonl \
  --manifest ./generated/manifest.json \
  --thresholds ./thresholds.json \
  --out ./evaluation/report \
  --metrics-csv
```

Generated reports:

- `report.json` — deterministic machine-readable metrics;
- `report.md` — human-readable summary and grouped results;
- `failures.jsonl` — query-level failure reasons;
- `metrics.csv` — optional query-level metrics.

Measured areas include exact and concept-based retrieval, graph expansion, relationship coverage, citations, tenant and domain isolation, ACL, classification, errors, throughput, and latency percentiles.

Example acceptance thresholds:

```json
{
  "minimum_seed_recall": 0.9,
  "minimum_graph_recall": 0.95,
  "minimum_seed_concept_recall": 0.9,
  "minimum_graph_concept_recall": 0.95,
  "minimum_citation_coverage": 0.95,
  "maximum_forbidden_leaks": 0,
  "maximum_error_rate": 0.01,
  "maximum_p95_latency_ms": 2000
}
```

The analyzer exits with code `3` when acceptance thresholds fail and code `2` for invalid input or controlled evaluation errors.

## Out of scope

This project does not:

- import PostgreSQL data;
- import Weaviate data;
- calculate embeddings;
- execute SovereignFlow pipelines;
- modify production data;
- use fallback providers.
