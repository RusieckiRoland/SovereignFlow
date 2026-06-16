# `prefix_router` Action

`prefix_router` selects a named pipeline route based on a prefix at the start of
`last_model_response`.

SovereignFlow routes are engine-level named routes. Therefore this action
returns a route name, and the step-level `routes` mapping resolves that route to
a step id.

## YAML Contract

```yaml
- id: route_prefix
  action: prefix_router
  action_version: "1.0"
  source: last_model_response
  prefixes:
    direct: "[DIRECT:]"
    retrieve: "[RETRIEVE:]"
  on_other: direct
  routes:
    direct: finalize
    retrieve: retrieve
```

## Behavior

- trims the selected source value;
- evaluates prefixes in YAML order;
- on match, stores route name in `last_route`;
- on match, stores route name in `last_prefix`;
- removes the matched prefix from `last_model_response`;
- returns the selected route name;
- returns `on_other` when no prefix matches.

The action never grants access, changes tenant, or bypasses security policy.
