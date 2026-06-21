#!/usr/bin/env bash
# Creates the domain schema in PostgreSQL.
# Set SF_DOMAIN_SCHEMA to the name of the domain schema to create.
set -euo pipefail

DOMAIN="${SF_DOMAIN_SCHEMA:?SF_DOMAIN_SCHEMA is required}"

EXISTS=$(docker exec sovereignflow-postgres-1 psql -U sovereignflow -d sovereignflow -tAc \
    "SELECT 1 FROM pg_namespace WHERE nspname='$DOMAIN'")

if [ "$EXISTS" = "1" ]; then
    echo "Schema $DOMAIN already exists."
    exit 0
fi

docker exec sovereignflow-postgres-1 psql -U sovereignflow -d sovereignflow \
    -c "CREATE SCHEMA $DOMAIN;"

echo "Schema $DOMAIN created."
