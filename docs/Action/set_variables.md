# `set_variables` Action

`set_variables` performs deterministic state mapping inside a pipeline run.

It is not a decision action and must not modify security context, tenant, ACL
labels, model policy, or authorization.

## YAML Contract

```yaml
- id: prepare_direct_answer
  action: set_variables
  action_version: "1.0"
  rules:
    - set: answer
      from: last_model_response
      transform: copy
  next: finalize
```

## Rules

Each rule requires:

- `set`: allowlisted target field;
- exactly one of `from` or `value`;
- optional `transform`.

Allowed sources:

- `answer`;
- `last_model_response`;
- `normalized_query`;
- `evidence`;
- `context_chunk_ids`;
- `last_route`;
- `last_prefix`;
- `variables`.

Allowed targets:

- `answer`;
- `last_model_response`;
- `normalized_query`;
- `evidence`;
- `variables`.

Allowed transforms:

- `copy`;
- `to_list`;
- `split_lines`;
- `parse_json`;
- `clear`.

Dot-paths are not supported in v1. The action fails fast instead of guessing.
