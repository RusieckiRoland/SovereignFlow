# SovereignFlow operations API

The operations API exposes tenant-scoped execution evidence, aggregate metrics, and explicit ingestion-job administration.

## Authentication

Set `admin.api_key_env` in the runtime configuration and provide the referenced environment variable before startup.

Every operations request must include:

```text
X-SovereignFlow-Admin-Key: <configured secret>
```

Every endpoint also requires a non-empty `tenant_id` query parameter. The API key authenticates the administrative client; the tenant parameter constrains every read and retry operation.

## Endpoints

### Execution details

```text
GET /v1/admin/executions/{request_id}?tenant_id={tenant_id}
```

Returns the latest matching pipeline run, its completed steps, citations count, provider-reported token usage, and configured cost estimate. A missing execution is represented explicitly as `null`.

### Aggregate metrics

```text
GET /v1/admin/metrics?tenant_id={tenant_id}&hours={1..744}
```

The default window is 24 hours. Metrics are derived from PostgreSQL execution records and include success rate, latency, evidence counts, token usage, cost, and action-level duration.

### Ingestion job

```text
GET /v1/admin/ingestion/jobs/{job_id}?tenant_id={tenant_id}
```

Returns safe job metadata without exposing document content.

### Retry ingestion

```text
POST /v1/admin/ingestion/jobs/{job_id}/retry?tenant_id={tenant_id}
```

Retries the durable job through the configured domain ingestion service. There is no automatic retry or hidden fallback.

## Error contract

Known failures use the standard response:

```json
{
  "ok": false,
  "error": {
    "code": "authentication_error",
    "message": "Administrative authentication failed",
    "request_id": "generated-or-supplied-request-id"
  }
}
```

Authentication failures return HTTP `401`. Validation failures return HTTP `400`. Dependency failures return HTTP `503`.
