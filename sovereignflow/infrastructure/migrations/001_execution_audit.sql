CREATE SCHEMA IF NOT EXISTS sf;

CREATE TABLE IF NOT EXISTS sf.pipeline_runs (
    run_id UUID PRIMARY KEY,
    request_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    pipeline_checksum CHAR(64) NOT NULL,
    status TEXT NOT NULL,
    query_text TEXT NOT NULL,
    answer_text TEXT,
    citation_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    CONSTRAINT ck_pipeline_runs_status
        CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT ck_pipeline_runs_citation_count
        CHECK (citation_count >= 0),
    CONSTRAINT ck_pipeline_runs_completion
        CHECK (
            (status = 'running' AND completed_at IS NULL)
            OR
            (status IN ('succeeded', 'failed') AND completed_at IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS ix_pipeline_runs_tenant_request
    ON sf.pipeline_runs (tenant_id, request_id, started_at DESC);

CREATE INDEX IF NOT EXISTS ix_pipeline_runs_tenant_session
    ON sf.pipeline_runs (tenant_id, session_id, started_at DESC);

CREATE TABLE IF NOT EXISTS sf.pipeline_steps (
    run_id UUID NOT NULL REFERENCES sf.pipeline_runs(run_id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    action_version TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    next_step_id TEXT,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, sequence_number),
    CONSTRAINT ck_pipeline_steps_sequence CHECK (sequence_number > 0),
    CONSTRAINT ck_pipeline_steps_duration CHECK (duration_ms >= 0)
);
