# SovereignFlow — Vision and Architectural Principles

## What SovereignFlow Is

SovereignFlow is a **universal RAG engine** designed for building production-grade, domain-specific knowledge solutions. It is intentionally domain-agnostic — it has no knowledge of orders, customs decisions, or any other business concepts.

Its purpose is to answer one question:
> *"Given this knowledge and these permissions — what can we tell this user?"*

It does not know what that knowledge means. It knows how to retrieve it securely and deliver it.

---

## What SovereignFlow Is NOT

- Not a chatbot for a specific domain
- Not a wrapper around LangChain or LlamaIndex
- Not a content management system
- Not a solution built for a single client

---

## Layer Model

```
┌─────────────────────────────────────┐
│           Domain Solution           │  ← TaricAI, other clients
│   config/ + prompts/ + dataset/     │
│   (YAML and data only, no code)     │
├─────────────────────────────────────┤
│           SovereignFlow             │  ← this repository
│       RAG Engine + Security         │
├─────────────────────────────────────┤
│           Infrastructure            │
│  Weaviate · PostgreSQL · Keycloak   │
└─────────────────────────────────────┘
```

**Boundary rule:** a domain solution never modifies SovereignFlow source code. If it must — that is a signal that a generic feature is missing from SF and should be added there in a domain-neutral way.

---

## Architectural Principles

### 1. Hexagonal Architecture

Code is organized into four layers with one-way dependencies:

```
domain → application → infrastructure
                    → interfaces
```

- **domain** — engine business rules (not client domain), zero external dependencies
- **application** — pipeline orchestration, ports as interfaces
- **infrastructure** — adapters: Weaviate, PostgreSQL, HTTP, Ollama/OpenAI
- **interfaces** — Flask API, CLI

No layer imports from a layer above it.

### 2. Pipeline as Configuration

A pipeline is a list of actions defined in YAML. Execution logic belongs to the engine, not to the configuration.

```yaml
# pipelines/default.yaml — domain neutral
steps:
  - action: normalize_query
  - action: retrieve
  - action: manage_context_budget
  - action: call_model
  - action: finalize
```

A domain solution selects a pipeline through domain configuration — it does not write its own actions.

### 3. Actions as Protocol

Actions are defined by `Protocol` (duck typing), not by a base class. Each action:
- Lives in its own module
- Does not import other actions
- Receives `PipelineContext` and mutates it
- Has no knowledge of the client's domain

### 4. Security as a First-Class Citizen

Every request passes through verification:

```
JWT token → tenant_id + groups + acl_labels
         → AccessPolicyBundle
         → CapabilityDescriptor (which pipeline, which domain)
         → ACL filter at the Weaviate level
```

Security is not a configuration option — it is part of the flow. It cannot be disabled, only configured in its mode.

### 5. Clean Code Without Exceptions

- Zero fallbacks for scenarios that cannot occur
- Zero comments describing what code does (only why, when non-obvious)
- Changing code = remove the old, write the new — do not "improve" existing
- Validation only at system boundaries (user input, external APIs)

---

## What Belongs in SovereignFlow

| Belongs in SF | Does NOT belong in SF |
|---|---|
| Pipeline engine and actions | Domain-specific prompts |
| Security model (JWT, ACL, capabilities) | Client domain configuration |
| Conversation management | Data for import |
| LLM and embedding abstraction | Customs classification / order logic |
| Graph-expanded retrieval | Client data schemas |
| Multi-tenancy | Client business rules |
| Database migrations | Client external integrations |

---

## SF Vocabulary (Domain-Neutral)

SF knows only these concepts:

- **tenant** — isolated data space for one client
- **domain** — a knowledge collection accessible through a specific pipeline
- **node** — a unit of knowledge (document chunk)
- **edge** — a relationship between nodes (knowledge graph)
- **capability** — permission to query a specific domain with a specific pipeline
- **pipeline** — a sequence of actions leading from question to answer
- **action** — an atomic operation within a pipeline

SF **never** uses concepts like: order, BTI decision, product, CN code, customer, invoice.

---

## Warning Signs — When Something Goes Wrong

If any of the following appears during SF development — stop and reconsider:

- A class or function name contains a domain concept (e.g. `CustomsClassifier`, `OrderRetriever`)
- A pipeline action assumes something about data structure (e.g. "the `code` field contains a CN code")
- A domain config requires a change to SF source code
- An SF test mocks data from a specific client domain
- A prompt in `prompts/general/` mentions a specific industry

---

## Roadmap (Direction, Not Deadlines)

1. **Production deployment** — VPS, nginx, SSL, systemd ← *we are here*
2. **Observability** — structured logging, request tracing
3. **Streaming** — token-by-token responses
4. **Translation actions** — multilingual answers from foreign-language documents
5. **Iterative retrieval** — query expansion when initial results are weak
6. **Metrics** — Prometheus / Grafana

Each stage ends with a working, deployed system — not code waiting for deployment.
