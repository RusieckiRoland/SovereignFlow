# finalize_conversation_turn

## Purpose

Finalizes the active conversation turn with the final answer produced by the pipeline.

## Version

`1.0`

## Configuration

No configuration fields are supported.

## Rules

- Requires a started conversation turn.
- Requires a final answer.
- Stores the final answer after normal answer finalization.
- Does not store system prompts or access tokens.
- Stores only safe operational metadata such as request id, model server id, citation count, and pipeline trace.

## Provides

No new state is required. The existing pipeline `result` state remains available.
