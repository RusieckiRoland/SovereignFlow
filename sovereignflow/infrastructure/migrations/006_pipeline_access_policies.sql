CREATE TABLE sf.policy_versions (
    tenant_id TEXT PRIMARY KEY,
    version BIGINT NOT NULL CHECK (version > 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE sf.security_groups (
    tenant_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant_id, group_id),
    FOREIGN KEY (tenant_id)
        REFERENCES sf.policy_versions(tenant_id)
        ON DELETE CASCADE
);

CREATE TABLE sf.capabilities (
    tenant_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    diagnostics_available BOOLEAN NOT NULL DEFAULT FALSE,
    external_model BOOLEAN NOT NULL DEFAULT FALSE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant_id, capability_id),
    FOREIGN KEY (tenant_id)
        REFERENCES sf.policy_versions(tenant_id)
        ON DELETE CASCADE
);

CREATE TABLE sf.group_capabilities (
    tenant_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, group_id, capability_id),
    FOREIGN KEY (tenant_id, group_id)
        REFERENCES sf.security_groups(tenant_id, group_id)
        ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, capability_id)
        REFERENCES sf.capabilities(tenant_id, capability_id)
        ON DELETE CASCADE
);

CREATE TABLE sf.security_decisions (
    decision_id BIGSERIAL PRIMARY KEY,
    request_id TEXT NOT NULL,
    subject_hash TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    pipeline_name TEXT,
    allowed BOOLEAN NOT NULL,
    reason_code TEXT NOT NULL,
    policy_version BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX sf_security_decisions_request_idx
    ON sf.security_decisions (request_id);
