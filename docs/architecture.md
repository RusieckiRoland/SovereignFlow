# SovereignFlow architecture

## Boundary

SovereignFlow owns reusable RAG mechanics:

- vertical query orchestration,
- retrieval, embedding and model ports,
- explicit local/external model selection,
- evidence assembly,
- citations,
- security metadata,
- API contracts,
- versioned YAML pipeline definitions,
- action contracts and deterministic routing,
- durable execution history and step audit,
- versioned, idempotent document ingestion,
- bounded traversal of neutral document relationships.
- authenticated, tenant-scoped operational administration;
- durable quality, latency, token-usage, and cost metrics.

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

The adapter stores run identity, tenant boundary, pipeline checksum, completed steps, selected transitions, durations, final output metadata, provider-reported token usage, configured cost estimates, and safe error details. Schema migrations are bundled with the package, serialized with a PostgreSQL advisory lock, and protected by SHA-256 checksums.

The application records the run before executing the first action and records completion or failure explicitly. Audit failure is a request failure because silently losing execution evidence would violate the platform contract.

## Document ingestion

Source-specific parsing and chunking remain in domain projects. They submit neutral, immutable `IngestionCommand` objects through the application service.

PostgreSQL is the ingestion source of truth. A single transaction stores:

- source-version identity and metadata;
- ordered document chunks;
- an idempotency key and canonical payload hash;
- an explicit indexing job.

The application then requests batch embeddings and replaces the source in the configured Weaviate collection. Only after successful vector indexing does PostgreSQL advance the current source-version pointer. A failed or interrupted job remains durable and requires an explicit retry.

PostgreSQL and Weaviate do not support one distributed transaction. SovereignFlow therefore uses an explicit job state machine and idempotent source replacement rather than pretending that cross-database atomicity exists.

Weaviate collection schemas are created or verified during startup. Any property or type drift is a startup failure; there is no best-effort schema fallback.

## Graph relationships

Graph relationships are domain-neutral links between document chunks. They are stored in PostgreSQL and owned by the version of the source that declared them.

This ownership model provides deterministic activation:

1. ingestion stores chunks and relationships in one PostgreSQL transaction;
2. Weaviate indexes the source version;
3. PostgreSQL advances the current source pointer;
4. only relationships owned by the current version participate in traversal.

Cross-source targets must already exist in the current graph. Internal targets must belong to the same ingestion command. Duplicate relationships and dangling targets are rejected.

The graph adapter does not load the entire tenant graph into memory. It performs bounded breadth-first expansion one depth level at a time. Every query is constrained by:

- tenant and domain;
- traversal direction;
- relationship-type allowlist;
- maximum depth;
- maximum number of expanded nodes;
- current source versions;
- ACL subset policy;
- classification ceiling.

The pipeline performs vector or keyword retrieval first. Retrieved chunks become graph seeds, and the explicit `expand_graph` action appends permitted related chunks before context construction. Graph evidence retains depth and relationship-path metadata.

## Security rule

External transmission is denied by configuration, not by convention. Configuration selects exactly one model endpoint before startup. If that endpoint fails, the request fails; SovereignFlow does not try another provider.

Weaviate anonymous access is disabled. Tenant, ACL, and classification boundaries are applied in retrieval and verified again before evidence is sent to the model.

Execution-history, metrics, ingestion-job inspection, and ingestion retry are exposed only through the administrative API. Every administrative request requires:

- the configured `X-SovereignFlow-Admin-Key`;
- an explicit `tenant_id`;
- tenant-scoped repository queries.

The key is compared in constant time. Missing or invalid authentication fails with a stable `401` response. Tenant isolation is enforced in the application and persistence layers rather than inferred from request data.

## Operational metrics

Operational metrics are calculated from durable PostgreSQL audit records. SovereignFlow does not maintain a second in-memory statistics source.

For a requested tenant and time window the API reports:

- execution, success, and failure counts;
- success rate and average execution duration;
- average citation count and successful runs without evidence;
- prompt, completion, and total token counts;
- estimated model cost;
- action-level execution counts and average durations.

Model providers must return explicit `prompt_tokens` and `completion_tokens`. Missing or malformed usage is a provider-protocol failure. Cost is derived only from the configured per-million-token prices; it is never guessed from text length.
