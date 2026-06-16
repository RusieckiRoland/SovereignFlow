# `<action_name>` Action

## Purpose

Describe what the action does in one or two paragraphs.

## Domain Neutrality

State whether the action is domain-neutral. If it came from LocalAI-RAG, list
which code-analysis assumptions were removed or generalized.

## YAML Schema

```yaml
- id: example_step
  action: action_name
  action_version: "1.0"
  next: next_step
```

List every supported action-specific field, its type, whether it is required,
and its default behavior.

## State Inputs

List state/context fields read by the action.

## State Outputs

List state/context fields written by the action.

## Routing

Describe whether the action returns a next-step override or relies on `next` /
`routes` from YAML.

## Fail-Fast Validation

List invalid configurations that must fail before or during execution with a
controlled error.

## Security

Describe what the action is not allowed to do, especially around tenant, ACL,
classification, prompt selection, and model transmission.

## Observability

List trace, audit, metrics, and diagnostics emitted by the action.

## Unit Tests

List required unit tests.

## Integration Tests

List required integration tests.

## Examples

Provide minimal and realistic examples.
