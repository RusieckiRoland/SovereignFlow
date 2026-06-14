# SovereignFlow

SovereignFlow is a domain-neutral foundation for Retrieval-Augmented Generation systems.

It is extracted from the architectural lessons of LocalAI-RAG, but it is not a code-analysis fork. The new core does not know about repositories, branches, source-code languages, Roslyn, SQL objects, UML, or code snapshots.

## Core principles

- **Domain-neutral core** — customs, legal, technical, internal knowledge, and future assistants use the same backend.
- **Provider-independent AI** — generation and embeddings use explicit HTTP service contracts.
- **No runtime provider fallback** — one model endpoint is selected before a request; its failure fails the request.
- **Evidence before answers** — responses retain citations and source metadata.
- **PostgreSQL-backed execution audit** — pipeline runs, completed steps, results, and safe failures are stored durably.
- **Durable operational metrics** — quality, latency, evidence, provider token usage, and configured cost estimates come from PostgreSQL audit records.
- **Authenticated administration** — execution history, metrics, job inspection, and explicit retry are protected and tenant-scoped.
- **Transactional document ingestion** — source versions, chunks, idempotency keys, and indexing jobs are committed atomically.
- **Versioned graph relationships** — document relations follow source versions and are traversed with explicit safety limits.
- **Weaviate for retrieval** — semantic, keyword, and hybrid search use generic document chunks.
- **Domain behavior through profiles** — prompts, disclaimers, collections, filters, and retrieval defaults live outside the core.
- **Versioned pipeline behavior** — YAML steps pin action behavior versions and are validated before startup.

## What belongs outside the core

Domain repositories such as TaricAI provide:

- source-specific importers,
- domain-specific relational schemas,
- synchronization state,
- domain validation,
- domain prompts and policies,
- application-specific API/UI code.

They depend on SovereignFlow rather than copy it.

PostgreSQL storage for domain records and synchronization state belongs to those domain packages. SovereignFlow stores only neutral execution-audit and document-ingestion records.

## Generic data model

Every indexed item becomes a `DocumentChunk`:

```text
chunk_id
domain
tenant_id
source_id
source_uri
text
metadata
acl_labels
classification_level
```

No field is specific to source code or customs classification.

Domain importers submit immutable `IngestionCommand` objects containing already prepared chunks. SovereignFlow does not guess source parsing or chunking rules.

The ingestion service:

1. validates tenant, domain, ACL, and classification boundaries;
2. atomically stores the source version, chunks, idempotency key, and indexing job in PostgreSQL;
3. calculates embeddings through the configured embedding service;
4. replaces the source version in Weaviate;
5. marks the PostgreSQL source pointer as current only after indexing succeeds.

Failed indexing jobs remain explicit and can be retried by job identifier. No hidden fallback or silent data loss is permitted.

Large neutral JSONL datasets can be imported with the separate
`sovereignflow-import` CLI. The importer stages data without loading the complete
dataset into memory, publishes relationships after source indexing, records durable
progress, supports safe resumption, and exposes an explicit consistency check.
See `docs/dataset-import.md`.

## Graph relationships

Domain importers may attach neutral `GraphRelationship` records to an ingestion command. A relationship connects two document chunks using source and chunk identifiers plus a domain-defined relationship type.

Relationships:

- belong to the ingested source version;
- become active only when that source version becomes current;
- require an existing target chunk for cross-source links;
- are persisted in PostgreSQL, not hidden inside vector metadata;
- are included in the canonical ingestion hash.

The `expand_graph` pipeline action performs bounded breadth-first traversal after vector or keyword retrieval. Domain configuration explicitly controls:

- whether expansion is enabled;
- outgoing, incoming, or bidirectional traversal;
- maximum depth;
- maximum number of added nodes;
- an optional relationship-type allowlist.

Each traversal query is tenant- and domain-scoped. Target chunks are loaded only from current source versions and must satisfy ACL and classification policies before entering model context.

## Local/external model selection

Configuration selects exactly one model endpoint. Endpoint scope is declared explicitly as `local` or `external`; it is not guessed from the URL. A domain must explicitly allow external transmission before an external endpoint can be selected.

## Pipeline execution

Each domain selects a YAML pipeline by name. Pipeline definitions declare:

- a behavior version,
- an entry step,
- a hard maximum path length,
- explicit action behavior versions,
- default transitions and optional named routes,
- terminal steps.

The validator rejects unknown actions, version mismatches, broken transitions, cycles, unreachable steps, missing state contracts, paths exceeding the configured limit, and terminal paths that do not produce a result.

There is no implicit action fallback. A routing action must return a route explicitly declared by its step.

