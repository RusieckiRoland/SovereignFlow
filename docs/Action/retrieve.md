# `retrieve` Action

`retrieve` performs the initial neutral RAG search and stores seed search hits in
pipeline context.

The action is configured only by YAML step fields. It must not infer retrieval
strategy from hidden defaults when the YAML contract is missing.

## YAML Contract

```yaml
- id: retrieve
  action: retrieve
  action_version: "1.0"
  query_source: normalized_query
  search_mode: hybrid
  top_k: 8
  filters:
    status: active
  next: expand_graph
```

## Fields

| Field | Required | Description |
|---|---:|---|
| `query_source` | yes | Allowlisted source for the retrieval query. |
| `search_mode` | yes | One of `semantic`, `bm25`, `hybrid`. |
| `top_k` | yes | Positive number of seed chunks to request. |
| `filters` | no | Static YAML filters merged with request and domain filters. |

Allowed query sources:

- `normalized_query`;
- `command_query`.

## Security

`retrieve` sends authenticated tenant, ACL labels, and classification ceiling to
the retrieval port.

Returned chunks are checked again after retrieval. A provider crossing tenant,
domain, ACL, or classification boundaries must fail the pipeline.

## Not Allowed

- arbitrary context attribute access;
- user-selected `search_mode`;
- fallback to domain-only retrieval settings when required YAML fields are
  missing;
- domain-specific assumptions.
