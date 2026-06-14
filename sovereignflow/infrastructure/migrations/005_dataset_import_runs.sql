CREATE TABLE IF NOT EXISTS ingestion.import_runs (
    import_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    dataset_hash CHAR(64) NOT NULL,
    status TEXT NOT NULL,
    source_count INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL,
    relationship_count INTEGER NOT NULL,
    deletion_count INTEGER NOT NULL,
    indexed_sources INTEGER NOT NULL DEFAULT 0,
    published_relationships INTEGER NOT NULL DEFAULT 0,
    deleted_sources INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_import_runs_status
        CHECK (status IN ('staging', 'relating', 'deleting', 'completed', 'failed')),
    CONSTRAINT ck_import_runs_counts
        CHECK (
            source_count > 0
            AND chunk_count > 0
            AND relationship_count >= 0
            AND deletion_count >= 0
            AND indexed_sources >= 0
            AND published_relationships >= 0
            AND deleted_sources >= 0
        )
);

CREATE INDEX IF NOT EXISTS ix_import_runs_tenant_updated
    ON ingestion.import_runs (tenant_id, updated_at DESC);
