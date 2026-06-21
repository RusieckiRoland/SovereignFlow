# ADR-0002: Pipeline-as-Config with Protocol-Based Actions

**Status:** Accepted  
**Date:** 2026-06-13

## Context

RAG query processing requires a sequence of steps: normalise query, retrieve documents, enforce security policy, call model, finalise response. Different domains and security profiles need different step sequences. Hard-coding these sequences in Python would require code changes for every variation.

A base class hierarchy for actions was considered but rejected — it couples all action implementations to a shared ancestor, makes the dependency graph opaque, and introduces the fragile base class problem.

## Decision

Pipelines are defined in YAML files and loaded at runtime. Each step names a registered action class. The pipeline engine resolves and executes steps in order, passing a mutable context object between them.

Actions are identified by a `Protocol` (structural subtyping / duck typing). Any class that implements `execute(context) -> None` and `configure(config) -> None` is a valid action — no inheritance required. The engine validates conformance at registration time, not at runtime.

```yaml
# pipelines/default.yaml
steps:
  - action: NormalizeQuery
  - action: Retrieve
  - action: EnforceModelTransmissionPolicy
  - action: CallModel
  - action: Finalize
```

## Consequences

**Positive:**
- New pipelines require only YAML — no Python code changes.
- Actions are independently testable units with no shared state.
- Protocol duck typing keeps action implementations decoupled from the engine.
- Domain-specific pipelines can override or extend the default without modifying the core.

**Negative:**
- Pipeline errors surface at runtime (misconfigured action name, wrong config key) rather than at import time.
- Configuration validation must be explicit inside each action's `configure()` method.
