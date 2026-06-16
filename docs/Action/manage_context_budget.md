# `manage_context_budget` Action

`manage_context_budget` materializes retrieved chunks into evidence text,
citations, selected context chunk IDs, and omitted chunk IDs.

This action makes context construction explicit in the pipeline YAML instead of
burying the context budget in retrieval or model invocation.

## YAML Contract

```yaml
- id: manage_context_budget
  action: manage_context_budget
  action_version: "1.0"
  source: hits
  target: evidence
  max_context_characters: 24000
  next: call_model
```

## Fields

| Field | Required | Description |
|---|---:|---|
| `source` | yes | Must be `hits` in the current implementation. |
| `target` | yes | Must be `evidence` in the current implementation. |
| `max_context_characters` | yes | Positive character budget. |

## Behavior

The action:

- preserves deterministic hit order;
- emits citations for included chunks;
- truncates the evidence text at the configured character budget;
- records `context_chunk_ids`;
- records `omitted_chunk_ids` when later chunks are excluded.

## Not Allowed

- implicit fallback to domain context budget;
- asking the model to decide which chunks are safe;
- hidden domain-specific formatting;
- unbounded prompt construction.
