#!/usr/bin/env bash
# Migrates all SovereignFlow tables from their original schemas
# (execution, ingestion, graph, conversation, public) into the unified sf schema.
# Skipped automatically if the migration has already been applied.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGRATIONS_DIR="$SCRIPT_DIR/../../../sovereignflow/infrastructure/migrations"

EXISTS=$(docker exec sovereignflow-postgres-1 psql -U sovereignflow -d sovereignflow -tAc \
    "SELECT 1 FROM pg_namespace WHERE nspname='execution'")

if [ "$EXISTS" != "1" ]; then
    echo "Schema execution not found — already migrated to sf schema."
    exit 0
fi

echo "Migrating tables to sf schema..."

docker exec sovereignflow-postgres-1 psql -U sovereignflow -d sovereignflow <<-'EOSQL'
    CREATE SCHEMA IF NOT EXISTS sf;

    -- execution schema
    ALTER TABLE execution.pipeline_runs SET SCHEMA sf;
    ALTER TABLE execution.pipeline_steps SET SCHEMA sf;
    DROP SCHEMA execution;

    -- ingestion schema (order: source_versions first, then dependents)
    ALTER TABLE ingestion.source_versions SET SCHEMA sf;
    ALTER TABLE ingestion.chunks SET SCHEMA sf;
    ALTER TABLE ingestion.jobs SET SCHEMA sf;
    ALTER TABLE ingestion.sources SET SCHEMA sf;
    ALTER TABLE ingestion.import_runs SET SCHEMA sf;
    DROP SCHEMA ingestion;

    -- graph schema
    ALTER TABLE graph.relationships SET SCHEMA sf;
    DROP SCHEMA graph;

    -- conversation schema
    ALTER TABLE conversation.conversations SET SCHEMA sf;
    ALTER TABLE conversation.conversation_turns SET SCHEMA sf;
    DROP SCHEMA conversation;

    -- public policy tables: move and drop sovereignflow_ prefix
    ALTER TABLE public.sovereignflow_policy_versions SET SCHEMA sf;
    ALTER TABLE sf.sovereignflow_policy_versions RENAME TO policy_versions;

    ALTER TABLE public.sovereignflow_security_groups SET SCHEMA sf;
    ALTER TABLE sf.sovereignflow_security_groups RENAME TO security_groups;

    ALTER TABLE public.sovereignflow_capabilities SET SCHEMA sf;
    ALTER TABLE sf.sovereignflow_capabilities RENAME TO capabilities;

    ALTER TABLE public.sovereignflow_group_capabilities SET SCHEMA sf;
    ALTER TABLE sf.sovereignflow_group_capabilities RENAME TO group_capabilities;

    ALTER TABLE public.sovereignflow_security_decisions SET SCHEMA sf;
    ALTER TABLE sf.sovereignflow_security_decisions RENAME TO security_decisions;

    ALTER TABLE public.sovereignflow_claim_group_mappings SET SCHEMA sf;
    ALTER TABLE sf.sovereignflow_claim_group_mappings RENAME TO claim_group_mappings;

    ALTER TABLE public.sovereignflow_policy_changes SET SCHEMA sf;
    ALTER TABLE sf.sovereignflow_policy_changes RENAME TO policy_changes;

    -- Create sf.schema_migrations to replace public.sovereignflow_schema_migrations
    CREATE TABLE IF NOT EXISTS sf.schema_migrations (
        version TEXT PRIMARY KEY,
        checksum TEXT NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    DROP TABLE IF EXISTS public.sovereignflow_schema_migrations;
EOSQL

echo "Table migration complete. Updating migration checksums..."

for migration in "$MIGRATIONS_DIR"/[0-9]*.sql; do
    name=$(basename "$migration")
    checksum=$(sha256sum "$migration" | cut -d' ' -f1)
    docker exec sovereignflow-postgres-1 psql -U sovereignflow -d sovereignflow -c \
        "INSERT INTO sf.schema_migrations (version, checksum) VALUES ('$name', '$checksum') ON CONFLICT (version) DO UPDATE SET checksum = EXCLUDED.checksum;"
    echo "  $name → $checksum"
done

echo "Migration to sf schema complete."
