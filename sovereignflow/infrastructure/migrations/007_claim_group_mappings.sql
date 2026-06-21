CREATE TABLE sf.claim_group_mappings (
    tenant_id TEXT NOT NULL,
    claim_name TEXT NOT NULL CHECK (claim_name IN ('groups', 'roles')),
    claim_value TEXT NOT NULL,
    group_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, claim_name, claim_value, group_id),
    FOREIGN KEY (tenant_id, group_id)
        REFERENCES sf.security_groups(tenant_id, group_id)
        ON DELETE CASCADE
);

CREATE TABLE sf.policy_changes (
    change_id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    previous_version BIGINT,
    published_version BIGINT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX sf_claim_group_mappings_lookup_idx
    ON sf.claim_group_mappings (tenant_id, claim_name, claim_value);
