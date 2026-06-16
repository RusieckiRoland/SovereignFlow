# `normalize_query` Action

`normalize_query` performs deterministic whitespace normalization of the user
query.

## YAML Contract

```yaml
- id: normalize_query
  action: normalize_query
  action_version: "1.0"
  next: retrieve
```

The action has no action-specific configuration in version `1.0`.

## Behavior

- reads `command.query`;
- collapses repeated whitespace;
- writes `normalized_query`.

The action does not change authorization, tenant, ACL labels, classification,
model routing, or prompt selection.
