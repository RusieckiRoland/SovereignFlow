# SovereignFlow

SovereignFlow is a domain-neutral foundation for Retrieval-Augmented Generation systems.

It is extracted from the architectural lessons of LocalAI-RAG, but it is not a code-analysis fork. The new core does not know about repositories, branches, source-code languages, Roslyn, SQL objects, UML, or code snapshots.

## Core principles

- **Domain-neutral core** — customs, legal, technical, internal knowledge, and future assistants use the same backend.
- **Provider-independent AI** — generation and embeddings use explicit HTTP service contracts.
- **No runtime provider fallback** — one model endpoint is selected before a request; its failure fails the request.
- **Evidence before answers** — responses retain citations and source metadata.
- **PostgreSQL as source of truth** — domain records, synchronization state, and audit/history belong in relational storage.
- **Weaviate for retrieval** — semantic, keyword, and hybrid search use generic document chunks.
- **Domain behavior through profiles** — prompts, disclaimers, collections, filters, and retrieval defaults live outside the core.

## What belongs outside the core

Domain repositories such as TaricAI provide:

- source-specific importers,
- relational schemas,
- domain validation,
- domain prompts and policies,
- application-specific API/UI code.

They depend on SovereignFlow rather than copy it.

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

## Local/external model selection

Configuration selects exactly one model endpoint. Endpoint scope is declared explicitly as `local` or `external`; it is not guessed from the URL. A domain must explicitly allow external transmission before an external endpoint can be selected.

## Repository layout

```text
sovereignflow/
  domain/            technology-independent entities and errors
  application/       use cases and ports
  infrastructure/    HTTP, PostgreSQL and Weaviate adapters
  interfaces/        HTTP API
  bootstrap/         configuration and composition root
config/domains/      domain profiles
prompts/             neutral prompt templates
tests/               core contract tests
docs/                architecture and extraction decisions
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest --cov=sovereignflow
```

## Running the server

SovereignFlow has no in-memory or fake runtime mode. The server starts only when PostgreSQL, authenticated Weaviate, the selected model endpoint, the embedding endpoint, prompts, and all configured collections are available.

```bash
cp config/sovereignflow.example.yaml config/sovereignflow.yaml
```

Edit `config/sovereignflow.yaml` and provide:

- a reachable OpenAI-compatible embedding endpoint,
- a reachable Weaviate host and ports,
- one selected local or external OpenAI-compatible model endpoint,
- the domain profiles that should be exposed.

Set the required secrets:

```bash
export POSTGRES_URL='postgresql://sovereignflow:change-me@127.0.0.1:15432/sovereignflow'
export WEAVIATE_API_KEY='replace-with-a-long-random-secret'
```

Start the infrastructure:

```bash
docker compose up -d postgres weaviate
```

Start SovereignFlow:

```bash
python -m sovereignflow --config config/sovereignflow.yaml
```

The CLI runs Flask through Waitress. If any required provider is missing or unavailable, startup fails before the HTTP API is opened.

Runtime endpoints:

- `GET /live` — process liveness,
- `GET /ready` — dependency readiness,
- `POST /v1/query` — versioned RAG query API.

## Status

This is the first extraction of the reusable foundation. The next implementation milestones are PostgreSQL-backed history/audit, explicit collection migrations, and the first domain package for TaricAI.
