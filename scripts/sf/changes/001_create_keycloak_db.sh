#!/usr/bin/env bash
# Creates the keycloak database in PostgreSQL.
# Skipped automatically if the database already exists (fresh install via Docker init).
set -euo pipefail

EXISTS=$(docker exec sovereignflow-postgres-1 psql -U sovereignflow -tAc \
    "SELECT 1 FROM pg_database WHERE datname='keycloak'")

if [ "$EXISTS" = "1" ]; then
    echo "Database keycloak already exists."
    exit 0
fi

docker exec sovereignflow-postgres-1 psql -U sovereignflow <<-EOSQL
    CREATE DATABASE keycloak;
    GRANT ALL PRIVILEGES ON DATABASE keycloak TO sovereignflow;
EOSQL

echo "Database keycloak created."
