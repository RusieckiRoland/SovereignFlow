# `expand_graph` Action

`expand_graph` expands retrieved seed chunks through neutral graph
relationships.

The action is optional. A pipeline that does not need graph expansion should omit
the step. A pipeline may also include the step with `enabled: false` when it needs
a stable trace shape.

## YAML Contract

```yaml
- id: expand_graph
  action: expand_graph
  action_version: "1.0"
  enabled: true
  max_depth: 2
  max_nodes: 40
  direction: both
  relationship_types: []
  next: manage_context_budget
```

## Fields

| Field | Required | Description |
|---|---:|---|
| `enabled` | yes | Enables or skips graph expansion. |
| `max_depth` | yes | Positive traversal depth. |
| `max_nodes` | yes | Positive maximum returned graph nodes. |
| `direction` | yes | One of `outgoing`, `incoming`, `both`. |
| `relationship_types` | yes | List of allowed relationship types, empty for all allowed by adapter policy. |

## Security

Graph-expanded chunks are checked with the same domain, tenant, ACL, and
classification boundary rules as seed retrieval results.

## Not Allowed

- expanding across tenants;
- using code-specific concepts such as branches, snapshots, or dependency tree
  names;
- silently falling back to domain graph settings when required YAML fields are
  missing.
