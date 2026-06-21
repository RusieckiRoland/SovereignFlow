# ADR-0001: Hexagonal Architecture with Domain-Neutral Core

**Status:** Accepted  
**Date:** 2026-06-13

## Context

SovereignFlow is designed to be a reusable RAG foundation across multiple business domains — customs law, legal research, internal knowledge bases, and others. The risk of coupling the core engine to any specific domain (e.g. document types, business rules, terminology) would make it impossible to reuse without modification.

At the same time, the engine must integrate with external systems: PostgreSQL, Weaviate, Keycloak, OpenAI, Ollama. These integrations are infrastructure concerns and must be replaceable without touching business logic.

## Decision

Adopt hexagonal architecture (ports and adapters) with three explicit layers:

- **`domain/`** — pure Python dataclasses and errors. No imports from application, infrastructure, or any framework. Defines the vocabulary of the system.
- **`application/`** — use cases, pipeline engine, action protocol. Depends only on `domain/` and abstract ports. No I/O.
- **`infrastructure/`** — concrete adapters for PostgreSQL, Weaviate, HTTP, OIDC. Implements ports defined in `application/`.
- **`interfaces/`** — HTTP API, CLI. Thin layer translating HTTP/CLI to application calls.
- **`bootstrap/`** — dependency injection. Wires infrastructure adapters to application ports.

Domain neutrality is enforced structurally: `domain/` contains no business-domain concepts. Domains are configured via YAML files loaded at runtime.

## Consequences

**Positive:**
- The same engine runs customs, legal, or any other domain by swapping YAML config.
- Infrastructure can be replaced (e.g. swap Weaviate for another vector DB) without touching application logic.
- Domain layer is trivially testable without any I/O.
- Architecture violations are detectable by import analysis (`test_architecture.py`).

**Negative:**
- More files and layers than a typical Flask application.
- New contributors must understand the port/adapter pattern before making changes.
