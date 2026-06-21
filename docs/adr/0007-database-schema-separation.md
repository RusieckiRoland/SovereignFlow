# ADR-0007: SF and Domain Data in Separate PostgreSQL Schemas

**Status:** Accepted (Implemented)  
**Date:** 2026-06-21

## Context

SovereignFlow is a domain-neutral foundation. Domain solutions (e.g. `regulations`, `orders`) are tenants of that foundation — they add their own tables, their own data lifecycle, and their own migration scripts. Without explicit separation, SF tables and domain tables coexist in the `public` schema with no ownership boundary. This creates the risk of:

- Domain migrations accidentally modifying SF tables.
- SF upgrades colliding with domain-specific table names.
- Unclear ownership when reading the schema — which tables belong to the foundation and which to the business layer.

The question was whether to separate by schema (same database) or by database (separate instances).

## Decision

**Separate schemas within the same PostgreSQL database.**

```
database: sovereignflow
  schema: sf      ← foundation tables (audit, policies, conversations, migrations tracking)
  schema: <domain> ← domain-specific tables, named after the domain (e.g. regulations)
```

Each schema has a dedicated owner:
- `sf` schema is created and managed exclusively by SF migration scripts (`sovereignflow/infrastructure/migrations/`).
- Domain schema is created and managed exclusively by domain change scripts (`scripts/domain/changes/`).

The application connects with a single connection string. The PostgreSQL user (`sovereignflow`) has full privileges on both schemas but migrations are namespaced so they cannot accidentally cross the boundary.

## Rejected Alternative: Separate Databases

A separate database per domain (e.g. `sovereignflow` for SF, `regulations` for domain) was considered. It was rejected for the following reasons:

- For a single-server deployment with one domain, it adds operational complexity (two connection strings, two backup jobs, two sets of Docker volumes) without meaningful additional isolation.
- Schema-level isolation with proper `GRANT` permissions achieves the same ownership boundary.
- If true isolation becomes necessary (multi-tenant SaaS, compliance requirements, separate backup schedules), migration to separate databases is straightforward — the schemas are already cleanly separated.

## When to Reconsider

Move to separate databases when:
- Multiple independent domain solutions run on the same server and must be isolated from each other.
- A domain solution requires a different PostgreSQL version, extension set, or backup schedule than SF.
- Compliance requires that domain data is physically unreachable from the SF connection string.

## Consequences

**Positive:**
- Single database instance, single backup, single connection string.
- Clear ownership boundary — `sf.*` is the foundation, `<domain>.*` is the business layer.
- Domain migrations cannot reference SF tables without an explicit cross-schema JOIN, which is a visible code smell.
- Adding a second domain solution means adding a second schema — no infrastructure changes required.

**Negative:**
- A compromised application connection string reaches both schemas.
- Requires updating all existing migrations to use explicit schema prefixes (`sf.table_name` or `SET search_path = sf`).
- Schema separation is a convention enforced by discipline, not by connection-level access control.

## Implementation

All SF tables have been migrated to the `sf` schema across migrations 001–008 and all infrastructure repositories. Changes applied:

1. Migration 001 creates `CREATE SCHEMA IF NOT EXISTS sf` and all subsequent migrations use `sf.*` table names.
2. `MigrationRunner` creates the `sf` schema on startup and tracks applied migrations in `sf.schema_migrations` (replacing `public.sovereignflow_schema_migrations`).
3. The `sovereignflow_` prefix was removed from the former `public` schema tables since it was a namespace guard no longer needed inside `sf`.
4. `scripts/domain/changes/001_create_domain_schema.sh` creates the domain schema (configured via `SF_DOMAIN_SCHEMA`).
5. `scripts/sf/changes/002_migrate_to_sf_schema.sh` handles live VPS migration: moves all existing tables to `sf`, renames the policy tables, and recomputes migration checksums.
