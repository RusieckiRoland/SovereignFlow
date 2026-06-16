# `loop_guard` Action

`loop_guard` prevents infinite loops in one pipeline run.

## YAML Contract

```yaml
- id: guard_loop
  action: loop_guard
  action_version: "1.0"
  max_loops: 3
  on_allow: continue
  on_deny: stop
  routes:
    continue: route_json
    stop: finalize
```

## Behavior

- increments a per-step counter in `loop_counters`;
- returns `on_allow` while the counter is below or equal to `max_loops`;
- returns `on_deny` after the limit is exceeded.

The action changes only loop counters and `last_route`.
