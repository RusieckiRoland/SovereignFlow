# `repeat_query_guard` Action

`repeat_query_guard` prevents repeated retrieval loops in one pipeline run.

## YAML Contract

```yaml
- id: guard_repeat_query
  action: repeat_query_guard
  action_version: "1.0"
  source: last_model_response
  query_parser: json
  on_ok: new
  on_repeat: repeat
  routes:
    new: retrieve
    repeat: finalize
```

## Behavior

- extracts a query from the configured source;
- normalizes query by trimming, lowercasing, and collapsing whitespace;
- returns `on_repeat` for empty or already seen queries;
- returns `on_ok` and records the normalized query for new queries.

Supported parsers:

- `raw`;
- `json`.

The JSON parser reads the `query` key from a JSON object. Invalid JSON fails
fast. No best-effort parser or hidden fallback is allowed.
