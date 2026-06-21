# `json_decision_router` Action

`json_decision_router` selects a named route from a strict JSON decision object.

SovereignFlow does not use best-effort parsing or Python literal fallbacks. The model must emit valid JSON.
Invalid JSON follows explicit `on_other` when configured, otherwise execution
fails.

## YAML Contract

```yaml
- id: route_json
  action: json_decision_router
  action_version: "1.0"
  source: last_model_response
  allowed_decisions:
    - direct
    - retrieve
  on_other: direct
  routes:
    direct: finalize
    retrieve: retrieve
```

## Input Shape

```json
{"decision":"retrieve","query":"orders storage"}
```

The decision is resolved from `decision`, `route`, or `mode`.

## Behavior

- accepts only valid JSON objects;
- normalizes decision to lowercase;
- accepts only configured decisions;
- removes routing keys before storing payload back to `last_model_response`;
- stores selected route in `last_route`;
- returns the selected route name.

Routing payload cleanup is deterministic and uses compact sorted JSON.
