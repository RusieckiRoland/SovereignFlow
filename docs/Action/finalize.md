# `finalize` Action

`finalize` performs neutral response finalization.

## YAML Contract

```yaml
- id: finalize
  action: finalize
  action_version: "1.0"
  end: true
```

The action has no action-specific configuration in version `1.0`.

## Behavior

- appends the domain disclaimer when configured;
- marks the pipeline as producing a result.

It does not change citations, retrieval diagnostics, security policy decisions,
or model output content beyond the configured disclaimer.
