# ADR-0006: Single Secrets File with SF_ Prefix

**Status:** Accepted  
**Date:** 2026-06-14

## Context

The deployment stack has multiple components that need secrets: the application needs a Postgres connection URL and a Weaviate API key; Docker Compose needs the same Weaviate key to configure the container; Keycloak needs its admin password. Early versions used separate variable names per component, which led to the same secret being defined twice under different names (e.g. `WEAVIATE_API_KEY` for Docker and `SF_WEAVIATE_API_KEY` for the application).

## Decision

All secrets live in a single file: `/etc/sovereignflow/.env`. Every variable uses the `SF_` prefix. Docker Compose reads the same file via `--env-file /etc/sovereignflow/.env`. The application reads variables by name from the environment loaded from the same file via systemd `EnvironmentFile=`.

No secret appears in the file more than once. If both Docker Compose and the application need the same value, they both reference the same `SF_`-prefixed variable.

The file is owned by `root:sovereignflow`, mode `0600`. The application process runs as the `sovereignflow` system user with no shell.

## Consequences

**Positive:**
- One place to rotate a secret — no risk of updating one copy and forgetting another.
- The prefix makes it immediately clear which variables belong to this stack when inspecting the environment.
- No secret duplication means no divergence between what the application sees and what Docker sees.

**Negative:**
- All components share one file — a compromise of the file exposes all secrets simultaneously. Mitigation: strict file permissions and a dedicated system user.
- `--env-file` must be passed explicitly to every `docker compose` invocation; forgetting it causes container startup failures.
