CREATE TABLE IF NOT EXISTS sf.conversations (
    conversation_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    subject_hash CHAR(64) NOT NULL,
    session_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    CONSTRAINT conversations_status_check CHECK (status IN ('active', 'deleted')),
    CONSTRAINT conversations_deleted_at_check CHECK (
        (status = 'active' AND deleted_at IS NULL)
        OR (status = 'deleted' AND deleted_at IS NOT NULL)
    ),
    CONSTRAINT conversations_subject_hash_check CHECK (subject_hash ~ '^[a-f0-9]{64}$')
);

CREATE INDEX IF NOT EXISTS conversations_subject_updated_idx
    ON sf.conversations (tenant_id, subject_hash, updated_at DESC);

CREATE INDEX IF NOT EXISTS conversations_subject_session_idx
    ON sf.conversations (tenant_id, subject_hash, session_id);

CREATE TABLE IF NOT EXISTS sf.conversation_turns (
    turn_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES sf.conversations(conversation_id),
    request_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    answer_text TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finalized_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT conversation_turns_status_check CHECK (
        status IN ('started', 'finalized', 'failed', 'discarded')
    ),
    CONSTRAINT conversation_turns_sequence_check CHECK (sequence_number > 0),
    CONSTRAINT conversation_turns_finalized_check CHECK (
        (status = 'started' AND answer_text IS NULL AND finalized_at IS NULL AND error_code IS NULL)
        OR (status = 'finalized' AND answer_text IS NOT NULL AND finalized_at IS NOT NULL AND error_code IS NULL)
        OR (status = 'failed' AND answer_text IS NULL AND finalized_at IS NOT NULL AND error_code IS NOT NULL)
        OR (status = 'discarded' AND answer_text IS NULL AND finalized_at IS NOT NULL)
    ),
    CONSTRAINT conversation_turns_request_unique UNIQUE (conversation_id, request_id),
    CONSTRAINT conversation_turns_sequence_unique UNIQUE (conversation_id, sequence_number)
);

CREATE INDEX IF NOT EXISTS conversation_turns_conversation_sequence_idx
    ON sf.conversation_turns (conversation_id, sequence_number);

CREATE INDEX IF NOT EXISTS conversation_turns_conversation_status_idx
    ON sf.conversation_turns (conversation_id, status, sequence_number DESC);
