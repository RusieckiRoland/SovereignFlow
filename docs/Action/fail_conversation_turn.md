# fail_conversation_turn

## Purpose

Marks the active conversation turn as failed.

## Version

`1.0`

## Configuration

No configuration fields are supported.

## Rules

- Requires a started conversation turn.
- Does not store partial answers.
- Stores a controlled error code.
- Does not store raw technical exceptions or tokens.
- The pipeline engine also marks started turns as failed when an exception occurs after turn creation.

## Provides

No new state is required.
