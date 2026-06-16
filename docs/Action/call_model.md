# `call_model` Action

## Purpose

`call_model` invokes the configured model gateway and stores the model output in
the pipeline context.

The action is responsible for:

- loading the system prompt selected by the pipeline step;
- assembling the user message from YAML-defined `user_parts`;
- passing optional generation parameters to the model gateway;
- recording safe prompt/model diagnostics;
- writing the generated answer into pipeline state.

`call_model` must not decide retrieval access, tenant access, ACL access,
classification access, or model-transmission policy. Those checks must happen
before the model call through hard backend rules.

## Domain Neutrality

This action is domain-neutral.

It may be used by TaricAI, technical documentation assistants, medical-domain
assistants, or any other SovereignFlow solution because it does not know what the
evidence means. It only formats already-authorized context into a model request.

Domain-specific behavior belongs in prompt files and pipeline configuration, not
inside Python code.

## LocalAI-RAG Source Contract

The contract follows the useful parts of LocalAI-RAG:

- `prompt_key` points to a prompt file;
- `user_parts` defines how user content is assembled;
- each `user_parts.*.source` reads a state/context field;
- each `user_parts.*.template` wraps that value and must contain `{}`;
- step-local generation parameters are optional overrides;
- the system prompt is controlled by application-owned files, never by user input.

## YAML Schema

```yaml
- id: call_model_answer
  action: call_model
  action_version: "1.0"
  prompt_key: "general/answer"
  user_parts:
    user_question:
      source: normalized_query
      template: "### User question:\n{}\n\n"
    evidence:
      source: evidence
      template: "### Evidence:\n{}\n\n"
  max_output_tokens: 700
  temperature: 0.0
  next: finalize
```

### Required fields

| Field | Type | Meaning |
|---|---|---|
| `prompt_key` | string | Relative prompt key. Resolves to `<prompts_root>/<prompt_key>.txt`. |
| `user_parts` | mapping | Ordered mapping of user-message parts. |
| `user_parts.<name>.source` | string | Allowlisted context source. |
| `user_parts.<name>.template` | string | Format string containing `{}`. |

### Optional fields

| Field | Type | Meaning |
|---|---|---|
| `max_tokens` | integer | Model output token limit. |
| `max_output_tokens` | integer | Alias for model output token limit. |
| `temperature` | number | Sampling temperature. |
| `top_p` | number | Nucleus sampling value. |
| `server_name` / `model_profile` | string | Future explicit model route, if supported by policy. |
| `use_history` | boolean | Future option to include conversation history from an explicit source. |

If an optional model parameter is omitted, `call_model` must not override the
model gateway default.

## Prompt Resolution

`prompt_key` resolves under the configured prompt repository root:

```text
<prompts_root>/<prompt_key>.txt
```

Rules:

- `prompt_key` is required;
- `prompt_key` may contain subfolders;
- path traversal is forbidden;
- missing prompt file fails fast;
- empty prompt file fails fast;
- user input must never select or modify `prompt_key`.

## User Parts Assembly

`user_parts` is evaluated in YAML order.

Each part:

1. reads an allowlisted source from pipeline context;
2. converts the value to safe text;
3. applies `template.format(text)`;
4. appends the result to the final user message.

Example:

```yaml
user_parts:
  evidence:
    source: evidence
    template: "### Evidence:\n{}\n\n"
  user_question:
    source: normalized_query
    template: "### User:\n{}\n\n"
```

The resulting user message is the concatenation of both rendered parts.

## Allowed Sources

Initial SovereignFlow sources should be explicit and small:

| Source | Meaning |
|---|---|
| `normalized_query` | Normalized user query. |
| `evidence` | Context assembled from retrieved chunks. |
| `context_chunk_ids` | IDs of chunks included in context. |
| `citations_text` | Safe citation summary. |
| `retrieval_trace_summary` | Safe retrieval trace summary, if diagnostics allow it. |

Do not allow arbitrary `getattr`, private attributes, tenant/security fields, or
raw authorization objects as `user_parts` sources.

## State Inputs

- `normalized_query`
- `evidence`
- `citations`
- selected safe source fields from the allowlist
- prompt repository
- model gateway
- current authorization after previous security checks

## State Outputs

- `answer`
- `prompt_tokens`
- `completion_tokens`
- `estimated_cost`
- `model_duration_ms`
- `system_prompt_hash`
- optional safe diagnostics: `prompt_key`, selected model, rendered part names

## Routing

`call_model` normally relies on `next` from YAML.

It should not return a dynamic next-step override unless a future documented
model-routing contract explicitly requires it. Routing based on model output
belongs to dedicated actions such as `prefix_router` or `json_decision_router`.

## Fail-Fast Validation

The action must fail with a controlled error when:

- `prompt_key` is missing or empty;
- prompt path escapes the prompt root;
- prompt file does not exist;
- prompt file is empty;
- `user_parts` is missing or empty;
- a `user_parts` key is empty;
- a `user_parts` item is not a mapping;
- `source` is missing, empty, or not allowlisted;
- `template` is missing or does not contain `{}`;
- both `max_tokens` and `max_output_tokens` are provided with conflicting values;
- numeric generation parameters have invalid types or ranges.

## Security

`call_model` must enforce prompt hygiene:

- system prompt comes only from `prompt_key` and prompt files;
- user input can appear only inside `user_parts` values;
- user input cannot choose prompt file, model profile, tenant, ACL, or policy;
- external-model transmission policy must already have allowed this call;
- diagnostics must not leak restricted context unless explicitly permitted.

`call_model` must not ask the model whether data is allowed. That decision is a
backend policy decision.

## Observability

The action should emit safe diagnostics:

- `prompt_key`;
- `system_prompt_hash`;
- selected model provider and model id;
- generated token counts;
- model duration;
- names of rendered `user_parts`;
- character count of user message;
- request correlation ID.

Full prompt logging must remain behind an explicit diagnostic policy and must not
bypass ACL/classification/model-transmission rules.

## Unit Tests

Required unit tests:

1. loads prompt by `prompt_key`;
2. rejects missing `prompt_key`;
3. rejects path traversal;
4. rejects empty prompt file;
5. assembles `user_parts` in YAML order;
6. rejects unknown source;
7. rejects template without `{}`;
8. passes only configured generation parameters;
9. rejects conflicting token aliases;
10. records prompt hash and token usage;
11. does not call model when validation fails;
12. does not allow user input to affect `prompt_key`.

## Integration Tests

Required integration tests:

1. controlled HTTP model receives the YAML-assembled user message;
2. prompt file from `prompts_root` is used as the system prompt;
3. three pipeline variants can use different `prompt_key` values;
4. diagnostics expose `prompt_key` and hash but not forbidden content;
5. external model is not called if a prior transmission policy blocks execution.

## Examples

### Minimal RAG answer

```yaml
- id: call_model
  action: call_model
  action_version: "1.0"
  prompt_key: "general/answer"
  user_parts:
    user_question:
      source: normalized_query
      template: "USER QUESTION\n{}\n\n"
    evidence:
      source: evidence
      template: "EVIDENCE\n{}\n\n"
  next: finalize
```

### Strict answer with explicit token limit

```yaml
- id: call_model
  action: call_model
  action_version: "1.0"
  prompt_key: "general/strict_answer"
  user_parts:
    evidence:
      source: evidence
      template: "### Evidence\n{}\n\n"
    user_question:
      source: normalized_query
      template: "### Question\n{}\n\n"
  max_output_tokens: 500
  temperature: 0.0
  next: finalize
```
