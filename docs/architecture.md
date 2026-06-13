# SovereignFlow architecture

## Boundary

SovereignFlow owns reusable RAG mechanics:

- vertical query orchestration,
- retrieval, embedding and model ports,
- explicit local/external model selection,
- evidence assembly,
- citations,
- security metadata,
- API contracts.
- versioned YAML pipeline definitions,
- action contracts and deterministic routing,
- durable execution history and step audit.

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

## Clean Architecture dependency direction

```text
interfaces -> application -> domain
infrastructure -> application ports
bootstrap -> all layers

TaricAI and other domains -> public SovereignFlow contracts
```

The domain layer imports no infrastructure framework or SDK. The application layer depends only on domain types and application ports.

## Pipeline contract

Pipeline YAML is infrastructure input converted into immutable domain definitions. Before a pipeline is accepted, the application validator checks:

- unique step identifiers;
- known action identifiers and exact behavior-version matches;
- valid default and named-route targets;
- absence of cycles and unreachable steps;
- action preconditions along every possible path;
- the configured maximum path length;
- a result-producing terminal step on every path.

Actions may return a named routing decision. The engine only follows routes declared in the current step. Unknown or missing decisions fail the run; no default provider or action substitution occurs.

## Execution audit

`ExecutionAuditPort` belongs to the application boundary. PostgreSQL implements it through a parameterized adapter.

The adapter stores run identity, tenant boundary, pipeline checksum, completed steps, selected transitions, durations, final output metadata, and safe error details. Schema migrations are bundled with the package, serialized with a PostgreSQL advisory lock, and protected by SHA-256 checksums.

The application records the run before executing the first action and records completion or failure explicitly. Audit failure is a request failure because silently losing execution evidence would violate the platform contract.

## Security rule

External transmission is denied by configuration, not by convention. Configuration selects exactly one model endpoint before startup. If that endpoint fails, the request fails; SovereignFlow does not try another provider.

Weaviate anonymous access is disabled. Tenant, ACL, and classification boundaries are applied in retrieval and verified again before evidence is sent to the model.

Execution-history reads require an explicit tenant identifier. SovereignFlow does not expose history over HTTP until an authenticated identity boundary is implemented.