## PostgreSQL execution audit

Database migrations run before the HTTP server opens. Migration checksums are recorded in `public.sovereignflow_schema_migrations`; modifying an already applied migration prevents startup.

Every query records:

- run, request, session, tenant, and domain identifiers,
- pipeline name, behavior version, and SHA-256 checksum,
- each completed step and its action behavior version,
- duration and selected transition,
- final status, answer, citation count, or safe error information.
- provider-reported prompt and completion token counts;
- estimated cost calculated from explicit model pricing configuration.

Audit reads are tenant-scoped and available only through the authenticated operations API.

## Repository layout

```text
sovereignflow/
  domain/            technology-independent entities and errors
  application/       use cases and ports
  infrastructure/    HTTP, PostgreSQL and Weaviate adapters
    migrations/      checksummed PostgreSQL schema migrations
  interfaces/        HTTP API
  bootstrap/         configuration and composition root
config/domains/      domain profiles
prompts/             neutral prompt templates
pipelines/           versioned domain-neutral workflow definitions
tests/               core contract tests
docs/                architecture and extraction decisions
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest --cov=sovereignflow --cov-branch
```

## Running the server

SovereignFlow has no in-memory or fake runtime mode. The server starts only when all required dependencies are available:

- PostgreSQL,
- authenticated Weaviate,
- one selected model endpoint,
- one embedding endpoint,
- prompt files,
- domain profiles,
- valid pipeline definitions.

Model generation and embedding calculation are performed by separate HTTP services. They may be local services or external providers exposing compatible endpoints.

### 1. Install SovereignFlow

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

When using Conda instead:

```bash
conda activate rag-weaviate
python -m pip install -e ".[dev]"
```

### 2. Create the runtime configuration

```bash
cp config/sovereignflow.example.yaml config/sovereignflow.yaml
```

Edit `config/sovereignflow.yaml` and provide:

- a reachable OpenAI-compatible embedding endpoint,
- a reachable Weaviate host and ports,
- one selected local or external OpenAI-compatible model endpoint,
- the domain profiles that should be exposed,
- the pipeline directory and pipeline selected by each domain.
- per-million-token input and output prices for the selected model;
- the environment variable containing the administrative API key.

The example configuration expects:

- model generation at `http://127.0.0.1:8080/v1`,
- embeddings at `http://127.0.0.1:8082/v1`,
- PostgreSQL at `127.0.0.1:15432`,
- Weaviate HTTP at `127.0.0.1:18080`,
- Weaviate gRPC at `127.0.0.1:15005`.

Change these values to match your environment.

### 3. Set required secrets

Use the same PostgreSQL password in `POSTGRES_URL` and Docker Compose.

```bash
export POSTGRES_PASSWORD='replace-with-a-long-random-secret'
export POSTGRES_URL='postgresql://sovereignflow:replace-with-a-long-random-secret@127.0.0.1:15432/sovereignflow'
export WEAVIATE_API_KEY='replace-with-a-long-random-secret'
export SOVEREIGNFLOW_ADMIN_API_KEY='replace-with-a-separate-long-random-secret'
```

If the password contains URL-special characters, percent-encode it in `POSTGRES_URL`.

For an external model provider, also export the environment variable referenced by its `api_key_env` field.

### 4. Start PostgreSQL and Weaviate

```bash
docker compose up -d postgres weaviate
```

Verify the containers:

```bash
docker compose ps
```

### 5. Weaviate collection migrations

SovereignFlow creates missing configured collections during startup and validates existing schemas exactly. Collections use self-provided vectors and these properties:

```text
chunk_id
domain
tenant_id
source_id
source_version
source_uri
text
metadata_json
acl_labels
classification_level
```

The property names and types must match exactly. Schema drift prevents startup. `metadata_json` stores a serialized JSON object, while vectors are produced by the configured embedding service.

An empty collection is valid, but meaningful queries require a domain importer to submit ingestion commands.

### 6. Start model and embedding services

Start the services configured in:

```yaml
models:
embeddings:
```

SovereignFlow expects OpenAI-compatible endpoints:

- model health and generation through `/models` and `/chat/completions`,
- embedding health and generation through `/models` and `/embeddings`.

The chat-completion response must include `usage.prompt_tokens` and `usage.completion_tokens`. Missing usage is a protocol error. SovereignFlow does not estimate token counts from text.

There is no fallback provider. If the selected service is unavailable, startup or the request fails.

### 7. Start SovereignFlow

```bash
python -m sovereignflow --config config/sovereignflow.yaml
```

The CLI runs Flask through Waitress. Before opening the HTTP API it:

