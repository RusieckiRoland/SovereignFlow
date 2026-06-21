# resolve_conversation

## Purpose

Resolves the conversation used by a pipeline run.

The action either validates an existing `conversation_id` from the request or creates a new conversation when the pipeline explicitly allows it.

## Version

`1.0`

## Configuration

```yaml
conversation_id_source: request
create_if_missing: true
title_source: query
```

## Rules

- Existing conversations must belong to the authenticated tenant and subject.
- New conversations are created only when `create_if_missing` is `true`.
- The action never reads tenant or subject from the request body.
- The action stores the resolved `conversation_id` in pipeline state.
- Missing storage is a controlled pipeline error.

## Provides

- `conversation`
