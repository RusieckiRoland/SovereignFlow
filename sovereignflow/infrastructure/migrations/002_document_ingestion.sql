CREATE SCHEMA IF NOT EXISTS ingestion;

CREATE TABLE IF NOT EXISTS ingestion.source_versions (
    tenant_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    source_uri TEXT,
    payload_hash CHAR(64) NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, domain, source_id, source_version)
);

CREATE TABLE IF NOT EXISTS ingestion.chunks (
    tenant_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    source_uri TEXT,
    text_content TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    acl_labels TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    clearance_label TEXT,
    classification_labels TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    PRIMARY KEY (tenant_id, domain, source_id, source_version, chunk_id),
    CONSTRAINT fk_ingestion_chunks_source_version
        FOREIGN KEY (tenant_id, domain, source_id, source_version)
        REFERENCES ingestion.source_versions (
            tenant_id, domain, source_id, source_version
        )
        ON DELETE CASCADE,
    CONSTRAINT ck_ingestion_chunks_position CHECK (position >= 0),
    CONSTRAINT ck_ingestion_chunks_text CHECK (btrim(text_content) <> '')
);

CREATE INDEX IF NOT EXISTS ix_ingestion_chunks_source_order
    ON ingestion.chunks (
        tenant_id, domain, source_id, source_version, position
    );

CREATE TABLE IF NOT EXISTS ingestion.jobs (
    job_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_hash CHAR(64) NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT fk_ingestion_jobs_source_version
        FOREIGN KEY (tenant_id, domain, source_id, source_version)
        REFERENCES ingestion.source_versions (
            tenant_id, domain, source_id, source_version
        )
        ON DELETE RESTRICT,
    CONSTRAINT uq_ingestion_jobs_idempotency
        UNIQUE (tenant_id, domain, idempotency_key),
    CONSTRAINT ck_ingestion_jobs_status
        CHECK (status IN ('staged', 'indexing', 'indexed', 'failed')),
    CONSTRAINT ck_ingestion_jobs_attempts CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_pending
    ON ingestion.jobs (status, updated_at)
    WHERE status IN ('staged', 'failed');

CREATE TABLE IF NOT EXISTS ingestion.sources (
    tenant_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    source_id TEXT NOT NULL,
    current_version TEXT NOT NULL,
    current_job_id UUID NOT NULL REFERENCES ingestion.jobs(job_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, domain, source_id),
    CONSTRAINT fk_ingestion_sources_current_version
        FOREIGN KEY (tenant_id, domain, source_id, current_version)
        REFERENCES ingestion.source_versions (
            tenant_id, domain, source_id, source_version
        )
        ON DELETE RESTRICT
);
