# start_conversation_turn

## Purpose

Starts a conversation turn for the current request.

## Version

`1.0`

## Configuration

No configuration fields are supported.

## Rules

- Requires a resolved conversation.
- Stores the authenticated user's question.
- Binds the turn to the current `request_id`.
- The repository is responsible for idempotency for the same `(conversation_id, request_id)`.
- The action stores `turn_id` in pipeline state.

## Provides

- `conversation_turn`