1. applies checksummed PostgreSQL migrations,
2. loads and validates every configured pipeline,
3. creates or validates Weaviate collections and prompt files,
4. checks PostgreSQL, Weaviate, embeddings, and the selected model.

If any required dependency or contract is invalid, startup fails.

Runtime endpoints:

- `GET /live` — process liveness,
- `GET /ready` — dependency readiness,
- `POST /v1/query` — versioned RAG query API.
- `GET /v1/admin/executions/{request_id}` — authenticated execution details.
- `GET /v1/admin/metrics` — authenticated operational metrics.
- `GET /v1/admin/ingestion/jobs/{job_id}` — authenticated job inspection.
- `POST /v1/admin/ingestion/jobs/{job_id}/retry` — authenticated explicit retry.

All administrative endpoints require:

```text
X-SovereignFlow-Admin-Key: <configured secret>
tenant_id=<explicit tenant>
```

The full contract is documented in `docs/operations-api.md`.

### 8. Verify health

```bash
curl --fail http://127.0.0.1:8000/live
curl --fail http://127.0.0.1:8000/ready
```

Expected readiness response:

```json
{
  "ok": true,
  "components": {
    "postgresql": "ready",
    "execution_audit": "ready",
    "ingestion_repository": "ready",
    "weaviate": "ready",
    "embeddings": "ready",
    "model": "ready",
    "retrieval:general": "ready"
  }
}
```

### 9. Submit a query

```bash
curl --fail-with-body \
  -X POST http://127.0.0.1:8000/v1/query \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: example-request-001' \
  -d '{
    "query": "What does the indexed source say?",
    "domain": "general",
    "session_id": "example-session-001",
    "filters": {}
  }'
```

The response contains:

- the generated answer,
- source citations,
- the executed pipeline trace,
- request, domain, and session identifiers.

The execution and each completed pipeline step are also recorded in PostgreSQL.

### 10. Inspect operational metrics

```bash
curl --fail-with-body \
  'http://127.0.0.1:8000/v1/admin/metrics?tenant_id=tenant-a&hours=24' \
  -H "X-SovereignFlow-Admin-Key: ${SOVEREIGNFLOW_ADMIN_API_KEY}"
```

## Testing

Run the complete unit and protocol-integration test suite with branch coverage:

```bash
python -m pytest --cov=sovereignflow --cov-branch --cov-report=term-missing
```

The project enforces 100% statement and branch coverage.

Run protocol integration tests; tests requiring external services are skipped unless configured:

```bash
python -m pytest -m integration
```

To include the real PostgreSQL and Weaviate ingestion tests, start both services and provide their connection settings:

```bash
export WEAVIATE_API_KEY='test-weaviate-key'
export POSTGRES_PASSWORD='test-password'
export SOVEREIGNFLOW_POSTGRES_PORT=25432
export SOVEREIGNFLOW_WEAVIATE_HTTP_PORT=28080
export SOVEREIGNFLOW_WEAVIATE_GRPC_PORT=25005
docker compose up -d postgres weaviate

export SOVEREIGNFLOW_TEST_POSTGRES_URL='postgresql://sovereignflow:test-password@127.0.0.1:25432/sovereignflow'
export SOVEREIGNFLOW_TEST_WEAVIATE_HOST='127.0.0.1'
export SOVEREIGNFLOW_TEST_WEAVIATE_HTTP_PORT=28080
export SOVEREIGNFLOW_TEST_WEAVIATE_GRPC_PORT=25005
export SOVEREIGNFLOW_TEST_WEAVIATE_API_KEY="$WEAVIATE_API_KEY"
python -m pytest -m integration

docker compose down
```

Run static quality checks:

```bash
ruff check sovereignflow tests
ruff format --check sovereignflow tests
python -m compileall -q sovereignflow tests
```

## Current boundaries

The reusable foundation intentionally does not include:

- domain-specific PostgreSQL schemas,
- domain synchronization workers,
- source-specific parsing or chunking,
- a public ingestion endpoint without authenticated service identity,
- dedicated graph databases or graph-query languages,
- asynchronous ingestion workers,
- model or embedding fallbacks.

## Status

Stage 5 is complete. SovereignFlow 1.0 provides the professional domain-neutral foundation extracted from LocalAI-RAG: clean architectural boundaries, versioned pipelines, durable ingestion, Weaviate retrieval, PostgreSQL graph expansion, explicit model and embedding services, execution audit, operational metrics, and authenticated tenant-scoped administration.

The next work belongs to domain packages such as TaricAI and to optional platform evolution that preserves these public contracts.
