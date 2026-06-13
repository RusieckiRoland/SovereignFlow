# SovereignFlow architecture

## Boundary

SovereignFlow owns reusable RAG mechanics:

- pipeline execution,
- retrieval and model ports,
- local/external model routing policy,
- generic chunk ingestion,
- evidence assembly,
- citations,
- security metadata,
- API contracts.

Domain projects own source semantics and business rules.

## Extraction from LocalAI-RAG

Retained ideas:

- configurable workflows,
- explicit runtime dependency injection,
- hybrid retrieval,
- local model support,
- security-aware routing,
- traceable evidence.

Intentionally removed:

- repository, branch, and source-code snapshot state,
- code symbol and dependency-graph actions,
- Roslyn and SQL/.NET summarizers,
- code-specific query classification,
- UML and Enterprise Architect commands,
- code-oriented prompts and model defaults.

## Dependency direction

```text
TaricAI importer/API ──> SovereignFlow contracts
Other domain app     ──> SovereignFlow contracts

SovereignFlow ──> PostgreSQL / Weaviate / local or external model adapters
```

A domain project may register new actions and metadata, but it must not require changes to the generic state model.

## Security rule

External transmission is denied by configuration, not by convention. A model endpoint declares its scope, and the router enforces the selected policy before a prompt is sent.

