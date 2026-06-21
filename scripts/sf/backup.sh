#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/var/backups/sovereignflow"
RETENTION_DAYS=7
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

source /etc/sovereignflow/.env

pg_dump "$SF_POSTGRES_URL" | gzip > "$BACKUP_DIR/postgres_${TIMESTAMP}.sql.gz"

find "$BACKUP_DIR" -name "postgres_*.sql.gz" -mtime +${RETENTION_DAYS} -delete

echo "Backup completed: $BACKUP_DIR/postgres_${TIMESTAMP}.sql.gz"
