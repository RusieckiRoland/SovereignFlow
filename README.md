# SovereignFlow

SovereignFlow is a domain-neutral foundation for Retrieval-Augmented Generation systems.

It is extracted from the architectural lessons of LocalAI-RAG, but it is not a code-analysis fork. The new core does not know about repositories, branches, source-code languages, Roslyn, SQL objects, UML, or code snapshots.

## Core principles

- **Domain-neutral core** — customs, legal, technical, internal knowledge, and future assistants use the same backend.
- **Provider-independent AI** — generation and embeddings use explicit HTTP service contracts.
- **No runtime provider fallback** — one model endpoint is selected before a request; its failure fails the request.
- **Evidence before answers** — responses retain citations and source metadata.
- **PostgreSQL-backed execution audit** — pipeline runs, completed steps, results, and safe failures are stored durably.
- **Weaviate for retrieval** — semantic, keyword, and hybrid search use generic document chunks.
- **Domain behavior through profiles** — prompts, disclaimers, collections, filters, and retrieval defaults live outside the core.
- **Versioned pipeline behavior** — YAML steps pin action behavior versions and are validated before startup.
- **Durable execution audit** — PostgreSQL stores every run, completed step, result, and safe failure.

## What belongs outside the core

Domain repositories such as TaricAI provide:

- source-specific importers,
- relational schemas,
- synchronization state,
- domain validation,
- domain prompts and policies,
- application-specific API/UI code.

They depend on SovereignFlow rather than copy it.

PostgreSQL storage for domain records and synchronization state belongs to those domain packages. The current SovereignFlow schema stores pipeline execution audit only.

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

The `DocumentChunk` contract and retrieval adapter already exist. Stage 2 does not yet provide a generic ingestion API or automatic Weaviate collection creation.

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

Audit reads are tenant-scoped. No unauthenticated audit endpoint is exposed.

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
- the configured Weaviate collections,
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

### 5. Create the configured Weaviate collection

SovereignFlow currently validates collection existence but does not create or migrate collections automatically.

For the example domain, an administrator or domain importer must create a collection named `SovereignFlowGeneral` before starting the API. It must use self-provided vectors and contain these properties:

```text
chunk_id
domain
tenant_id
source_id
source_uri
text
metadata_json
acl_labels
classification_level
```

The property names must match exactly. `metadata_json` stores a serialized JSON object. The collection must be populated with vectors produced by the same embedding model configured for query embeddings.

Automatic collection migrations and generic document ingestion are planned for the next stage.

An empty collection is sufficient for the startup health check, but meaningful queries require indexed objects and vectors.

### 6. Start model and embedding services

Start the services configured in:

```yaml
models:
embeddings:
```

SovereignFlow expects OpenAI-compatible endpoints:

- model health and generation through `/models` and `/chat/completions`,
- embedding health and generation through `/models` and `/embeddings`.

There is no fallback provider. If the selected service is unavailable, startup or the request fails.

### 7. Start SovereignFlow

```bash
python -m sovereignflow --config config/sovereignflow.yaml
```

The CLI runs Flask through Waitress. Before opening the HTTP API it:

1. applies checksummed PostgreSQL migrations,
2. loads and validates every configured pipeline,
3. validates prompt files and Weaviate collections,
4. checks PostgreSQL, Weaviate, embeddings, and the selected model.

If any required dependency or contract is invalid, startup fails.

Runtime endpoints:

- `GET /live` — process liveness,
- `GET /ready` — dependency readiness,
- `POST /v1/query` — versioned RAG query API.

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

## Testing

Run the complete unit and protocol-integration test suite with branch coverage:

```bash
python -m pytest --cov=sovereignflow --cov-branch --cov-report=term-missing
```

The project enforces 100% statement and branch coverage.

Run integration tests that do not require PostgreSQL:

```bash
python -m pytest -m integration
```

To include the real PostgreSQL audit integration test, start PostgreSQL and provide its URL:

```bash
export WEAVIATE_API_KEY='not-used-when-starting-only-postgres'
export POSTGRES_PASSWORD='test-password'
export SOVEREIGNFLOW_POSTGRES_PORT=25432
docker compose up -d postgres

export SOVEREIGNFLOW_TEST_POSTGRES_URL='postgresql://sovereignflow:test-password@127.0.0.1:25432/sovereignflow'
python -m pytest -m integration

docker compose down
```

Run static quality checks:

```bash
ruff check sovereignflow tests
ruff format --check sovereignflow tests
python -m compileall -q sovereignflow tests
```

## Current limitations

Stage 2 intentionally does not yet include:

- generic document ingestion,
- automatic Weaviate collection creation or migration,
- graph relationship storage and traversal,
- authenticated execution-history API,
- domain-specific PostgreSQL schemas,
- domain synchronization workers,
- model or embedding fallbacks.

## Status

Stage 2 is complete: the reusable foundation now includes a contract-validated pipeline engine and PostgreSQL-backed execution audit. The next milestones are ingestion, explicit Weaviate collection migrations, graph relationships, and the first domain package.
