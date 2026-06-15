# Access policy administration

SovereignFlow authorizes neutral RAG capabilities through versioned PostgreSQL
policies. Identity Provider claims identify a user but do not directly grant a
pipeline.

## Runtime resolution

```text
signed access token
-> tenant and configured claims
-> exact claim mappings
-> internal security groups
-> capability grants
-> configured domain and exact pipeline
```

No policy, no mapping, an empty grant set, or a policy-repository failure denies
the request before retrieval.

## Publish a policy

The administrative endpoint replaces one tenant policy atomically:

```text
PUT /v1/admin/access-policies/{tenant_id}
X-SovereignFlow-Admin-Key: ...
```

Example body:

```json
{
  "expected_version": 1,
  "version": 2,
  "groups": ["knowledge-readers"],
  "claim_mappings": [
    {
      "claim_name": "groups",
      "claim_value": "identity-knowledge-readers",
      "group_id": "knowledge-readers"
    }
  ],
  "capabilities": [
    {
      "capability_id": "general-query",
      "display_name": "General knowledge",
      "description": "Query the general neutral RAG domain.",
      "domain": "general",
      "pipeline_name": "default",
      "diagnostics_available": true,
      "external_model": false
    }
  ],
  "grants": [
    {
      "group_id": "knowledge-readers",
      "capability_id": "general-query"
    }
  ]
}
```

`expected_version` provides optimistic concurrency control. A mismatch returns a
controlled conflict and publishes nothing. Every referenced domain and pipeline
must exist in the running configuration.

One domain may expose multiple explicitly configured pipelines through
`allowed_pipeline_names`. SovereignFlow includes three neutral examples:

- `direct` performs retrieval, context construction, and generation without graph expansion;
- `graph` adds graph expansion after the initial retrieval;
- `strict` adds graph expansion and refuses generation when no evidence exists.

Identity groups can receive grants to different pipeline capabilities while all
of those capabilities continue to use the same domain and data model.

## User catalog and query

```text
GET /v1/catalog
Authorization: Bearer ...
```

The response contains only capabilities granted by the current policy.

```text
POST /v1/query
Authorization: Bearer ...
```

```json
{
  "capability_id": "general-query",
  "query": "What evidence is available?",
  "session_id": "session-1",
  "filters": {}
}
```

The public request cannot provide `domain`, `pipeline_id`, `pipeline_name`,
tenant, groups, ACL labels, classification, or model permissions.

## Audit

Every allow or deny decision is stored in
`public.sovereignflow_security_decisions` with:

- request ID;
- SHA-256 subject hash;
- tenant;
- requested capability;
- resolved pipeline when known;
- decision and stable reason code;
- policy version;
- timestamp.

Tokens, raw claims, prompts, and query text are not stored in this audit.
