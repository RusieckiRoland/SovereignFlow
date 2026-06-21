# ADR-0003: No Runtime Provider Fallback

**Status:** Accepted  
**Date:** 2026-06-13

## Context

It is tempting to build resilience by falling back to an alternative model provider when the primary one fails — e.g. fall back from OpenAI to a local Ollama instance if OpenAI times out. This appears to increase availability.

In a security-sensitive RAG system, however, the model server is part of the trust boundary. The configured model has been selected based on its security profile (`trust_boundary: external` or `trust_boundary: local`). A fallback to a different provider silently changes the trust boundary — potentially sending data to an external service when the operator intended local-only processing, or vice versa.

## Decision

One model endpoint is selected before a request. If that endpoint fails, the request fails. There is no automatic fallback.

Model servers are configured with an explicit `trust_boundary` field. The pipeline enforces transmission policy based on document classification labels and the model's trust boundary — this check is meaningless if the model can change at runtime.

## Consequences

**Positive:**
- Security policy is predictable and auditable. The model that processed a request is always the configured model.
- Execution audit records the exact model used per request.
- Operators understand exactly what happens on failure: the request fails with a clear error.

**Negative:**
- Availability is lower than a system with fallback. An OpenAI outage fails requests rather than routing to a local model.
- Operators must handle availability at the infrastructure level (retries, circuit breakers) rather than relying on automatic fallback.
