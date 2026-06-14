ALTER TABLE execution.pipeline_runs
    ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS completion_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(20, 10) NOT NULL DEFAULT 0;

ALTER TABLE execution.pipeline_runs
    DROP CONSTRAINT IF EXISTS ck_pipeline_runs_prompt_tokens,
    ADD CONSTRAINT ck_pipeline_runs_prompt_tokens CHECK (prompt_tokens >= 0),
    DROP CONSTRAINT IF EXISTS ck_pipeline_runs_completion_tokens,
    ADD CONSTRAINT ck_pipeline_runs_completion_tokens CHECK (completion_tokens >= 0),
    DROP CONSTRAINT IF EXISTS ck_pipeline_runs_estimated_cost,
    ADD CONSTRAINT ck_pipeline_runs_estimated_cost CHECK (estimated_cost >= 0);

CREATE INDEX IF NOT EXISTS ix_pipeline_runs_tenant_started
    ON execution.pipeline_runs (tenant_id, started_at DESC);
