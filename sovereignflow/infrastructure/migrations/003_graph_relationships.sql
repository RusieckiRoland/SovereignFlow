CREATE SCHEMA IF NOT EXISTS graph;

CREATE TABLE IF NOT EXISTS graph.relationships (
    tenant_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    owner_source_id TEXT NOT NULL,
    owner_source_version TEXT NOT NULL,
    from_source_id TEXT NOT NULL,
    from_chunk_id TEXT NOT NULL,
    to_source_id TEXT NOT NULL,
    to_chunk_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (
        tenant_id,
        domain,
        owner_source_id,
        owner_source_version,
        from_source_id,
        from_chunk_id,
        to_source_id,
        to_chunk_id,
        relationship_type
    ),
    CONSTRAINT fk_graph_relationships_owner_version
        FOREIGN KEY (
            tenant_id,
            domain,
            owner_source_id,
            owner_source_version
        )
        REFERENCES ingestion.source_versions (
            tenant_id,
            domain,
            source_id,
            source_version
        )
        ON DELETE CASCADE,
    CONSTRAINT fk_graph_relationships_from_chunk
        FOREIGN KEY (
            tenant_id,
            domain,
            owner_source_id,
            owner_source_version,
            from_chunk_id
        )
        REFERENCES ingestion.chunks (
            tenant_id,
            domain,
            source_id,
            source_version,
            chunk_id
        )
        ON DELETE CASCADE,
    CONSTRAINT ck_graph_relationships_owner
        CHECK (owner_source_id = from_source_id),
    CONSTRAINT ck_graph_relationships_type
        CHECK (btrim(relationship_type) <> '')
);

CREATE INDEX IF NOT EXISTS ix_graph_relationships_outgoing
    ON graph.relationships (
        tenant_id,
        domain,
        from_source_id,
        from_chunk_id,
        relationship_type
    );

CREATE INDEX IF NOT EXISTS ix_graph_relationships_incoming
    ON graph.relationships (
        tenant_id,
        domain,
        to_source_id,
        to_chunk_id,
        relationship_type
    );
