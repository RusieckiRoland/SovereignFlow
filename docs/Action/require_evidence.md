# `require_evidence` Action

`require_evidence` fails the pipeline when no evidence was selected for the
answer.

## YAML Contract

```yaml
- id: require_evidence
  action: require_evidence
  action_version: "1.0"
  next: enforce_model_transmission_policy
```

The action has no action-specific configuration in version `1.0`.

## Behavior

- requires non-empty evidence;
- requires at least one citation;
- fails before model invocation when evidence is missing.

It does not perform retrieval and does not ask the model whether evidence is
sufficient.
