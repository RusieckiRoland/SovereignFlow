# SovereignFlow

SovereignFlow is a domain-neutral, local-first foundation for Retrieval-Augmented Generation systems.

It is extracted from the architectural lessons of LocalAI-RAG, but it is not a code-analysis fork. The new core does not know about repositories, branches, source-code languages, Roslyn, SQL objects, UML, or code snapshots.

## Core principles

- **Domain-neutral core** — customs, legal, technical, internal knowledge, and future assistants use the same backend.
- **Local-first AI** — embeddings and generation can run entirely on infrastructure controlled by the operator.
- **External providers are optional** — policy decides whether an external model may be used.
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

## Local/external model policy

SovereignFlow exposes three routing modes:

- `local_only` — never send prompts to an external endpoint,
- `prefer_local` — use local AI first and optionally fall back,
- `external_allowed` — use the configured endpoint order.

Endpoint scope is declared explicitly as `local` or `external`; it is not guessed from the URL.

## Repository layout

```text
sovereignflow/       reusable Python package
config/domains/      domain profiles
pipelines/           configurable RAG workflows
prompts/             neutral prompt templates
tests/               core contract tests
docs/                architecture and extraction decisions
```

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The initial repository contains an in-memory backend for contract tests and local development. Weaviate and OpenAI-compatible model adapters are included as infrastructure ports.

## Status

This is the first extraction of the reusable foundation. The next implementation milestones are PostgreSQL-backed history/audit, production bootstrap configuration, collection migrations, and the first domain package for TaricAI.

