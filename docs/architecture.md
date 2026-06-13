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

## Security rule

External transmission is denied by configuration, not by convention. Configuration selects exactly one model endpoint before startup. If that endpoint fails, the request fails; SovereignFlow does not try another provider.

Weaviate anonymous access is disabled. Tenant, ACL, and classification boundaries are applied in retrieval and verified again before evidence is sent to the model.
