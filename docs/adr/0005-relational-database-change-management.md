# ADR-0005: Relational Database Change Management via Hash-Verified Sequential Scripts

**Status:** Accepted  
**Date:** 2026-06-21

## Context

The application uses PostgreSQL for execution audit, conversation history, access policies, and operational metrics. The schema evolves as new capabilities are added. Changes must be applied exactly once, in order, on every environment — development, staging, production.

Two categories of change scripts exist:
- **SF changes** — infrastructure-level changes to the SovereignFlow foundation (e.g. creating the Keycloak database, schema for core tables).
- **Domain changes** — business-domain changes specific to the deployed domain solution (e.g. custom schema extensions for a particular tenant).

These two categories must not be mixed: SF changes are owned by the SovereignFlow team; domain changes are owned by the domain solution team.

## Decision

**Application schema migrations** (`sovereignflow/infrastructure/migrations/*.sql`) are numbered, checksummed SQL files run automatically on application startup via `MigrationRunner`. A migration that has already been applied is skipped. A migration whose checksum has changed after application raises an error and blocks startup.

**Infrastructure change scripts** (`scripts/sf/changes/`, `scripts/domain/changes/`) are numbered bash scripts run manually via `apply-changes.sh`. Applied scripts are tracked by a `.done` marker file and a `.sha256` hash file stored in `/var/lib/sovereignflow/applied-changes/{sf,domain}/`. If an applied script is found with a mismatched hash, the runner fails with an explicit error — the operator must write a new script instead of modifying an applied one.

**Idempotent setup scripts** (`scripts/sf/setup.sh`, `scripts/domain/setup.sh`) configure cron jobs, systemd units, and directories. They are re-run on every deploy and must produce the same result regardless of how many times they are invoked.

## Consequences

**Positive:**
- Schema changes are auditable and reproducible across environments.
- A modified applied script is caught immediately — no silent divergence between code and database state.
- SF and domain changes are independently versioned and owned.
- Idempotent setup scripts make re-deployment safe without manual state tracking.

**Negative:**
- Tracking via filesystem markers is lost if the server is rebuilt from scratch. On a fresh server, all scripts run again (which is correct, but the tracking history is not portable).
- A tool like Flyway would store tracking in the database itself, making it survive server rebuilds. This is a known limitation accepted in exchange for zero external dependencies.
