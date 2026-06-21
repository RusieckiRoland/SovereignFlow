# load_conversation_history

## Purpose

Loads previous finalized conversation turns as optional conversation context.

## Version

`1.0`

## Configuration

```yaml
limit: 10
max_characters: 4000
include_failed: false
format: dialog
```

## Rules

- Loads only `finalized` turns.
- Failed and discarded turns are never loaded.
- The currently started turn is not loaded.
- The action applies both turn count and character budget limits.
- Empty history is valid and produces an empty history string.
- Storage errors are controlled pipeline errors.
- History is not evidence and must not be used as a citation source.

## Provides

- `conversation_history`
